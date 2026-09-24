"""Independent process-registry checks using the public CLI and owned fixtures."""

from __future__ import annotations

import json
import io
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from codex_adaptive_agents import process_registry
from codex_adaptive_agents.platform import atomic, encode


ENTRY = Path(__file__).resolve().parents[1] / "codex-adaptive-agents.py"
PYTHON = sys.executable


class ProcessRegistryTests(unittest.TestCase):
    """Exercise the external launcher boundary, including incomplete ledgers."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="codex-adaptive-agents-process-registry-")
        self.project = Path(self._temporary.name) / "MiXeDProject"
        self.project.mkdir()
        self.project = self.project.resolve()
        self._run_ids = []
        self._wrappers = []
        self._handles = []
        self._unrelated = []

    def tearDown(self):
        for proc in self._wrappers + self._unrelated:
            self._stop_process(proc)
        for handle in self._handles:
            try:
                handle.close()
            except OSError:
                pass
        for run_id in self._run_ids:
            try:
                self._invoke("process-cleanup", "--run-id", run_id, expected=None)
            except Exception:
                pass
        self._temporary.cleanup()

    @staticmethod
    def _stop_process(proc):
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _invoke(self, command, *options, expected=0, timeout=30, project=None):
        target_project = self.project if project is None else Path(project)
        args = [PYTHON, "-I", "-B", str(ENTRY), command,
                "--project", str(target_project), *map(str, options)]
        completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=timeout)
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError as error:
            self.fail({"command": args, "returncode": completed.returncode,
                       "stdout": stdout, "stderr": stderr, "error": str(error)})
        if expected is not None:
            self.assertEqual(completed.returncode, expected,
                             {"command": args, "result": value, "stderr": stderr})
        return value

    def _case_alias(self):
        alias = Path(str(self.project).lower())
        if str(alias) == str(self.project):
            self.skipTest("test fixture has no mixed-case path component")
        try:
            same_directory = os.path.samefile(self.project, alias)
        except (OSError, ValueError):
            same_directory = False
        if not same_directory:
            self.skipTest("case-insensitive filesystem alias is unavailable")
        return alias

    def _init_run(self):
        result = self._invoke("process-init")
        self.assertEqual(result["status"], "STARTED")
        run_id = result["run_id"]
        self.assertRegex(run_id, r"^[0-9a-f]{32}$")
        self._run_ids.append(run_id)
        return run_id

    def _run_command(self, run_id, command, *, timeout=10, expected=0, summary=False):
        options = ["--run-id", run_id, "--owner", "process-registry-tests",
                  "--purpose", "isolated process handoff regression", "--timeout", timeout]
        if summary:
            options.append("--summary")
        return self._invoke(
            "process-run", *options, "--", *command, expected=expected,
            timeout=max(30, timeout + 15),
        )

    @staticmethod
    def _read_json(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _start_live(self, name="live"):
        run_id = self._init_run()
        marker = self.project / (name + ".started")
        stderr_path = self.project / (name + ".stderr")
        stdout_path = self.project / (name + ".stdout")
        command = (
            "from pathlib import Path; import os,time; "
            f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(120)"
        )
        stdout = stdout_path.open("wb")
        stderr = stderr_path.open("wb")
        self._handles.extend((stdout, stderr))
        proc = subprocess.Popen(
            [PYTHON, "-I", "-B", str(ENTRY), "process-run",
             "--project", str(self.project), "--run-id", run_id,
             "--owner", "live-fixture", "--purpose", "long-lived test fixture",
             "--timeout", "100", "--", PYTHON, "-I", "-B", "-c", command],
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
        )
        self._wrappers.append(proc)
        deadline = time.monotonic() + 15
        event = None
        entry = None
        while time.monotonic() < deadline:
            event = self._read_json(stderr_path)
            if marker.exists() and event and event.get("entry_id"):
                entry_id = event["entry_id"]
                entry_path = self.project / ".codex-adaptive-agents" / "processes" / run_id / (entry_id + ".json")
                entry = self._read_json(entry_path)
                if entry and entry.get("process"):
                    return {
                        "run_id": run_id,
                        "entry_id": entry_id,
                        "entry_path": entry_path,
                        "entry": entry,
                        "proc": proc,
                    }
            if proc.poll() is not None:
                self.fail({"fixture": name, "returncode": proc.returncode,
                           "event": event, "stderr": stderr_path.read_text(errors="replace"),
                           "stdout": stdout_path.read_text(errors="replace")})
            time.sleep(0.05)
        self.fail({"fixture": name, "event": event, "marker": marker.exists()})

    @staticmethod
    def _pid_exists(pid):
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def test_init_empty_and_unknown_run_are_rejected(self):
        run_id = self._init_run()
        run_path = self.project / ".codex-adaptive-agents" / "processes" / run_id / "run.json"
        run_doc = self._read_json(run_path)
        self.assertEqual(run_doc["entry_ids"], [])
        self.assertEqual(self._invoke("process-check", "--run-id", run_id)["status"], "CLEAN")

        unknown = self._invoke("process-check", "--run-id", "f" * 32, expected=1)
        self.assertEqual(unknown["status"], "ERROR")

    def test_cli_normal_failure_and_timeout_lifecycles(self):
        cases = (
            ([PYTHON, "-I", "-B", "-c", "print('registry-ok')"], 5, "SUCCESS"),
            ([PYTHON, "-I", "-B", "-c", "raise SystemExit(7)"], 5, "FAILED"),
            ([PYTHON, "-I", "-B", "-c", "import time; time.sleep(5)"], 0.2, "TIMEOUT"),
        )
        for command, timeout, expected_status in cases:
            with self.subTest(status=expected_status):
                run_id = self._init_run()
                result = self._run_command(run_id, command, timeout=timeout,
                                           expected=0 if expected_status == "SUCCESS" else 1)
                self.assertEqual(result["status"], expected_status)
                if expected_status == "SUCCESS":
                    self.assertIn("registry-ok", result["stdout"])
                checked = self._invoke("process-check", "--run-id", run_id)
                self.assertEqual(checked["status"], "CLEAN")
                cleaned = self._invoke("process-cleanup", "--run-id", run_id)
                self.assertEqual(cleaned["status"], "CLEAN")

    def test_summary_keeps_verbose_output_in_private_bounded_logs(self):
        run_id = self._init_run()
        command = [PYTHON, "-I", "-B", "-c",
                   "import sys; sys.stdout.buffer.write(b'O'*50000); sys.stderr.buffer.write(b'E'*30000)"]
        result = self._run_command(run_id, command, summary=True)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertNotIn("stdout", result)
        self.assertNotIn("stderr", result)
        self.assertLess(len(json.dumps(result).encode("utf-8")), 4096)
        self.assertTrue(result["output_complete"])
        self.assertEqual(result["captured_bytes"], {"stdout": 50000, "stderr": 30000})
        run_dir = self.project / ".codex-adaptive-agents" / "processes" / run_id
        for name, expected in (("stdout", b"O" * 50000), ("stderr", b"E" * 30000)):
            path = Path(result["logs"][name])
            self.assertTrue(path.is_absolute())
            self.assertEqual(path.read_bytes(), expected)
            self.assertTrue(path.parent.parent.samefile(run_dir))
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self._invoke("process-check", "--run-id", run_id)["status"], "CLEAN")

    def test_summary_reports_nonzero_timeout_overflow_and_log_write_failure(self):
        run_id = self._init_run()
        failed = self._run_command(
            run_id, [PYTHON, "-I", "-B", "-c",
                     "import sys; sys.stderr.write('failure-evidence'); raise SystemExit(7)"],
            summary=True, expected=1,
        )
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["exit_code"], 7)
        self.assertTrue(failed["output_complete"])
        self.assertEqual(Path(failed["logs"]["stderr"]).read_text(), "failure-evidence")

        timed = self._run_command(
            run_id, [PYTHON, "-I", "-B", "-c",
                     "import sys,time; print('before-timeout',flush=True); time.sleep(5)"],
            timeout=0.25, summary=True, expected=1,
        )
        self.assertEqual(timed["status"], "TIMEOUT")
        self.assertIn(b"before-timeout", Path(timed["logs"]["stdout"]).read_bytes())

        overflow = self._run_command(
            run_id, [PYTHON, "-I", "-B", "-c",
                     f"import sys; sys.stdout.buffer.write(b'X'*{process_registry.MAX_OUTPUT_BYTES + 16384})"],
            summary=True, expected=1,
        )
        self.assertEqual(overflow["status"], "FAILED")
        self.assertIn("exceeded bound", overflow["error"])
        self.assertFalse(overflow["output_complete"])
        stdout_path = Path(overflow["logs"]["stdout"])
        self.assertLessEqual(stdout_path.stat().st_size, process_registry.MAX_OUTPUT_BYTES)
        self.assertEqual(stdout_path.stat().st_size, overflow["captured_bytes"]["stdout"])
        self.assertEqual(self._invoke("process-check", "--run-id", run_id)["status"], "CLEAN")

        write_run = self._init_run()
        bootstrap = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from unittest.mock import patch
from codex_adaptive_agents import process_registry as registry
project, run_id, python = sys.argv[2:5]
def partial_write(handle, chunk, on_write=None):
    amount = min(3, len(chunk))
    written = handle.write(memoryview(chunk)[:amount])
    if on_write is not None:
        on_write(written)
    raise OSError(28, "No space left on device")
with patch.object(registry, "_write_all", side_effect=partial_write):
    result = registry.run_process(project, run_id, "process-registry-tests",
        "summary write failure", [python, "-I", "-B", "-c", "print('write-me')"],
        summary=True)
print(json.dumps(result))
'''
        failure_runner = subprocess.run(
            [PYTHON, "-I", "-B", "-c", bootstrap, str(ENTRY.parent),
             str(self.project), write_run, PYTHON],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        self.assertEqual(failure_runner.returncode, 0, failure_runner.stderr.decode(errors="replace"))
        write_error = json.loads(failure_runner.stdout)
        self.assertEqual(write_error["status"], "FAILED")
        self.assertIn("summary log write failed", write_error["error"])
        self.assertIn("No space left on device", write_error["error"])
        self.assertFalse(write_error["output_complete"])
        self.assertEqual(write_error["captured_bytes"]["stdout"], 3)
        self.assertEqual(Path(write_error["logs"]["stdout"]).read_bytes(), b"wri")

    def test_summary_log_paths_reject_symlink_and_hardlink_tampering(self):
        run_id = self._init_run()
        run_dir = self.project / ".codex-adaptive-agents" / "processes" / run_id
        outside = self.project / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_bytes(b"preserve")
        symlink_entry = "a" * 32
        try:
            os.symlink(outside, run_dir / symlink_entry, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable")
        with self.assertRaises(process_registry.ProcessRegistryError):
            process_registry._prepare_summary_logs(self.project, run_id, symlink_entry)
        self.assertEqual(sentinel.read_bytes(), b"preserve")

        hardlink_entry = "b" * 32
        paths, handles = process_registry._prepare_summary_logs(self.project, run_id, hardlink_entry)
        external_link = self.project / "outside-linked-log"
        os.link(paths["stdout"], external_link)
        captured = [0, 0]
        stream_errors = [None, None]
        write_failed = threading.Event()
        process_registry._drain(
            io.BytesIO(b"must not write through linked log"), None, 0,
            threading.Lock(), threading.Event(),
            log_handles=handles,
            log_paths=[Path(paths[name]) for name in ("stdout", "stderr")],
            captured=captured, stream_error=stream_errors, write_failed=write_failed,
        )
        self.assertTrue(write_failed.is_set())
        self.assertIn("hard link", stream_errors[0])
        self.assertEqual(captured[0], 0)
        self.assertEqual(external_link.read_bytes(), b"")
        self.assertIn("integrity", process_registry._close_summary_logs(handles, paths))

    def test_full_prelaunch_failures_keep_legacy_return_shape(self):
        run_ids = [self._init_run() for _ in range(3)]
        bootstrap = '''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from unittest.mock import patch
from codex_adaptive_agents import process_registry as registry
project, callback_run, spawn_run, identity_run, python = sys.argv[2:7]
def command(): return [python, "-I", "-B", "-c", "import time; time.sleep(5)"]
def fail_callback(_): raise RuntimeError("callback probe")
results = [registry.run_process(project, callback_run, "compat", "callback failure",
    command(), on_start=fail_callback)]
with patch.object(registry._platform, "spawn", side_effect=RuntimeError("spawn probe")):
    results.append(registry.run_process(project, spawn_run, "compat", "spawn failure", command()))
real_identity = registry._now_identity
def unavailable_child(pid):
    return real_identity(pid) if pid == os.getpid() else ("unknown", None)
with patch.object(registry, "_now_identity", side_effect=unavailable_child):
    results.append(registry.run_process(project, identity_run, "compat", "identity failure", command()))
print(json.dumps(results))
'''
        compatibility = subprocess.run(
            [PYTHON, "-I", "-B", "-c", bootstrap, str(ENTRY.parent),
             str(self.project), *run_ids, PYTHON],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        self.assertEqual(compatibility.returncode, 0, compatibility.stderr.decode(errors="replace"))
        results = json.loads(compatibility.stdout)
        expected_keys = {"status", "run_id", "entry_id", "owner", "purpose", "pid",
                         "identity", "started_at", "error"}
        for result in results:
            self.assertEqual(set(result), expected_keys)
            self.assertIn("identity", result)
            self.assertIn("started_at", result)
        self.assertEqual([result["status"] for result in results], ["FAILED", "FAILED", "UNVERIFIED"])
        self.assertEqual(results[1]["error"], "process could not start")
        self.assertIn("identity unavailable", results[2]["error"])

    def test_same_run_concurrent_entries_keep_manifest_complete(self):
        run_id = self._init_run()
        processes = []
        try:
            for index in range(4):
                command = [PYTHON, "-I", "-B", "-c",
                           f"import time; print('entry-{index}'); time.sleep(.25)"]
                processes.append(subprocess.Popen(
                    [PYTHON, "-I", "-B", str(ENTRY), "process-run",
                     "--project", str(self.project), "--run-id", run_id,
                     "--owner", f"concurrent-{index}", "--purpose", "manifest race",
                     "--timeout", "10", "--", *command],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                ))
            results = []
            for proc in processes:
                stdout, stderr = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 0,
                                 {"stdout": stdout.decode(errors="replace"),
                                  "stderr": stderr.decode(errors="replace")})
                results.append(json.loads(stdout))
        finally:
            for proc in processes:
                self._stop_process(proc)
        self.assertEqual({result["status"] for result in results}, {"SUCCESS"})
        entry_ids = [result["entry_id"] for result in results]
        self.assertEqual(len(set(entry_ids)), 4)
        run_path = self.project / ".codex-adaptive-agents" / "processes" / run_id / "run.json"
        run_doc = self._read_json(run_path)
        self.assertEqual(set(run_doc["entry_ids"]), set(entry_ids))
        self.assertEqual(self._invoke("process-check", "--run-id", run_id)["status"], "CLEAN")

    def test_killed_wrapper_is_cleaned_without_becoming_business_success(self):
        live = self._start_live("wrapper-kill")
        active = self._invoke("process-check", "--run-id", live["run_id"], expected=1)
        self.assertNotIn(active["status"], {"CLEAN", "RETAINED"})
        self._stop_process(live["proc"])
        cleaned = self._invoke("process-cleanup", "--run-id", live["run_id"])
        self.assertEqual(cleaned["status"], "CLEAN")
        detail = next(item for item in cleaned["entries"] if item["entry_id"] == live["entry_id"])
        self.assertFalse(detail["completion_reported"])
        self.assertNotEqual(detail["status"], "SUCCESS")
        checked = self._invoke("process-check", "--run-id", live["run_id"])
        self.assertEqual(checked["status"], "CLEAN")
        self.assertIn(live["entry_id"], checked["unreported_entries"])

    def test_retain_then_later_cleanup_releases_the_entry(self):
        live = self._start_live("retain")
        unknown = self._invoke("process-cleanup", "--run-id", live["run_id"],
                              "--retain", "a" * 32, expected=1)
        self.assertEqual(unknown["status"], "ERROR")
        retained = self._invoke("process-cleanup", "--run-id", live["run_id"],
                                "--retain", live["entry_id"])
        self.assertEqual(retained["status"], "RETAINED")
        self.assertIsNone(live["proc"].poll())
        retained_check = self._invoke("process-check", "--run-id", live["run_id"])
        self.assertEqual(retained_check["status"], "RETAINED")
        cleaned = self._invoke("process-cleanup", "--run-id", live["run_id"])
        self.assertEqual(cleaned["status"], "CLEAN")

    def test_cleanup_keeps_other_run_and_unrelated_process_alive(self):
        first = self._start_live("first-run")
        second = self._start_live("second-run")
        unrelated = subprocess.Popen([PYTHON, "-I", "-B", "-c", "import time; time.sleep(120)"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self._unrelated.append(unrelated)
        cleaned = self._invoke("process-cleanup", "--run-id", first["run_id"])
        self.assertEqual(cleaned["status"], "CLEAN")
        self.assertIsNone(second["proc"].poll())
        self.assertIsNone(unrelated.poll())
        self.assertNotEqual(self._invoke("process-check", "--run-id", second["run_id"], expected=1)["status"], "CLEAN")
        self.assertEqual(self._invoke("process-cleanup", "--run-id", second["run_id"])["status"], "CLEAN")

    def test_identity_mismatch_is_not_signalled_and_failed_cleanup_stays_visible(self):
        live = self._start_live("identity-mismatch")
        # The live wrapper also reads this ledger.  Inject the stale identity
        # under its run lock so Windows does not reject replacing an open file.
        with process_registry._run_lock(self.project, live["run_id"]):
            entry = self._read_json(live["entry_path"])
            self.assertIsNotNone(entry)
            process = dict(entry["process"])
            identity = dict(process["identity"])
            if identity["kind"] == "linux":
                identity["start_time"] += 1
            elif identity["kind"] == "darwin":
                identity["identity"] += 1
            elif identity["kind"] == "windows":
                identity["creation_time"] += 1
            else:
                self.skipTest("unsupported process identity kind")
            process["identity"] = identity
            entry["process"] = process
            atomic(live["entry_path"], encode(entry))

        result = self._invoke("process-cleanup", "--run-id", live["run_id"], expected=1)
        self.assertEqual(result["status"], "UNVERIFIED")
        checked = self._invoke("process-check", "--run-id", live["run_id"], expected=1)
        self.assertNotEqual(checked["status"], "CLEAN")

        # The low-level signal path must reject the stale birth token before
        # touching the PID.  This owns only the current test process.
        current_state, current_identity = process_registry._now_identity(os.getpid())
        self.assertEqual(current_state, "live")
        stale = dict(current_identity)
        if stale["kind"] == "linux":
            stale["start_time"] += 1
        elif stale["kind"] == "darwin":
            stale["identity"] += 1
        else:
            stale["creation_time"] += 1
        with patch.object(process_registry.os, "kill") as kill:
            ok, error = process_registry._signal_record({"pid": os.getpid(), "identity": stale})
        self.assertFalse(ok)
        self.assertTrue(error)
        kill.assert_not_called()

    def test_darwin_exec_version_change_keeps_unique_process_identity(self):
        expected = {"kind": "darwin", "boot_id": "boot", "identity": 41, "version": 3}
        after_exec = {"kind": "darwin", "boot_id": "boot", "identity": 41, "version": 4}
        self.assertTrue(process_registry._identity_matches(expected, after_exec))
        reused = dict(after_exec, identity=42)
        self.assertFalse(process_registry._identity_matches(expected, reused))

    def test_tracker_scalar_birth_tokens_are_not_treated_as_snapshot_tuples(self):
        linux = {"kind": "linux", "boot_id": "boot", "start_time": 123}
        darwin = {"kind": "darwin", "boot_id": "boot", "identity": 456, "version": 1}
        self.assertTrue(process_registry._raw_birth_matches(linux, 123))
        self.assertTrue(process_registry._raw_birth_matches(linux, (123, 7)))
        self.assertFalse(process_registry._raw_birth_matches(linux, 124))
        self.assertTrue(process_registry._raw_birth_matches(darwin, 456))
        self.assertFalse(process_registry._raw_birth_matches(darwin, 457))

    def test_missing_entry_file_cannot_be_reported_clean(self):
        run_id = self._init_run()
        result = self._run_command(run_id, [PYTHON, "-I", "-B", "-c", "print('entry')"])
        self.assertEqual(result["status"], "SUCCESS")
        run_dir = self.project / ".codex-adaptive-agents" / "processes" / run_id
        run_doc = self._read_json(run_dir / "run.json")
        self.assertEqual(len(run_doc["entry_ids"]), 1)
        (run_dir / (run_doc["entry_ids"][0] + ".json")).unlink()
        rejected = self._invoke("process-check", "--run-id", run_id, expected=1)
        self.assertEqual(rejected["status"], "ERROR")
        self.assertNotEqual(self._invoke("process-cleanup", "--run-id", run_id, expected=1)["status"], "CLEAN")

    def test_case_alias_check_cleanup_active_command(self):
        alias = self._case_alias()
        live = self._start_live("case-alias-active")

        active = self._invoke("process-check", "--run-id", live["run_id"],
                              project=alias, expected=1)
        self.assertEqual(active["status"], "DIRTY")
        cleaned = self._invoke("process-cleanup", "--run-id", live["run_id"],
                               project=alias)
        self.assertEqual(cleaned["status"], "CLEAN")
        checked = self._invoke("process-check", "--run-id", live["run_id"],
                               project=alias)
        self.assertEqual(checked["status"], "CLEAN")

    def test_existing_run_can_start_again_through_case_alias(self):
        alias = self._case_alias()
        run_id = self._init_run()
        result = self._invoke(
            "process-run", "--run-id", run_id, "--owner", "case-alias-tests",
            "--purpose", "start an existing run through a case alias", "--timeout", 10,
            "--", PYTHON, "-I", "-B", "-c", "print('case-alias-ok')",
            project=alias,
        )
        self.assertEqual(result["status"], "SUCCESS")
        self.assertIn("case-alias-ok", result["stdout"])
        self.assertEqual(self._invoke("process-check", "--run-id", run_id,
                                      project=alias)["status"], "CLEAN")
        self.assertEqual(self._invoke("process-cleanup", "--run-id", run_id,
                                      project=alias)["status"], "CLEAN")

    def test_copied_ledger_for_different_project_is_rejected(self):
        run_id = self._init_run()
        result = self._run_command(run_id, [PYTHON, "-I", "-B", "-c", "print('ledger-copy')"])
        self.assertEqual(result["status"], "SUCCESS")

        other_temporary = tempfile.TemporaryDirectory(prefix="codex-adaptive-agents-process-registry-copy-")
        self.addCleanup(other_temporary.cleanup)
        other = Path(other_temporary.name).resolve()
        source_dir = self.project / ".codex-adaptive-agents" / "processes" / run_id
        copied_dir = other / ".codex-adaptive-agents" / "processes" / run_id
        copied_dir.parent.mkdir(parents=True)
        shutil.copytree(source_dir, copied_dir)

        rejected = self._invoke("process-check", "--run-id", run_id,
                                project=other, expected=1)
        self.assertEqual(rejected["status"], "ERROR")

        copied_run = self._read_json(copied_dir / "run.json")
        self.assertIsNotNone(copied_run)
        copied_run["project"] = str(other)
        atomic(copied_dir / "run.json", encode(copied_run))
        rejected_entry = self._invoke("process-check", "--run-id", run_id,
                                      project=other, expected=1)
        self.assertEqual(rejected_entry["status"], "ERROR")

    def test_legacy_entry_without_project_remains_bound_to_validated_run(self):
        completed_run = self._init_run()
        completed = self._run_command(
            completed_run, [PYTHON, "-I", "-B", "-c", "print('legacy-completed')"]
        )
        self.assertEqual(completed["status"], "SUCCESS")
        completed_entry = self._read_json(
            self.project / ".codex-adaptive-agents" / "processes" / completed_run
            / (completed["entry_id"] + ".json")
        )
        completed_entry.pop("project", None)
        atomic(
            self.project / ".codex-adaptive-agents" / "processes" / completed_run
            / (completed["entry_id"] + ".json"),
            encode(completed_entry),
        )
        self.assertEqual(self._invoke("process-check", "--run-id", completed_run)["status"], "CLEAN")
        self.assertEqual(self._invoke("process-cleanup", "--run-id", completed_run)["status"], "CLEAN")

        active = self._start_live("legacy-active")
        with process_registry._run_lock(self.project, active["run_id"]):
            active_entry = self._read_json(active["entry_path"])
            active_entry.pop("project", None)
            atomic(active["entry_path"], encode(active_entry))
        active_check = self._invoke("process-check", "--run-id", active["run_id"], expected=1)
        self.assertEqual(active_check["status"], "DIRTY")
        self.assertEqual(self._invoke("process-cleanup", "--run-id", active["run_id"])["status"], "CLEAN")


if __name__ == "__main__":
    unittest.main()
