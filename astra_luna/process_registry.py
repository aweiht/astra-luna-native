"""Persistent, identity-checked process handoff records.

The registry is deliberately cooperative.  ``run_process`` records the
process-tree supervisor returned by :mod:`astra_luna.platform`, while
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
    return project / ".astra-luna" / PROCESS_ROOT


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
    if value.get("project") != str(project) or not isinstance(value.get("created_at"), str):
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
        "schema", "run_id", "entry_id", "owner", "purpose", "wrapper", "process",
        "descendants", "descendant_snapshot", "started_at", "finished_at", "status",
        "exit_code", "error", "cleanup_status",
    }
    if set(value) - allowed or value.get("schema") != SCHEMA or value.get("run_id") != run_id or value.get("entry_id") != entry_id:
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


def _drain(pipe, buffers: list[bytearray], index: int, guard: threading.Lock, overflow: threading.Event) -> None:
    try:
        while True:
            chunk = pipe.read1(16384)
            if not chunk:
                break
            with guard:
                if sum(map(len, buffers)) + len(chunk) > MAX_OUTPUT_BYTES:
                    overflow.set()
                    break
                buffers[index].extend(chunk)
    except (OSError, ValueError):
        pass
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


def run_process(project: str | Path, run_id: str, owner: str, purpose: str, argv: list[str] | tuple[str, ...], *, timeout: float = 600, on_start=None) -> dict:
    project_path = _project(project)
    run_id = _run_id(run_id)
    owner = _text(owner, "owner")
    purpose = _text(purpose, "purpose")
    args = _validate_argv(argv)
    timeout = _validate_timeout(timeout)
    if on_start is not None and not callable(on_start):
        raise ProcessRegistryError("on_start must be callable")
    wrapper_pid = os.getpid()
    wrapper_state, wrapper_identity = _now_identity(wrapper_pid)
    if wrapper_state != "live" or wrapper_identity is None or not wrapper_identity.get("boot_id", "") and wrapper_identity.get("kind") in {"linux", "darwin"}:
        raise ProcessRegistryError("launcher identity unavailable")
    wrapper = {"pid": wrapper_pid, "identity": wrapper_identity}
    entry_id = None
    proc = None
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
            return {**callback, "status": "FAILED", "error": "on_start callback failed"}
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
                return {**callback, "status": "UNVERIFIED", "error": update["error"]}
            process = None if already_exited else {"pid": int(proc.pid), "identity": process_identity}
            entry = _read_json(_entry_path(project_path, run_id, entry_id), "entry ledger")
            entry.update({"process": process, "status": "RUNNING"})
            _validate_entry(entry, project_path, run_id, entry_id)
            _write_json(_entry_path(project_path, run_id, entry_id), entry)
        except Exception:
            if proc is not None:
                _stop(proc)
            entry = _read_json(_entry_path(project_path, run_id, entry_id), "entry ledger")
            entry.update({"status": "FAILED", "finished_at": _stamp(), "error": "process could not start"})
            _validate_entry(entry, project_path, run_id, entry_id)
            _write_json(_entry_path(project_path, run_id, entry_id), entry)
            return {**callback, "status": "FAILED", "error": "process could not start"}

    buffers = [bytearray(), bytearray()]
    guard = threading.Lock()
    overflow = threading.Event()
    threads = [threading.Thread(target=_drain, args=(proc.stdout, buffers, 0, guard, overflow), daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, buffers, 1, guard, overflow), daemon=True)]
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
            if overflow.is_set():
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
        elif code == 0:
            status = "SUCCESS"
            error = None
        else:
            status = "FAILED"
            error = "process exited with non-zero status"
        update = {"status": status, "finished_at": _stamp(), "exit_code": code, "error": error,
                  "cleanup_status": "UNVERIFIED" if cleanup_error or drain_error else "CONFIRMED"}
        updated, _run, _entry = _update_entry(project_path, run_id, entry_id, update, allow_closing=True)
        if not updated:
            status = "UNVERIFIED"
            error = "run closed while command was executing"
        result = {
            "status": status,
            "run_id": run_id,
            "entry_id": entry_id,
            "owner": owner,
            "purpose": purpose,
            "pid": int(proc.pid),
            "exit_code": code,
            "stdout": bytes(buffers[0]).decode("utf-8", errors="replace"),
            "stderr": bytes(buffers[1]).decode("utf-8", errors="replace"),
        }
        if error:
            result["error"] = error
        return result
    except BaseException:
        cleanup_error = _stop(proc)
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
