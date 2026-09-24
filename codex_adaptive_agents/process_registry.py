"""Persistent, identity-checked process handoff records.

The registry is deliberately cooperative.  ``run_process`` records the
process-tree supervisor returned by :mod:`codex_adaptive_agents.platform`, while
``check_run`` and ``cleanup_run`` independently re-read operating-system
identity before treating a PID as belonging to this run.  A ledger is an
observation aid and a recovery boundary; it is not a sandbox for hostile
commands.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes as _ctypes
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

from . import platform as _platform
from . import process_tree as _tree


SCHEMA = 1
PROCESS_ROOT = "processes"
MAX_TIMEOUT = 24 * 60 * 60
MAX_TEXT = 512
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
POLL_INTERVAL = 0.1
_RUN_ID_RE = __import__("re").compile(r"^[0-9a-f]{32}$")
_ENTRY_ID_RE = _RUN_ID_RE


class _WinFileTime(_ctypes.Structure):
    _fields_ = [("low", _ctypes.c_uint32), ("high", _ctypes.c_uint32)]


def _windows_kernel():
    """Load the small Windows API surface with pointer-sized handles."""
    kernel = _ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [_ctypes.c_uint32, _ctypes.c_int, _ctypes.c_uint32]
    kernel.OpenProcess.restype = _ctypes.c_void_p
    kernel.GetProcessTimes.argtypes = [
        _ctypes.c_void_p,
        _ctypes.POINTER(_WinFileTime),
        _ctypes.POINTER(_WinFileTime),
        _ctypes.POINTER(_WinFileTime),
        _ctypes.POINTER(_WinFileTime),
    ]
    kernel.GetProcessTimes.restype = _ctypes.c_int
    kernel.TerminateProcess.argtypes = [_ctypes.c_void_p, _ctypes.c_uint32]
    kernel.TerminateProcess.restype = _ctypes.c_int
    kernel.WaitForSingleObject.argtypes = [_ctypes.c_void_p, _ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = _ctypes.c_uint32
    kernel.GetExitCodeProcess.argtypes = [_ctypes.c_void_p, _ctypes.POINTER(_ctypes.c_uint32)]
    kernel.GetExitCodeProcess.restype = _ctypes.c_int
    kernel.CloseHandle.argtypes = [_ctypes.c_void_p]
    kernel.CloseHandle.restype = _ctypes.c_int
    return kernel


def _windows_creation_time(kernel, handle) -> int | None:
    creation = _WinFileTime()
    exit_time = _WinFileTime()
    kernel_time = _WinFileTime()
    user_time = _WinFileTime()
    if not kernel.GetProcessTimes(
        handle,
        _ctypes.byref(creation),
        _ctypes.byref(exit_time),
        _ctypes.byref(kernel_time),
        _ctypes.byref(user_time),
    ):
        return None
    return (int(creation.high) << 32) | int(creation.low)


def _windows_has_exited(kernel, handle) -> bool | None:
    """Use the same opened handle to distinguish a dead process from a live one."""
    wait = kernel.WaitForSingleObject(handle, 0)
    if wait == 0x00000000:  # WAIT_OBJECT_0
        return True
    if wait != 0x00000102:  # WAIT_TIMEOUT
        return None
    code = _ctypes.c_uint32()
    if not kernel.GetExitCodeProcess(handle, _ctypes.byref(code)):
        return None
    return int(code.value) != 259  # STILL_ACTIVE


class ProcessRegistryError(ValueError):
    """A malformed or unverifiable process registry operation."""


def _stamp(value: datetime | None = None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _project(value: object) -> Path:
    if isinstance(value, Path):
        raw = value
    elif isinstance(value, str) and value:
        raw = Path(value)
    else:
        raise ProcessRegistryError("project must be an existing directory")
    try:
        result = raw.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise ProcessRegistryError("project must be an existing directory") from None
    if not result.is_dir():
        raise ProcessRegistryError("project must be an existing directory")
    return result


def _same_real_directory(left: object, right: object) -> bool:
    """Match a directory identity while accepting only a case alias.

    The registry stores a project path in both the run and entry ledgers.  A
    case-insensitive filesystem can return the same directory for paths with
    different casing, but a plain case-folded string comparison would also
    accept a copied or redirected ledger.  ``samefile`` proves the filesystem
    identity; rejecting symlink/reparse components and requiring the
    normalized strings to differ only by case keeps the accepted alias
    narrow on POSIX and Windows.
    """
    if not isinstance(left, (str, Path)) or not isinstance(right, (str, Path)):
        return False
    try:
        left_path = Path(left)
        right_path = Path(right)
        if not left_path.is_absolute() or not right_path.is_absolute():
            return False

        def has_link_component(path: Path) -> bool:
            current = Path(path.anchor)
            for part in path.parts[1:]:
                current /= part
                try:
                    info = current.lstat()
                except (OSError, ValueError):
                    return False
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    return True
            return False

        if has_link_component(left_path) or has_link_component(right_path):
            return False
        left_info = left_path.lstat()
        right_info = right_path.lstat()
        if (
            stat.S_ISLNK(left_info.st_mode)
            or stat.S_ISLNK(right_info.st_mode)
            or getattr(left_info, "st_file_attributes", 0) & 0x400
            or getattr(right_info, "st_file_attributes", 0) & 0x400
            or not stat.S_ISDIR(left_info.st_mode)
            or not stat.S_ISDIR(right_info.st_mode)
        ):
            return False
        if not os.path.samefile(left_path, right_path):
            return False
        return os.path.normpath(os.fspath(left_path)).casefold() == os.path.normpath(os.fspath(right_path)).casefold()
    except (OSError, TypeError, ValueError, RuntimeError):
        return False


def _run_id(value: object) -> str:
    if not isinstance(value, str) or not _RUN_ID_RE.fullmatch(value):
        raise ProcessRegistryError("invalid run id")
    return value


def _entry_id(value: object) -> str:
    if not isinstance(value, str) or not _ENTRY_ID_RE.fullmatch(value):
        raise ProcessRegistryError("invalid entry id")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT or "\x00" in value:
        raise ProcessRegistryError(name + " must be a bounded non-empty string")
    return value


def _root(project: Path) -> Path:
    return project / ".codex-adaptive-agents" / PROCESS_ROOT


def _run_dir(project: Path, run_id: str) -> Path:
    return _root(project) / _run_id(run_id)


def _run_path(project: Path, run_id: str) -> Path:
    return _run_dir(project, run_id) / "run.json"


def _lock_path(project: Path, run_id: str) -> Path:
    return _run_dir(project, run_id) / "run.lock"


def _entry_path(project: Path, run_id: str, entry_id: str) -> Path:
    return _run_dir(project, run_id) / (_entry_id(entry_id) + ".json")


def _read_json(path: Path, label: str) -> dict:
    try:
        raw = _platform.read(path)
        if raw is None:
            raise ProcessRegistryError(label + " unavailable")
        value = _platform.loads(raw)
    except ProcessRegistryError:
        raise
    except (OSError, TypeError, UnicodeError, ValueError):
        raise ProcessRegistryError(label + " invalid") from None
    if not isinstance(value, dict):
        raise ProcessRegistryError(label + " invalid")
    return value


def _write_json(path: Path, value: dict) -> None:
    try:
        _platform.atomic(path, _platform.encode(value))
    except (OSError, TypeError, ValueError):
        raise ProcessRegistryError("process ledger write failed") from None


def _validate_run(value: dict, project: Path, run_id: str) -> dict:
    allowed = {"schema", "run_id", "project", "created_at", "status", "closed_at", "result_status", "entry_ids", "retained"}
    if set(value) - allowed or value.get("schema") != SCHEMA or value.get("run_id") != run_id:
        raise ProcessRegistryError("run ledger invalid")
    if not _same_real_directory(value.get("project"), project) or not isinstance(value.get("created_at"), str):
        raise ProcessRegistryError("run ledger invalid")
    entry_ids = value.get("entry_ids")
    if not isinstance(entry_ids, list) or len(entry_ids) != len(set(entry_ids)):
        raise ProcessRegistryError("run ledger invalid")
    for entry_id in entry_ids:
        _entry_id(entry_id)
    retained = value.get("retained", [])
    if not isinstance(retained, list) or any(item not in entry_ids for item in retained):
        raise ProcessRegistryError("run retention invalid")
    if value.get("status") not in {"OPEN", "CLOSING", "CLOSED"}:
        raise ProcessRegistryError("run ledger invalid")
    if value.get("closed_at") not in (None, "") and not isinstance(value.get("closed_at"), str):
        raise ProcessRegistryError("run ledger invalid")
    result_status = value.get("result_status")
    if result_status is not None and not isinstance(result_status, str):
        raise ProcessRegistryError("run ledger invalid")
    return value


def _identity_shape(value: object) -> dict:
    if not isinstance(value, dict):
        raise ProcessRegistryError("process identity invalid")
    allowed = {"kind", "boot_id", "start_time", "identity", "version", "creation_time", "session_id"}
    if set(value) - allowed or not isinstance(value.get("kind"), str):
        raise ProcessRegistryError("process identity invalid")
    kind = value["kind"]
    if kind == "linux":
        if not isinstance(value.get("boot_id"), str) or not value["boot_id"] or not isinstance(value.get("start_time"), int) or value["start_time"] < 0:
            raise ProcessRegistryError("process identity invalid")
    elif kind == "darwin":
        if not isinstance(value.get("boot_id"), str) or not value["boot_id"] or not isinstance(value.get("identity"), int) or not isinstance(value.get("version"), int):
            raise ProcessRegistryError("process identity invalid")
    elif kind == "windows":
        if not isinstance(value.get("creation_time"), int) or value["creation_time"] < 0:
            raise ProcessRegistryError("process identity invalid")
    else:
        raise ProcessRegistryError("process identity invalid")
    session = value.get("session_id")
    if session is not None and (not isinstance(session, int) or session < 0):
        raise ProcessRegistryError("process identity invalid")
    return value


def _process_shape(value: object, *, required: bool = False) -> dict | None:
    if value is None and not required:
        return None
    if not isinstance(value, dict) or set(value) - {"pid", "identity"}:
        raise ProcessRegistryError("process record invalid")
    pid = value.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ProcessRegistryError("process record invalid")
    return {"pid": pid, "identity": _identity_shape(value.get("identity"))}


def _validate_entry(value: dict, project: Path, run_id: str, entry_id: str) -> dict:
    allowed = {
        "schema", "run_id", "entry_id", "project", "owner", "purpose", "wrapper", "process",
        "descendants", "descendant_snapshot", "started_at", "finished_at", "status",
        "exit_code", "error", "cleanup_status",
    }
    if set(value) - allowed or value.get("schema") != SCHEMA or value.get("run_id") != run_id or value.get("entry_id") != entry_id:
        raise ProcessRegistryError("entry ledger invalid")
    if "project" in value and not _same_real_directory(value.get("project"), project):
        raise ProcessRegistryError("entry ledger invalid")
    _text(value.get("owner"), "owner")
    _text(value.get("purpose"), "purpose")
    _process_shape(value.get("wrapper"), required=True)
    _process_shape(value.get("process"))
    descendants = value.get("descendants")
    if not isinstance(descendants, list):
        raise ProcessRegistryError("entry ledger invalid")
    seen = set()
    for item in descendants:
        item = _process_shape(item, required=True)
        if item["pid"] in seen:
            raise ProcessRegistryError("entry ledger invalid")
        seen.add(item["pid"])
    if value.get("descendant_snapshot") not in {"PENDING", "OK", "LIMITED", "UNVERIFIED"}:
        raise ProcessRegistryError("entry ledger invalid")
    if not isinstance(value.get("started_at"), str) or value.get("finished_at") not in (None, "") and not isinstance(value.get("finished_at"), str):
        raise ProcessRegistryError("entry ledger invalid")
    if value.get("status") not in {"STARTING", "RUNNING", "SUCCESS", "FAILED", "TIMEOUT", "UNVERIFIED"}:
        raise ProcessRegistryError("entry ledger invalid")
    exit_code = value.get("exit_code")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise ProcessRegistryError("entry ledger invalid")
    for field in ("error", "cleanup_status"):
        if value.get(field) is not None and not isinstance(value.get(field), str):
            raise ProcessRegistryError("entry ledger invalid")
    return value


def _read_run(project: Path, run_id: str) -> dict:
    run_id = _run_id(run_id)
    run_dir = _run_dir(project, run_id)
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise ProcessRegistryError("run ledger unavailable")
    return _validate_run(_read_json(run_dir / "run.json", "run ledger"), project, run_id)


def _read_entries(project: Path, run_id: str) -> list[dict]:
    run_dir = _run_dir(project, run_id)
    run = _read_run(project, run_id)
    expected = set(run["entry_ids"])
    try:
        paths = sorted(run_dir.glob("*.json"))
    except OSError:
        raise ProcessRegistryError("entry ledger unavailable") from None
    actual = {path.stem for path in paths if path.name != "run.json"}
    if actual != expected:
        raise ProcessRegistryError("entry ledger incomplete")
    result = []
    for entry_id in sorted(expected):
        result.append(_validate_entry(_read_json(_entry_path(project, run_id, entry_id), "entry ledger"), project, run_id, entry_id))
    return result


@contextmanager
def _run_lock(project: Path, run_id: str):
    try:
        with _platform.file_lock(_lock_path(project, _run_id(run_id)), timeout=5):
            yield
    except ProcessRegistryError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise ProcessRegistryError("process ledger locked or unavailable") from None


def _now_identity(pid: int) -> tuple[str, dict | None]:
    """Return live/gone/unknown plus a stable identity, never just a PID."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return "unknown", None
    if sys.platform == "linux":
        stat_path = Path("/proc") / str(pid) / "stat"
        try:
            value = _tree._linux_identity(pid)
        except Exception:
            value = None
        if value is None:
            try:
                stat_path.stat()
            except FileNotFoundError:
                return "gone", None
            except OSError:
                return "unknown", None
            return "unknown", None
        try:
            fields = stat_path.read_bytes().rsplit(b")", 1)[1].split()
            if fields and fields[0] in {b"Z", b"X"}:
                return "gone", None
        except FileNotFoundError:
            return "gone", None
        except OSError:
            return "unknown", None
        boot = _linux_boot_id()
        if not boot:
            return "live", {"kind": "linux", "boot_id": "", "start_time": int(value[0]), "session_id": _session_id(pid)}
        return "live", {"kind": "linux", "boot_id": boot, "start_time": int(value[0]), "session_id": _session_id(pid)}
    if sys.platform == "darwin":
        snapshot = None
        try:
            snapshot = _darwin_snapshot()
            value = snapshot.identity(pid, zombies=False)
        except Exception:
            value = None
        if value is None:
            try:
                if snapshot is not None and snapshot.identity(pid, zombies=True) is not None:
                    return "gone", None
            except Exception:
                pass
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return "gone", None
            except PermissionError:
                return "unknown", None
            except OSError:
                return "unknown", None
            return "unknown", None
        boot = _darwin_boot_id()
        return "live", {"kind": "darwin", "boot_id": boot or "", "identity": int(value[0]), "version": int(value[2]), "session_id": _session_id(pid)}
    if os.name == "nt":
        return _windows_identity(pid)
    return "unknown", None


_DARWIN_SNAPSHOT = None


def _darwin_snapshot():
    global _DARWIN_SNAPSHOT
    if _DARWIN_SNAPSHOT is None:
        _DARWIN_SNAPSHOT = _tree._DarwinSnapshot()
    return _DARWIN_SNAPSHOT


def _session_id(pid: int) -> int | None:
    try:
        value = os.getsid(pid)
    except (OSError, ProcessLookupError, PermissionError):
        return None
    return int(value) if value >= 0 else None


def _linux_boot_id() -> str | None:
    try:
        value = (Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip())
    except (OSError, UnicodeError):
        return None
    return value if value and len(value) <= 128 else None


def _darwin_boot_id() -> str | None:
    class Timeval(_ctypes.Structure):
        _fields_ = [("sec", _ctypes.c_long), ("usec", _ctypes.c_int32)]
    try:
        libc = _ctypes.CDLL(None, use_errno=True)
        call = libc.sysctlbyname
        call.argtypes = [_ctypes.c_char_p, _ctypes.c_void_p, _ctypes.POINTER(_ctypes.c_size_t), _ctypes.c_void_p, _ctypes.c_size_t]
        call.restype = _ctypes.c_int
        value = Timeval()
        size = _ctypes.c_size_t(_ctypes.sizeof(value))
        if call(b"kern.boottime", _ctypes.byref(value), _ctypes.byref(size), None, 0) != 0:
            return None
        return "{}:{}".format(int(value.sec), int(value.usec))
    except (AttributeError, OSError, TypeError):
        return None


def _windows_identity(pid: int) -> tuple[str, dict | None]:
    if os.name != "nt":
        return "unknown", None
    kernel = None
    handle = None
    try:
        kernel = _windows_kernel()
        handle = kernel.OpenProcess(0x101000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            error = _ctypes.get_last_error()
            return ("gone", None) if error == 87 else ("unknown", None)
        stamp = _windows_creation_time(kernel, handle)
        if stamp is None:
            return "unknown", None
        exited = _windows_has_exited(kernel, handle)
        if exited is None:
            return "unknown", None
        if exited:
            return "gone", None
        return "live", {"kind": "windows", "creation_time": stamp}
    except (AttributeError, OSError, TypeError):
        return "unknown", None
    finally:
        if handle:
            try:
                kernel.CloseHandle(handle)
            except (AttributeError, OSError, TypeError):
                pass


def _identity_matches(expected: dict, current: dict | None) -> bool | None:
    if current is None or expected.get("kind") != current.get("kind"):
        return False
    kind = expected.get("kind")
    if kind == "linux":
        if not expected.get("boot_id") or not current.get("boot_id"):
            return None
        return expected.get("boot_id") == current.get("boot_id") and expected.get("start_time") == current.get("start_time")
    if kind == "darwin":
        if not expected.get("boot_id") or not current.get("boot_id"):
            return None
        # Darwin pidversion changes across exec; uniqueid identifies the
        # process lifetime and stays stable when its executable changes.
        return (expected.get("boot_id") == current.get("boot_id") and
                expected.get("identity") == current.get("identity"))
    if kind == "windows":
        return expected.get("creation_time") == current.get("creation_time")
    return None


def _observe(record: dict) -> tuple[str, dict | None]:
    state, current = _now_identity(record["pid"])
    if state == "gone":
        return "gone", None
    if state != "live":
        return "unknown", current
    matched = _identity_matches(record["identity"], current)
    if matched is True:
        return "match", current
    if matched is False:
        return "mismatch", current
    return "unknown", current


def _raw_birth_matches(identity: dict, raw: object) -> bool:
    # Tracker.known/scan store scalar birth tokens, not snapshot tuples.
    birth = raw[0] if isinstance(raw, (tuple, list)) and raw else raw
    field = "start_time" if identity.get("kind") == "linux" else "identity"
    return type(birth) is int and birth == identity.get(field)


def _snapshot_descendants(pid: int, expected_identity: dict | None = None) -> tuple[str, list[dict]]:
    """Return a bounded identity snapshot of a supervisor and its descendants."""
    if os.name == "nt":
        return "LIMITED", []
    try:
        snapshot = _darwin_snapshot() if sys.platform == "darwin" else _tree._linux_snapshot
        tracker = _tree.Tracker(pid, snapshot, original_parent=sys.platform == "darwin",
                                original_versions=False,
                                own_group=sys.platform == "darwin")
        if expected_identity is not None and not _raw_birth_matches(expected_identity, tracker.known.get(pid)):
            return "UNVERIFIED", []
        alive = tracker.scan()
        if expected_identity is not None and not _raw_birth_matches(expected_identity, alive.get(pid)):
            return "UNVERIFIED", []
        descendants = []
        for child_pid in sorted(alive):
            if child_pid == pid:
                continue
            state, identity = _now_identity(int(child_pid))
            if state == "unknown":
                return "UNVERIFIED", descendants
            if state == "live" and identity is not None:
                if not _raw_birth_matches(identity, alive[child_pid]):
                    return "UNVERIFIED", descendants
                descendants.append({"pid": int(child_pid), "identity": identity})
        return "OK", descendants
    except (OSError, RuntimeError, ProcessRegistryError, TypeError, ValueError, IndexError):
        return "UNVERIFIED", []


def _payload(entry: dict, *, retained: bool = False, active: bool = False, issue: str | None = None) -> dict:
    result = {
        "entry_id": entry["entry_id"],
        "owner": entry["owner"],
        "purpose": entry["purpose"],
        "status": entry["status"],
        "active": bool(active),
        "retained": bool(retained),
    }
    if issue:
        result["issue"] = issue
    return result


def _inspect(project: Path, run: dict, entries: list[dict], retain: set[str], *, closing_unverified: bool = True) -> dict:
    details, issues, active, unreported = [], [], [], []
    for entry in entries:
        confirmed = entry.get("cleanup_status") == "CONFIRMED"
        missing = entry["status"] in {"STARTING", "RUNNING"}
        records = [("wrapper", entry["wrapper"])]
        if entry.get("process"):
            records.append(("process", entry["process"]))
        records.extend(("descendant", row) for row in entry["descendants"])
        live = False
        issue = None
        for label, record in records:
            state, _ = _observe(record)
            if state in {"unknown", "mismatch"}:
                issue = f"{label} identity {state}"
            elif state == "match":
                live = True
                if label != "wrapper":
                    snapshot_state, _ = _snapshot_descendants(record["pid"], record["identity"])
                    if snapshot_state == "UNVERIFIED":
                        issue = "live descendant snapshot unavailable"
        if missing:
            unreported.append(entry["entry_id"])
            if not live and not confirmed:
                issue = issue or "completion not reported; independent cleanup required"
        if not confirmed and (entry.get("cleanup_status") == "UNVERIFIED" or
                              entry.get("descendant_snapshot") == "UNVERIFIED"):
            issue = issue or "previous cleanup or snapshot was not confirmed"
        item = _payload(entry, retained=entry["entry_id"] in retain, active=live, issue=issue)
        item.update(pid=entry["wrapper"]["pid"], command_pid=(entry.get("process") or {}).get("pid"),
                    completion_reported=not missing)
        details.append(item)
        if live:
            active.append(entry["entry_id"])
        if issue:
            issues.append(dict(entry_id=entry["entry_id"], error=issue))
    if issues or (closing_unverified and run["status"] == "CLOSING"):
        status = "UNVERIFIED"
    elif set(active) - retain:
        status = "DIRTY"
    elif retain:
        status = "RETAINED"
    else:
        status = "CLEAN"
    return dict(status=status, run_id=run["run_id"], project=str(project), entries=details,
                active_entries=active, retained=sorted(retain), issues=issues,
                unreported_entries=unreported, registered_scope_only=True)


def _new_entry(project: Path, run_id: str, owner: str, purpose: str, wrapper: dict) -> tuple[str, dict]:
    for _ in range(20):
        entry_id = uuid.uuid4().hex
        path = _entry_path(project, run_id, entry_id)
        if path.exists():
            continue
        entry = {
            "schema": SCHEMA,
            "run_id": run_id,
            "entry_id": entry_id,
            "project": str(project),
            "owner": owner,
            "purpose": purpose,
            "wrapper": wrapper,
            "process": None,
            "descendants": [],
            "descendant_snapshot": "PENDING",
            "started_at": _stamp(),
            "finished_at": None,
            "status": "STARTING",
            "exit_code": None,
            "error": None,
            "cleanup_status": None,
        }
        _write_json(path, entry)
        return entry_id, entry
    raise ProcessRegistryError("could not allocate entry id")


def init_run(project: str | Path) -> dict:
    project_path = _project(project)
    root = _root(project_path)
    try:
        _platform.safe(root / ".directory-check")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _platform.safe(root / ".directory-check")
    except (OSError, ValueError):
        raise ProcessRegistryError("process ledger unavailable") from None
    for _ in range(20):
        run_id = uuid.uuid4().hex
        run_dir = _run_dir(project_path, run_id)
        try:
            run_dir.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError:
            raise ProcessRegistryError("process ledger unavailable") from None
        run = {
            "schema": SCHEMA,
            "run_id": run_id,
            "project": str(project_path),
            "created_at": _stamp(),
            "status": "OPEN",
            "closed_at": None,
            "result_status": None,
            "entry_ids": [],
        }
        try:
            _write_json(run_dir / "run.json", run)
        except BaseException:
            raise
        return {"status": "STARTED", "run_id": run_id, "project": str(project_path), "created_at": run["created_at"]}
    raise ProcessRegistryError("could not allocate run id")


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProcessRegistryError("timeout must be finite and positive")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT:
        raise ProcessRegistryError("timeout must be finite and positive")
    return timeout


def _validate_argv(argv: object) -> list[str]:
    if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)) or not argv:
        raise ProcessRegistryError("command argv must be a non-empty argument list")
    result = []
    total = 0
    for index, value in enumerate(argv):
        if not isinstance(value, str) or (not value and index == 0) or "\x00" in value:
            raise ProcessRegistryError("command argv contains invalid argument")
        total += len(value.encode("utf-8", errors="strict"))
        if total > MAX_OUTPUT_BYTES:
            raise ProcessRegistryError("command argv is too large")
        result.append(value)
    return result


def _update_entry(project: Path, run_id: str, entry_id: str, update: dict, *, allow_closing: bool = False) -> tuple[bool, dict, dict]:
    with _run_lock(project, run_id):
        run = _read_run(project, run_id)
        if entry_id not in run["entry_ids"]:
            raise ProcessRegistryError("entry is not listed in run ledger")
        entry_path = _entry_path(project, run_id, entry_id)
        entry = _validate_entry(_read_json(entry_path, "entry ledger"), project, run_id, entry_id)
        if run["status"] != "OPEN" and not allow_closing:
            return False, run, entry
        entry.update(update)
        _validate_entry(entry, project, run_id, entry_id)
        _write_json(entry_path, entry)
        return True, run, entry


def _record_snapshot(project: Path, run_id: str, entry_id: str, pid: int, expected_identity: dict | None = None) -> str:
    state, descendants = _snapshot_descendants(pid, expected_identity)
    with _run_lock(project, run_id):
        run = _read_run(project, run_id)
        if entry_id not in run["entry_ids"]:
            raise ProcessRegistryError("entry is not listed in run ledger")
        entry_path = _entry_path(project, run_id, entry_id)
        entry = _validate_entry(_read_json(entry_path, "entry ledger"), project, run_id, entry_id)
        known = {item["pid"]: item for item in entry.get("descendants", [])}
        for item in descendants:
            previous = known.get(item["pid"])
            if previous is not None and _identity_matches(previous["identity"], item["identity"]) is not True:
                state = "UNVERIFIED"
                continue
            known.setdefault(item["pid"], item)
        merged = sorted(known.values(), key=lambda item: item["pid"])
        if run["status"] == "OPEN" and (
            entry.get("descendants") != merged or entry.get("descendant_snapshot") != state
        ):
            entry.update({"descendants": merged, "descendant_snapshot": state})
            _validate_entry(entry, project, run_id, entry_id)
            _write_json(entry_path, entry)
    return state


def _summary_log_directory(project: Path, run_id: str, entry_id: str) -> Path:
    return _run_dir(project, run_id) / _entry_id(entry_id)


def _summary_log_paths(project: Path, run_id: str, entry_id: str) -> dict[str, Path]:
    directory = _summary_log_directory(project, run_id, entry_id)
    return {name: directory / (name + ".log") for name in ("stdout", "stderr")}


def _same_file_identity(path: Path, info: os.stat_result) -> bool:
    try:
        _platform.safe(path)
        current = path.lstat()
    except (OSError, ValueError, RuntimeError):
        return False
    return (
        stat.S_ISREG(current.st_mode)
        and current.st_dev == info.st_dev
        and current.st_ino == info.st_ino
        and getattr(current, "st_nlink", 1) == 1
        and not getattr(current, "st_file_attributes", 0) & 0x400
    )


def _prepare_summary_logs(project: Path, run_id: str, entry_id: str) -> tuple[dict[str, str], list[object]]:
    """Create new, private, non-following output logs for one registered entry."""
    directory = _summary_log_directory(project, run_id, entry_id)
    run_dir = _run_dir(project, run_id)
    try:
        _platform.safe(directory)
        run_info = run_dir.lstat()
        if not stat.S_ISDIR(run_info.st_mode) or getattr(run_info, "st_file_attributes", 0) & 0x400:
            raise OSError("managed run directory is not a regular directory")
        if os.name != "nt" and run_info.st_mode & 0o077:
            raise OSError("managed run directory is not private")
        directory.mkdir(mode=0o700)
        directory_info = directory.lstat()
        if not stat.S_ISDIR(directory_info.st_mode) or getattr(directory_info, "st_file_attributes", 0) & 0x400:
            raise OSError("entry log directory is not a regular directory")
        if os.name != "nt" and directory_info.st_mode & 0o077:
            raise OSError("entry log directory is not private")
    except (OSError, ValueError, RuntimeError) as error:
        raise ProcessRegistryError(_summary_os_error("summary log directory unavailable", error)) from None

    paths = _summary_log_paths(project, run_id, entry_id)
    handles: list[object] = []
    created: list[tuple[Path, os.stat_result]] = []
    try:
        for name in ("stdout", "stderr"):
            path = paths[name]
            _platform.safe(path)
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
            fd = None
            try:
                fd = os.open(path, flags, 0o600)
                info = os.fstat(fd)
                created.append((path, info))
                if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1:
                    raise OSError("summary log is not a new single-link regular file")
                if os.name != "nt":
                    os.fchmod(fd, 0o600)
                    info = os.fstat(fd)
                if not _same_file_identity(path, info):
                    raise OSError("summary log path changed during creation")
                handle = os.fdopen(fd, "wb", buffering=0)
                fd = None
                handles.append(handle)
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
    except (OSError, ValueError, RuntimeError) as error:
        for handle in handles:
            try:
                handle.close()
            except OSError:
                pass
        for path, info in created:
            if _same_file_identity(path, info):
                try:
                    path.unlink()
                except OSError:
                    pass
        try:
            directory.rmdir()
        except OSError:
            pass
        raise ProcessRegistryError(_summary_os_error("summary log creation failed", error)) from None
    return ({name: str(path.absolute()) for name, path in paths.items()}, handles)


def _close_summary_logs(handles: list[object] | None, paths: dict[str, str]) -> str | None:
    if handles is None:
        return None
    error = None
    identities = {}
    for name, handle in zip(("stdout", "stderr"), handles):
        try:
            handle.flush()
            os.fsync(handle.fileno())
        except (OSError, ValueError) as exc:
            error = error or _summary_os_error("summary log flush failed", exc)
        try:
            identities[name] = os.fstat(handle.fileno())
        except (OSError, ValueError) as exc:
            error = error or _summary_os_error("summary log inspection failed", exc)
        try:
            handle.close()
        except (OSError, ValueError) as exc:
            error = error or _summary_os_error("summary log close failed", exc)
    for name, raw_path in paths.items():
        try:
            path = Path(raw_path)
            if name not in identities or not _same_file_identity(path, identities[name]):
                error = error or "summary log integrity check failed"
        except (OSError, ValueError, RuntimeError):
            error = error or "summary log integrity check failed"
    return error


def _summary_os_error(prefix: str, error: OSError | ValueError) -> str:
    detail = getattr(error, "strerror", None) or str(error) or type(error).__name__
    detail = detail[:256]
    errno = getattr(error, "errno", None)
    code = "" if errno is None else " (errno {})".format(errno)
    return prefix + code + ": " + detail


def _write_all(handle, chunk: bytes, on_write=None) -> None:
    view = memoryview(chunk)
    while view:
        written = handle.write(view)
        if not written:
            raise OSError("short write")
        if on_write is not None:
            on_write(written)
        view = view[written:]


def _process_result(status: str, run_id: str, entry_id: str, owner: str, purpose: str,
                    pid: int | None, exit_code: int | None, error: str | None, *,
                    summary: bool, logs: dict[str, str] | None = None,
                    captured: list[int] | None = None, complete: bool = False,
                    buffers: list[bytearray] | None = None) -> dict:
    result = {
        "status": status,
        "run_id": run_id,
        "entry_id": entry_id,
        "owner": owner,
        "purpose": purpose,
        "pid": pid,
        "exit_code": exit_code,
    }
    if error:
        result["error"] = error
    if summary:
        captured = captured or [0, 0]
        result.update({
            "logs": dict(logs or {}),
            "captured_bytes": {"stdout": captured[0], "stderr": captured[1]},
            "output_complete": bool(complete),
        })
    else:
        buffers = buffers or [bytearray(), bytearray()]
        result.update({
            "stdout": bytes(buffers[0]).decode("utf-8", errors="replace"),
            "stderr": bytes(buffers[1]).decode("utf-8", errors="replace"),
        })
    return result


def _drain(pipe, buffers: list[bytearray] | None, index: int, guard: threading.Lock,
           overflow: threading.Event, *, log_handles: list[object] | None = None,
           log_paths: list[Path] | None = None,
           captured: list[int] | None = None, stream_error: list[str | None] | None = None,
           write_failed: threading.Event | None = None) -> None:
    try:
        while True:
            chunk = pipe.read1(16384)
            if not chunk:
                break
            with guard:
                total = sum(captured) if captured is not None else sum(map(len, buffers or ()))
                if total + len(chunk) > MAX_OUTPUT_BYTES:
                    overflow.set()
                    break
                if log_handles is None:
                    buffers[index].extend(chunk)
                else:
                    try:
                        if not _same_file_identity(log_paths[index], os.fstat(log_handles[index].fileno())):
                            raise OSError("summary log path changed or gained a hard link")
                        _write_all(log_handles[index], chunk,
                                   on_write=lambda amount: captured.__setitem__(
                                       index, captured[index] + amount))
                    except OSError as exc:
                        if stream_error is not None:
                            stream_error[index] = _summary_os_error("summary log write failed", exc)
                        if write_failed is not None:
                            write_failed.set()
                        break
                if captured is not None and log_handles is None:
                    captured[index] += len(chunk)
    except (OSError, ValueError) as exc:
        if log_handles is not None:
            if stream_error is not None:
                stream_error[index] = _summary_os_error("summary output stream read failed", exc)
            if write_failed is not None:
                write_failed.set()
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _stop(proc) -> str | None:
    try:
        _platform.stop(proc)
    except Exception as error:
        return str(error) if type(error) in (ValueError, RuntimeError) else "process cleanup could not be confirmed"
    return None


def run_process(project: str | Path, run_id: str, owner: str, purpose: str,
                argv: list[str] | tuple[str, ...], *, timeout: float = 600,
                on_start=None, summary: bool = False) -> dict:
    project_path = _project(project)
    run_id = _run_id(run_id)
    owner = _text(owner, "owner")
    purpose = _text(purpose, "purpose")
    args = _validate_argv(argv)
    timeout = _validate_timeout(timeout)
    if not isinstance(summary, bool):
        raise ProcessRegistryError("summary must be a boolean")
    if on_start is not None and not callable(on_start):
        raise ProcessRegistryError("on_start must be callable")
    wrapper_pid = os.getpid()
    wrapper_state, wrapper_identity = _now_identity(wrapper_pid)
    if wrapper_state != "live" or wrapper_identity is None or not wrapper_identity.get("boot_id", "") and wrapper_identity.get("kind") in {"linux", "darwin"}:
        raise ProcessRegistryError("launcher identity unavailable")
    wrapper = {"pid": wrapper_pid, "identity": wrapper_identity}
    entry_id = None
    proc = None
    log_paths: dict[str, str] = {}
    log_handles: list[object] | None = None
    started_at = _stamp()
    callback_error = None
    with _run_lock(project_path, run_id):
        run = _read_run(project_path, run_id)
        if run["status"] != "OPEN":
            raise ProcessRegistryError("run is closing or closed")
        entry_id, _entry = _new_entry(project_path, run_id, owner, purpose, wrapper)
        run["entry_ids"].append(entry_id)
        _validate_run(run, project_path, run_id)
        _write_json(_run_path(project_path, run_id), run)
        callback = {
            "status": "STARTED",
            "run_id": run_id,
            "entry_id": entry_id,
            "owner": owner,
            "purpose": purpose,
            "pid": wrapper_pid,
            "identity": wrapper_identity,
            "started_at": started_at,
        }
        if on_start is not None:
            try:
                on_start(dict(callback))
            except Exception as error:
                callback_error = str(error)
        if callback_error is not None:
            update = {"status": "FAILED", "finished_at": _stamp(), "error": "on_start callback failed"}
            entry = _read_json(_entry_path(project_path, run_id, entry_id), "entry ledger")
            entry.update(update)
            _validate_entry(entry, project_path, run_id, entry_id)
            _write_json(_entry_path(project_path, run_id, entry_id), entry)
            if not summary:
                return {**callback, "status": "FAILED", "error": "on_start callback failed"}
            return _process_result("FAILED", run_id, entry_id, owner, purpose, wrapper_pid,
                                   None, "on_start callback failed", summary=summary,
                                   complete=False)
        if summary:
            try:
                log_paths, log_handles = _prepare_summary_logs(project_path, run_id, entry_id)
            except ProcessRegistryError as log_exception:
                error = str(log_exception)
                entry = _read_json(_entry_path(project_path, run_id, entry_id), "entry ledger")
                entry.update({"status": "FAILED", "finished_at": _stamp(), "error": error,
                              "cleanup_status": "CONFIRMED"})
                _validate_entry(entry, project_path, run_id, entry_id)
                _write_json(_entry_path(project_path, run_id, entry_id), entry)
                return _process_result("FAILED", run_id, entry_id, owner, purpose, wrapper_pid,
                                       None, error, summary=True, complete=False)
        try:
            proc = _platform.spawn(args, cwd=str(project_path))
            process_state, process_identity = _now_identity(int(proc.pid))
            already_exited = process_state == "gone" and proc.poll() is not None
            if not already_exited and (process_state != "live" or process_identity is None or not process_identity.get("boot_id", "") and process_identity.get("kind") in {"linux", "darwin"}):
                cleanup_error = _stop(proc)
                update = {"status": "UNVERIFIED", "finished_at": _stamp(), "error": cleanup_error or "process identity unavailable"}
                entry = _read_json(_entry_path(project_path, run_id, entry_id), "entry ledger")
                entry.update(update)
                _validate_entry(entry, project_path, run_id, entry_id)
                _write_json(_entry_path(project_path, run_id, entry_id), entry)
                log_error = _close_summary_logs(log_handles, log_paths) if summary else None
                log_handles = None
                if log_error:
                    update["error"] = update["error"] + "; " + log_error
                    _update_entry(project_path, run_id, entry_id,
                                  {"error": update["error"]}, allow_closing=True)
                if not summary:
                    return {**callback, "status": "UNVERIFIED", "error": update["error"]}
                return _process_result("UNVERIFIED", run_id, entry_id, owner, purpose,
                                       int(proc.pid), None, update["error"], summary=summary,
                                       logs=log_paths, complete=False)
            process = None if already_exited else {"pid": int(proc.pid), "identity": process_identity}
            entry = _read_json(_entry_path(project_path, run_id, entry_id), "entry ledger")
            entry.update({"process": process, "status": "RUNNING"})
            _validate_entry(entry, project_path, run_id, entry_id)
            _write_json(_entry_path(project_path, run_id, entry_id), entry)
        except Exception:
            cleanup_error = _stop(proc) if proc is not None else None
            log_error = _close_summary_logs(log_handles, log_paths) if summary else None
            log_handles = None
            error = "process could not start"
            if cleanup_error:
                error += "; " + cleanup_error
            if log_error:
                error += "; " + log_error
            entry = _read_json(_entry_path(project_path, run_id, entry_id), "entry ledger")
            status = "UNVERIFIED" if cleanup_error else "FAILED"
            entry.update({"status": status, "finished_at": _stamp(), "error": error,
                          "cleanup_status": "UNVERIFIED" if cleanup_error else "CONFIRMED"})
            _validate_entry(entry, project_path, run_id, entry_id)
            _write_json(_entry_path(project_path, run_id, entry_id), entry)
            if not summary:
                return {**callback, "status": status, "error": error}
            return _process_result(status, run_id, entry_id, owner, purpose,
                                   int(proc.pid) if proc is not None else wrapper_pid, None,
                                   error, summary=summary, logs=log_paths,
                                   complete=False)

    buffers = None if summary else [bytearray(), bytearray()]
    captured = [0, 0]
    stream_errors: list[str | None] = [None, None]
    guard = threading.Lock()
    overflow = threading.Event()
    write_failed = threading.Event()
    log_path_list = [Path(log_paths[name]) for name in ("stdout", "stderr")] if summary else None
    threads = [
        threading.Thread(target=_drain,
                         args=(pipe, buffers, index, guard, overflow),
                         kwargs={"log_handles": log_handles, "log_paths": log_path_list,
                                 "captured": captured, "stream_error": stream_errors,
                                 "write_failed": write_failed}, daemon=True)
        for index, pipe in enumerate((proc.stdout, proc.stderr))
    ]
    for thread in threads:
        thread.start()
    try:
        try:
            proc.stdin.close()
        except OSError:
            pass
        deadline = time.monotonic() + timeout
        timed_out = False
        snapshot_state = "PENDING"
        while True:
            snapshot_state = _record_snapshot(
                project_path, run_id, entry_id, int(proc.pid), process["identity"]
            ) if process else "OK"
            code = proc.poll()
            if code is not None:
                break
            if overflow.is_set() or write_failed.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(POLL_INTERVAL)
        cleanup_error = _stop(proc)
        for thread in threads:
            thread.join(timeout=1)
        drain_error = "command streams did not stop after cleanup" if any(
            thread.is_alive() for thread in threads
        ) else None
        log_error = _close_summary_logs(log_handles, log_paths) if summary else None
        log_handles = None
        code = proc.returncode if proc.returncode is not None else proc.poll()
        if cleanup_error:
            status = "UNVERIFIED"
            error = cleanup_error
        elif drain_error:
            status = "UNVERIFIED"
            error = drain_error
        elif timed_out:
            status = "TIMEOUT"
            error = "process timed out"
        elif overflow.is_set():
            status = "FAILED"
            error = "process output exceeded bound"
        elif write_failed.is_set():
            status = "FAILED"
            error = next((item for item in stream_errors if item), "summary log write failed")
        elif log_error:
            status = "FAILED"
            error = log_error
        elif code == 0:
            status = "SUCCESS"
            error = None
        else:
            status = "FAILED"
            error = "process exited with non-zero status"
        extra_log_error = next((item for item in stream_errors if item), None) or log_error
        if extra_log_error and error and extra_log_error not in error:
            error += "; " + extra_log_error
        update = {"status": status, "finished_at": _stamp(), "exit_code": code, "error": error,
                  "cleanup_status": "UNVERIFIED" if cleanup_error or drain_error else "CONFIRMED"}
        updated, _run, _entry = _update_entry(project_path, run_id, entry_id, update, allow_closing=True)
        if not updated:
            status = "UNVERIFIED"
            error = "run closed while command was executing"
        return _process_result(
            status, run_id, entry_id, owner, purpose, int(proc.pid), code, error,
            summary=summary, logs=log_paths, captured=captured,
            complete=(not overflow.is_set() and not write_failed.is_set() and
                      not cleanup_error and not drain_error and not log_error),
            buffers=buffers,
        )
    except BaseException:
        cleanup_error = _stop(proc)
        log_error = _close_summary_logs(log_handles, log_paths) if summary else None
        log_handles = None
        error = cleanup_error or log_error or "process execution failed"
        _update_entry(project_path, run_id, entry_id, {"status": "UNVERIFIED", "finished_at": _stamp(), "error": cleanup_error or "process execution failed"}, allow_closing=True)
        raise


def _retain_ids(retain: object) -> set[str]:
    if retain is None:
        return set()
    if isinstance(retain, (str, bytes)):
        raise ProcessRegistryError("retain must be an iterable of entry ids")
    try:
        values = list(retain)
    except TypeError:
        raise ProcessRegistryError("retain must be an iterable of entry ids") from None
    result = set()
    for value in values:
        result.add(_entry_id(value))
    return result


def check_run(project: str | Path, run_id: str, *, retain: tuple[str, ...] = tuple()) -> dict:
    project_path = _project(project)
    run_id = _run_id(run_id)
    wanted = _retain_ids(retain)
    with _run_lock(project_path, run_id):
        run = _read_run(project_path, run_id)
        entries = _read_entries(project_path, run_id)
    if not wanted:
        wanted = set(run.get("retained", []))
    known = {entry["entry_id"] for entry in entries}
    unknown = sorted(wanted - known)
    if unknown:
        raise ProcessRegistryError("unknown retain entry id")
    return _inspect(project_path, run, entries, wanted)


def _signal_record(record: dict) -> tuple[bool, str | None]:
    state, _current = _observe(record)
    if state == "gone":
        return True, None
    if state != "match":
        return False, "{} process identity {}".format(record["pid"], state)
    pid = int(record["pid"])
    if os.name == "nt":
        kernel = None
        handle = None
        try:
            kernel = _windows_kernel()
            handle = kernel.OpenProcess(0x101001, False, pid)  # TERMINATE | QUERY_LIMITED_INFORMATION
            if not handle:
                error = _ctypes.get_last_error()
                return (True, None) if error == 87 else (False, "process termination handle unavailable")
            # Revalidate creation time on the very handle that will receive the
            # termination request.  The earlier _observe call is advisory and
            # leaves a PID-reuse window if it is used on its own.
            creation = _windows_creation_time(kernel, handle)
            if creation is None:
                return False, "process termination identity unavailable"
            if creation != record["identity"].get("creation_time"):
                return False, "process identity mismatch"
            exited = _windows_has_exited(kernel, handle)
            if exited is None:
                return False, "process termination state unavailable"
            if exited:
                return True, None
            ok = kernel.TerminateProcess(handle, 1)
            if not ok:
                return False, "process termination failed"
        except (AttributeError, OSError, TypeError):
            return False, "process termination unavailable"
        finally:
            if handle:
                try:
                    kernel.CloseHandle(handle)
                except (AttributeError, OSError, TypeError):
                    pass
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True, None
        except (OSError, PermissionError):
            return False, "process termination failed"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        state, _current = _observe(record)
        if state == "gone":
            return True, None
        if state == "mismatch":
            return False, "process identity changed during cleanup"
        if state == "unknown":
            return False, "process termination not verifiable"
        time.sleep(0.02)
    return False, "process did not stop"


def _kill_entry(entry: dict, *, persist=None) -> tuple[bool, str | None]:
    # Keep the launcher's supervisor alive while it handles controller EOF.
    # Remember a fresh tree first, stop the wrapper, then independently clean
    # each still-live registered root/descendant. No name or global PID sweep.
    records = list(entry["descendants"])
    process = entry.get("process")
    if process is None and entry["status"] == "STARTING":
        return False, "launcher exited before command registration; cleanup unverified"
    if process:
        records.insert(0, process)
    errors = []
    fresh = []
    for record in records:
        state, _ = _observe(record)
        if state in {"unknown", "mismatch"}:
            errors.append("registered identity " + state)
        elif state == "match":
            snapshot_state, children = _snapshot_descendants(record["pid"], record["identity"])
            if snapshot_state == "UNVERIFIED" and _observe(record)[0] != "gone":
                errors.append("descendant snapshot unavailable")
            fresh.extend(children)
    # Persist newly observed descendants before stopping their parent. If the
    # cleanup command is interrupted, its next invocation still knows them.
    remembered = {row["pid"]: row for row in entry["descendants"]}
    for row in fresh:
        prior = remembered.get(row["pid"])
        if prior and _identity_matches(prior["identity"], row["identity"]) is not True:
            errors.append("descendant identity changed before cleanup")
        else:
            remembered.setdefault(row["pid"], row)
    if persist is not None:
        persist({"descendants": sorted(remembered.values(), key=lambda row: row["pid"])})
    wrapper = entry["wrapper"]
    if wrapper["pid"] == os.getpid():
        return False, "cleanup must run in a separate process from the registered launcher"
    ok, error = _signal_record(wrapper)
    if not ok:
        errors.append(error)
    # Give the existing supervisor/Windows Job a bounded chance to reap.
    deadline = time.monotonic() + 3
    while process and _observe(process)[0] == "match" and time.monotonic() < deadline:
        time.sleep(.02)
    unique = {(row["pid"], _platform.encode(row["identity"])): row for row in records + fresh}
    for record in unique.values():
        state, _ = _observe(record)
        if state == "match" and os.name != "nt":
            try:
                snapshot = _darwin_snapshot() if sys.platform == "darwin" else _tree._linux_snapshot
                tracker = _tree.Tracker(record["pid"], snapshot,
                    original_parent=sys.platform == "darwin", original_versions=False)
                if not _raw_birth_matches(record["identity"], tracker.known.get(record["pid"])):
                    errors.append("process identity changed before cleanup")
                    continue
                tracker.cleanup()
            except (OSError, RuntimeError) as exc:
                # An exit between observe and tracker construction is normal.
                if _observe(record)[0] != "gone":
                    errors.append(str(exc))
        ok, error = _signal_record(record)
        if not ok:
            errors.append(error)
    return not errors, "; ".join(str(error) for error in errors)[:MAX_TEXT] or None


def cleanup_run(project: str | Path, run_id: str, *, retain: tuple[str, ...] = tuple()) -> dict:
    project_path = _project(project)
    run_id = _run_id(run_id)
    wanted = _retain_ids(retain)
    with _run_lock(project_path, run_id):
        run = _read_run(project_path, run_id)
        entries = _read_entries(project_path, run_id)
        known = {entry["entry_id"] for entry in entries}
        unknown = sorted(wanted - known)
        if unknown:
            raise ProcessRegistryError("unknown retain entry id")
        run["status"] = "CLOSING"
        _write_json(_run_path(project_path, run_id), run)

    cleaned = []
    errors = []
    retained = sorted(wanted)
    for entry in entries:
        if entry["entry_id"] in wanted:
            continue
        ok, error = _kill_entry(entry, persist=lambda update: _update_entry(
            project_path, run_id, entry["entry_id"], update, allow_closing=True))
        if ok:
            cleaned.append(entry["entry_id"])
        elif error:
            errors.append({"entry_id": entry["entry_id"], "error": error})
        try:
            _update_entry(project_path, run_id, entry["entry_id"], {"cleanup_status": "CONFIRMED" if ok else "UNVERIFIED"}, allow_closing=True)
        except ProcessRegistryError as update_error:
            errors.append({"entry_id": entry["entry_id"], "error": str(update_error)})

    with _run_lock(project_path, run_id):
        run = _read_run(project_path, run_id)
        entries = _read_entries(project_path, run_id)
        inspection = _inspect(project_path, run, entries, wanted, closing_unverified=False)
        if errors or inspection["status"] == "UNVERIFIED":
            final_status = "UNVERIFIED"
        elif wanted:
            final_status = "RETAINED"
        elif inspection["status"] == "CLEAN":
            final_status = "CLEAN"
        else:
            final_status = "UNVERIFIED"
        run.update({"status": "CLOSED", "closed_at": _stamp(), "result_status": final_status, "retained": retained})
        _write_json(_run_path(project_path, run_id), run)
    inspection["status"] = final_status
    inspection["cleaned"] = sorted(cleaned)
    inspection["retained"] = retained
    inspection["issues"] = list(inspection.get("issues", [])) + errors
    return inspection


__all__ = ["ProcessRegistryError", "init_run", "run_process", "check_run", "cleanup_run"]
