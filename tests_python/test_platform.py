import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_adaptive_agents import platform as p


class PlatformTests(unittest.TestCase):
    def test_json_rejects_ambiguous_input(self):
        for value in ('{"a":1,"a":2}', '{"a":NaN}', '[Infinity]'):
            with self.assertRaises(ValueError):
                p.loads(value)

    def test_unicode_atomic_and_link_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'space 中文' / 'config'
            p.atomic(target, b'first')
            p.atomic(target, b'second')
            self.assertEqual(p.read(target), b'second')
            if os.name != 'nt':
                link = root / 'link'
                link.symlink_to(target)
                with self.assertRaises(ValueError):
                    p.atomic(link, b'changed')
                self.assertEqual(target.read_bytes(), b'second')

    def test_lock_exclusion_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'lock'
            with p.file_lock(path):
                with self.assertRaises(RuntimeError):
                    with p.file_lock(path, timeout=0.05):
                        pass
            with p.file_lock(path, timeout=0.05):
                pass

    def test_process_io_bounded_output_timeout(self):
        result = p.run([sys.executable, '-c', 'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'], input=b'input')
        self.assertEqual(result.stdout, b'input')
        with self.assertRaisesRegex(RuntimeError, 'output'):
            p.run([sys.executable, '-c', 'print("x"*1000000)'], limit=1024)
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            p.run([sys.executable, '-c', 'import time;time.sleep(20)'], timeout=0.1)
        self.assertLess(time.monotonic() - start, 5)

    def test_quoted_command_and_clean_environment(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-never-print', 'PYTHONPATH': 'untrusted'}):
            env = p.clean_env()
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('PYTHONPATH', env)
        self.assertEqual(env['PYTHONDONTWRITEBYTECODE'], '1')
        text = p.command_text(['path with space', "a'b"])
        self.assertIn('path with space', text)

    def test_timeout_stops_descendant_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'unexpected'
            child = 'import time,pathlib;time.sleep(1);pathlib.Path(' + repr(str(target)) + ').write_text("alive")'
            parent = 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",' + repr(child) + ']);time.sleep(20)'
            with self.assertRaises(RuntimeError):
                p.run([sys.executable, '-c', parent], timeout=0.2)
            time.sleep(1.1)
            self.assertFalse(target.exists())

    def test_parent_exit_still_stops_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'unexpected'
            child = 'import time,pathlib;time.sleep(1);pathlib.Path(' + repr(str(target)) + ').write_text("alive")'
            parent = ('import subprocess,sys;subprocess.Popen([sys.executable,"-c",' + repr(child)
                      + '],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)')
            p.run([sys.executable, '-c', parent], timeout=3)
            time.sleep(1.1)
            self.assertFalse(target.exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX sessions only')
    def test_detached_child_stopped_after_timeout_or_parent_exit(self):
        for exits in (False, True):
            with self.subTest(parent_exits=exits), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                target = root / 'escaped'
                pidfile = root / 'pid'
                child = ('import os,time,pathlib;os.setsid();'
                         f'pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()));'
                         'time.sleep(1);'
                         f'pathlib.Path({str(target)!r}).write_text("alive")')
                parent = ('import subprocess,sys,time;'
                          f'subprocess.Popen([sys.executable,"-c",{child!r}],'
                          'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);'
                          + ('' if exits else 'time.sleep(20)'))
                if exits and sys.platform == 'darwin':
                    from codex_adaptive_agents.process_tree import _DarwinSnapshot
                    if not _DarwinSnapshot().original_versions:
                        self.skipTest('older Darwin lacks detached-orphan original-parent versions')
                try:
                    start = time.monotonic()
                    if exits:
                        self.assertEqual(p.run([sys.executable, '-c', parent], timeout=3).returncode, 0)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'timed out'):
                            p.run([sys.executable, '-c', parent], timeout=0.3)
                    self.assertLess(time.monotonic() - start, 5)
                    time.sleep(1.1)
                    self.assertFalse(target.exists(), 'detached child wrote after cleanup')
                finally:
                    if pidfile.exists():
                        try:
                            os.kill(int(pidfile.read_text()), 9)
                        except ProcessLookupError:
                            pass

    @unittest.skipIf(os.name == 'nt', 'POSIX identities only')
    def test_cleanup_preserves_unrelated_process(self):
        other = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'])
        try:
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                p.run([sys.executable, '-c', 'import time;time.sleep(20)'], timeout=0.1)
            self.assertIsNone(other.poll())
        finally:
            other.kill()
            other.wait()

    @unittest.skipIf(os.name == 'nt', 'POSIX identities only')
    def test_tracker_does_not_signal_reused_pid(self):
        from codex_adaptive_agents.process_tree import Tracker
        current = {10: (100, 1, 1, 0), 11: (200, 100, 2, 1)}
        tracker = Tracker(10, lambda: dict(current), original_parent=True)
        tracker.scan()
        # The same PID now represents a different process and family.
        current[11] = (201, 999, 3, 999)
        current[12] = (202, 201, 4, 3)
        with patch('codex_adaptive_agents.process_tree.os.kill') as kill:
            tracker.cleanup()
        self.assertEqual({call.args[0] for call in kill.call_args_list}, {10})

    @unittest.skipIf(os.name == 'nt', 'POSIX identities only')
    def test_legacy_reserved_version_is_not_used_as_parent_identity(self):
        from codex_adaptive_agents.process_tree import Tracker
        current = {10: (100, 1, 7, 0), 20: (200, 999, 8, 7)}
        tracker = Tracker(10, lambda: dict(current), original_parent=True)
        with patch('codex_adaptive_agents.process_tree.os.kill') as kill:
            tracker.cleanup()
        self.assertEqual({call.args[0] for call in kill.call_args_list}, {10})

    @unittest.skipIf(os.name == 'nt', 'POSIX process group alias')
    def test_zero_pid_cannot_be_adopted_or_signalled(self):
        from codex_adaptive_agents.process_tree import Tracker
        current = {10: (100, 1, 7, 0), 0: (0, 0, 0, 0)}
        tracker = Tracker(10, lambda: dict(current), original_parent=True, own_group=True)
        with patch('codex_adaptive_agents.process_tree.os.getpgid', return_value=10), \
                patch('codex_adaptive_agents.process_tree.os.kill') as kill:
            tracker.cleanup()
            with self.assertRaises(RuntimeError):
                tracker._signal(0, 0, 9)
        self.assertEqual({call.args[0] for call in kill.call_args_list}, {10})

    def test_linux_process_name_is_parsed_as_bytes(self):
        from codex_adaptive_agents.process_tree import _linux_identity
        fields = [b'S', b'7'] + [b'0'] * 17 + [b'1234']
        raw = b'42 (bad\xff) name) ' + b' '.join(fields)
        with patch.object(Path, 'read_bytes', return_value=raw):
            self.assertEqual(_linux_identity(42), (1234, 7))

    @unittest.skipIf(os.name == 'nt', 'POSIX cleanup fault injection')
    def test_cleanup_error_still_kills_known_detached_child(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'unexpected'
            child = ('import os,time,pathlib;os.setsid();time.sleep(1);'
                     f'pathlib.Path({str(target)!r}).write_text("alive")')
            parent = ('import subprocess,sys,time;'
                      f'subprocess.Popen([sys.executable,"-c",{child!r}]);time.sleep(20)')
            script = ('import os,sys,threading;from unittest.mock import patch;'
                      f'sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r});'
                      'from codex_adaptive_agents import process_tree as t;'
                      'r,w=os.pipe();rr,rw=os.pipe();'
                      'threading.Timer(.3,lambda:os.close(w)).start();'
                      '\nwith patch.object(t.Tracker,"cleanup",side_effect=RuntimeError("injected")):'
                      f'\n try:t._supervise(r,rw,[sys.executable,"-c",{parent!r}])'
                      '\n except RuntimeError:pass'
                      '\n else:raise AssertionError("missing failure")')
            result = p.run([sys.executable, '-I', '-B', '-c', script], timeout=6)
            self.assertEqual(result.returncode, 0, result.stderr)
            time.sleep(1.1)
            self.assertFalse(target.exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX supervision only')
    def test_supervisor_preserves_signal_and_normal_exit_status(self):
        for code in (0, 7, 125):
            with self.subTest(code=code):
                result = p.run([sys.executable, '-c', f'raise SystemExit({code})'])
                self.assertEqual(result.returncode, code)
        for sig in (9, 15):
            result = p.run([sys.executable, '-c', f'import os;os.kill(os.getpid(),{sig})'])
            self.assertEqual(result.returncode, -sig)
        with self.assertRaisesRegex(RuntimeError, 'cleanup was not confirmed'):
            p.run(['/nonexistent/codex-adaptive-agents-command'])

    @unittest.skipUnless(sys.platform == 'linux', 'Linux subreaper only')
    def test_double_fork_detached_orphan_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'double-fork'
            child = ('import os,time,pathlib;'
                     'pid=os.fork();'
                     'os._exit(0) if pid else None;'
                     'os.setsid();time.sleep(1);'
                     f'pathlib.Path({str(target)!r}).write_text("alive")')
            p.run([sys.executable, '-c', child], timeout=3)
            time.sleep(1.1)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
