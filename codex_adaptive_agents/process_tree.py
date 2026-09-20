"""Identity-checked POSIX cleanup; not a sandbox for hostile executables.

Linux uses a private subreaper so detached orphans remain owned by the command.
macOS remembers original parent identities, which survive setsid and reparenting.
A macOS intermediary that forks and exits between snapshots can still hide its
lineage; do not present this fallback as kernel-enforced process containment.
"""
from __future__ import annotations

import ctypes as c
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _linux_identity(pid):
    try:
        # Linux comm is arbitrary bytes, not necessarily UTF-8.
        fields = (Path('/proc') / str(pid) / 'stat').read_bytes().rsplit(b')', 1)[1].split()
        return int(fields[19]), int(fields[1])
    except (OSError, ValueError, IndexError):
        return None


def _linux_snapshot():
    result = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        value = _linux_identity(int(entry.name))
        if value is not None:
            result[int(entry.name)] = value
    return result


_linux_snapshot.identity = _linux_identity


class _DarwinSnapshot:
    class Unique(c.Structure):
        _fields_ = [('uuid', c.c_uint8 * 16), ('identity', c.c_uint64),
                    ('parent', c.c_uint64), ('version', c.c_int32),
                    ('original_parent_version', c.c_int32),
                    ('reserved', c.c_uint64 * 2)]

    def __init__(self):
        self.lib = c.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        self.lib.proc_listallpids.argtypes = [c.c_void_p, c.c_int]
        self.lib.proc_listallpids.restype = c.c_int
        self.lib.proc_pidinfo.argtypes = [c.c_int, c.c_int, c.c_uint64,
                                          c.c_void_p, c.c_int]
        self.lib.proc_pidinfo.restype = c.c_int
        own, parent = self.identity(os.getpid()), self.identity(os.getppid())
        # Older Darwin layouts reserve offset 36. Never interpret it as a
        # parent version unless the live parent/child pair confirms support.
        self.original_versions = bool(own and parent and own[3] > 0 and own[3] == parent[2])

    def identity(self, pid, *, zombies=True):
        data = self.Unique()
        if self.lib.proc_pidinfo(pid, 17, int(zombies), c.byref(data), c.sizeof(data)) != c.sizeof(data):
            return None
        return data.identity, data.parent, data.version, data.original_parent_version

    def __call__(self):
        count = self.lib.proc_listallpids(None, 0)
        if count <= 0:
            raise RuntimeError('process enumeration unavailable')
        pids = (c.c_int * (count + 256))()
        count = self.lib.proc_listallpids(pids, c.sizeof(pids))
        if count <= 0 or count >= len(pids):
            raise RuntimeError('process enumeration incomplete')
        result = {}
        for pid in pids[:count]:
            if pid <= 0:
                continue
            value = self.identity(pid)
            if value:
                result[pid] = value
        return result


class Tracker:
    def __init__(self, pid, snapshot, *, original_parent=False, original_versions=False, own_group=False):
        self.pid, self.snapshot, self.original_parent = pid, snapshot, original_parent
        self.original_versions, self.own_group = original_versions, own_group
        self.known = {}
        self.ancestors = set()
        self.versions = set()
        current = snapshot()
        if pid not in current:
            raise RuntimeError('command process identity unavailable')
        self.known[pid] = current[pid][0]
        self.ancestors.add(current[pid][0])
        if original_versions:
            self.versions.add(current[pid][2])

    def scan(self):
        current = self.snapshot()
        if self.original_versions:
            for pid, identity in self.known.items():
                if current.get(pid, (None,))[0] == identity:
                    self.versions.add(current[pid][2])
        # Retain original identities after exit. Unlike a PID, they cannot be
        # reused to accidentally adopt a different command's descendants.
        parents = set(self.ancestors) if self.original_parent else {
            pid for pid, identity in self.known.items()
            if current.get(pid, (None,))[0] == identity}
        changed = True
        while changed:
            changed = False
            for pid, value in current.items():
                if pid <= 0:
                    continue
                identity, parent = value[:2]
                belongs = parent in parents or (self.original_versions and value[3] in self.versions)
                if not belongs and self.own_group:
                    try:
                        belongs = os.getpgid(pid) == self.pid
                    except (ProcessLookupError, PermissionError):
                        pass
                if belongs and self.known.get(pid) != identity:
                    self.known[pid] = identity
                    self.ancestors.add(identity)
                    parents.add(identity if self.original_parent else pid)
                    if self.original_versions:
                        self.versions.add(value[2])
                    changed = True
        return {pid: identity for pid, identity in self.known.items()
                if current.get(pid, (None,))[0] == identity}

    def cleanup(self, *, exclude_root=False):
        # Freeze before killing. Scan again after freezing so children created
        # during enumeration become visible before their parent is removed.
        frozen = set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            alive = self.scan()
            fresh = set(alive) - frozen
            if exclude_root:
                fresh.discard(self.pid)
            if not fresh:
                break
            for pid in fresh:
                self._signal(pid, alive[pid], signal.SIGSTOP)
            frozen.update(fresh)
        else:
            raise RuntimeError('process tree cleanup exceeded limit')
        for pid, identity in alive.items():
            if pid != self.pid:
                self._signal(pid, identity, signal.SIGKILL)
        if not exclude_root and self.pid in alive:
            self._signal(self.pid, alive[self.pid], signal.SIGKILL)
        if isinstance(self.snapshot, _DarwinSnapshot):
            # Orphans are reaped by launchd, so waitpid cannot wait for them.
            # Excluding zombies observes that they can no longer execute.
            pending = {pid: identity for pid, identity in alive.items()
                       if not (exclude_root and pid == self.pid)}
            deadline = time.monotonic() + 2
            while pending:
                pending = {pid: identity for pid, identity in pending.items()
                           if (self.snapshot.identity(pid, zombies=False) or (None,))[0] == identity}
                if pending:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('descendant termination was not confirmed')
                    time.sleep(0.01)

    def _signal(self, pid, identity, sig):
        # Revalidate immediately before signalling; never act on a stale PID.
        if pid <= 0:
            raise RuntimeError('refusing a process-group signal as a process id')
        current = self.snapshot.identity(pid) if hasattr(self.snapshot, 'identity') else self.snapshot().get(pid)
        if not current or current[0] != identity:
            return
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass

    def abort(self):
        """Best-effort cleanup after an error; never authorizes a success ack."""
        deadline = time.monotonic() + 2
        while True:
            if sys.platform == 'linux':
                # The dedicated subreaper receives orphaned descendants even
                # if a full process-table snapshot failed.
                try:
                    children = Path(f'/proc/self/task/{self.pid}/children').read_bytes().split()
                    for raw in children:
                        pid = int(raw)
                        identity = _linux_identity(pid)
                        if identity is not None:
                            self.known[pid] = identity[0]
                except (OSError, ValueError):
                    pass
            found = False
            for pid, identity in list(self.known.items()):
                if pid == self.pid:
                    continue
                try:
                    self._signal(pid, identity, signal.SIGKILL)
                    found = True
                except OSError:
                    pass
            if not found or time.monotonic() >= deadline:
                return
            time.sleep(0.01)


def launch(argv, options):
    read_fd, write_fd = os.pipe()
    result_read, result_write = os.pipe()
    proc = None
    try:
        if sys.platform in ('linux', 'darwin'):
            command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                       str(read_fd), str(result_write), *argv]
        else:
            raise RuntimeError('POSIX process cleanup is unsupported on this platform')
        proc = subprocess.Popen(command, pass_fds=(read_fd, result_write), **options)
        os.close(read_fd)
        read_fd = None
        os.close(result_write)
        result_write = None
        # EOF requests cleanup, including if the controlling process dies.
        proc._native_control = write_fd
        proc._native_cleanup_result = result_read
        write_fd = None
        result_read = None
        return proc
    except BaseException:
        if proc is not None:
            proc.kill()
            proc.wait()
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                pipe.close()
        raise
    finally:
        for fd in (read_fd, write_fd, result_read, result_write):
            if fd is not None:
                os.close(fd)


def _supervise(control, result_fd, argv):
    # This flag is process-local, in a dedicated helper. It cannot adopt or
    # reap subprocesses belonging to other calls in the Python host.
    if sys.platform == 'linux':
        libc = c.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise RuntimeError('Linux child subreaper unavailable')
    os.set_blocking(control, False)
    snapshot = _DarwinSnapshot() if sys.platform == 'darwin' else _linux_snapshot
    tracker = Tracker(os.getpid(), snapshot, original_parent=sys.platform == 'darwin',
                      original_versions=getattr(snapshot, 'original_versions', False),
                      own_group=sys.platform == 'darwin')
    child = subprocess.Popen(argv, close_fds=True)
    result = 125
    try:
        while True:
            # Preserve final birth/exec identifiers before poll reaps a child.
            tracker.scan()
            result = child.poll()
            if result is not None:
                break
            try:
                if os.read(control, 1) == b'':
                    result = 124
                    break
            except BlockingIOError:
                pass
            time.sleep(0.01)
    finally:
        try:
            tracker.cleanup(exclude_root=True)
        except Exception:
            tracker.abort()
            raise
        finally:
            # Even enumeration/cleanup failures must not skip direct-child
            # termination. Unreaped Popen ownership protects against PID reuse.
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            os.close(control)
        # SIGKILL completion is asynchronous. Reap every adopted child before
        # reporting completion to the caller.
        deadline = time.monotonic() + 2
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('adopted child did not stop')
                    time.sleep(0.01)
            except ChildProcessError:
                break
    os.write(result_fd, b'1')
    os.close(result_fd)
    return result


if __name__ == '__main__':
    try:
        result = _supervise(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3:])
        if result < 0:
            # Preserve Popen's negative signal result. Mapping it to a normal
            # exit (128 + signal) would let invalid-input checks accept crashes.
            if -result not in (signal.SIGKILL, signal.SIGSTOP):
                signal.signal(-result, signal.SIG_DFL)
            os.kill(os.getpid(), -result)
        raise SystemExit(result)
    except Exception:
        print('POSIX command supervision failed', file=sys.stderr)
        raise SystemExit(125)
