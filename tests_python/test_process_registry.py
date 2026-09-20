"""Independent process-registry checks using the public CLI and owned fixtures."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from astra_luna import process_registry
from astra_luna.platform import atomic, encode


ENTRY = Path(__file__).resolve().parents[1] / "astra-luna.py"
PYTHON = sys.executable


class ProcessRegistryTests(unittest.TestCase):
    """Exercise the external launcher boundary, including incomplete ledgers."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="astra-luna-process-registry-")
        self.project = Path(self._temporary.name).resolve()
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

    def _invoke(self, command, *options, expected=0, timeout=30):
        args = [PYTHON, "-I", "-B", str(ENTRY), command,
                "--project", str(self.project), *map(str, options)]
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

    def _init_run(self):
        result = self._invoke("process-init")
        self.assertEqual(result["status"], "STARTED")
        run_id = result["run_id"]
        self.assertRegex(run_id, r"^[0-9a-f]{32}$")
        self._run_ids.append(run_id)
        return run_id

    def _run_command(self, run_id, command, *, timeout=10, expected=0):
        return self._invoke(
            "process-run", "--run-id", run_id, "--owner", "process-registry-tests",
            "--purpose", "isolated process handoff regression", "--timeout", timeout,
            "--", *command, expected=expected, timeout=max(30, timeout + 15),
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
                entry_path = self.project / ".astra-luna" / "processes" / run_id / (entry_id + ".json")
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
        run_path = self.project / ".astra-luna" / "processes" / run_id / "run.json"
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
        run_path = self.project / ".astra-luna" / "processes" / run_id / "run.json"
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
        entry = dict(live["entry"])
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
        run_dir = self.project / ".astra-luna" / "processes" / run_id
        run_doc = self._read_json(run_dir / "run.json")
        self.assertEqual(len(run_doc["entry_ids"]), 1)
        (run_dir / (run_doc["entry_ids"][0] + ".json")).unlink()
        rejected = self._invoke("process-check", "--run-id", run_id, expected=1)
        self.assertEqual(rejected["status"], "ERROR")
        self.assertNotEqual(self._invoke("process-cleanup", "--run-id", run_id, expected=1)["status"], "CLEAN")


if __name__ == "__main__":
    unittest.main()
