"""Offline policy tests for native capability and daily-cache boundaries."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_adaptive_agents import policy


UTC = timezone.utc
EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def unavailable_sources(*_args, **_kwargs):
    return []


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "codex home"
        self.home.mkdir()
        self.project = Path(self.temp.name).resolve() / "project"
        self.project.mkdir()
        self.now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    def snapshot(self, checked_at, *, codex=None, efforts=None):
        codex = codex or str(self.home / 'codex')
        resource = self.home / policy.RESOURCE
        resource.mkdir(exist_ok=True)
        value = {
            "schema": 1,
            "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
            "supported_efforts": efforts or EFFORTS,
            "root_model": "gpt-6-astra",
            "codex": codex,
            "cli_version": "codex-cli 0.147.0",
            "evidence": "public_client_catalogue",
        }
        path = resource / "capabilities.json"
        raw = json.dumps(value, sort_keys=True).encode("utf-8")
        path.write_bytes(raw)
        return path, raw

    def test_source_json_rejects_duplicates_and_bad_timestamps(self):
        with self.assertRaises(policy.PolicyError):
            policy.parse_modeldial(
                b'{"schemaVersion":"1.1","schemaVersion":"1.1","batch":{},"rankings":[]}'
            )
        with self.assertRaises(policy.PolicyError):
            policy.parse_deng(
                b'{"schema":3,"mode":"equal_latest_3","benchmark_id":"b",'
                b'"source_updated_at":"not-a-time","points":[]}'
            )

    def test_same_local_day_reuses_cache_without_refresh(self):
        calls = {"catalog": 0, "sources": 0}

        def catalog(*_args):
            calls["catalog"] += 1
            return list(EFFORTS)

        def sources(*_args, **_kwargs):
            calls["sources"] += 1
            return []

        with patch.dict(os.environ, {"TZ": "UTC"}, clear=False), \
                patch.object(policy, "DirectSupportedEfforts", side_effect=catalog), \
                patch.object(policy, "FetchSources", side_effect=sources):
            first = policy.select(self.home, self.project, now=self.now)
            second = policy.select(self.home, self.project,
                                   now=self.now + timedelta(hours=1))

        self.assertEqual(first["role"], "adaptive_luna_max")
        self.assertEqual(second["role"], "adaptive_luna_max")
        self.assertTrue(first["refreshed"])
        self.assertFalse(second["refreshed"])
        self.assertEqual(calls, {"catalog": 1, "sources": 1})

    def test_system_timezone_controls_natural_day(self):
        with patch.dict(os.environ, {"TZ": "Asia/Shanghai"}, clear=False), \
                patch.object(policy, "ZoneInfo", return_value=timezone(timedelta(hours=8))), \
                patch.object(policy, "DirectSupportedEfforts", return_value=list(EFFORTS)), \
                patch.object(policy, "FetchSources", return_value=[]):
            result = policy.select(
                self.home, self.project,
                now=datetime(2026, 9, 15, 16, 0, tzinfo=UTC),
            )

        self.assertEqual(result["timezone"], "Asia/Shanghai")
        self.assertEqual(result["day"], "2026-09-16")

    def test_fresh_snapshot_fallback_keeps_original_checked_at_and_stays_stale(self):
        checked_at = self.now - timedelta(hours=2)
        path, original = self.snapshot(checked_at)

        with patch.dict(os.environ, {"TZ": "UTC"}, clear=False), \
                patch.object(policy, "DirectSupportedEfforts",
                              side_effect=policy.PolicyError("catalog unavailable")), \
                patch.object(policy, "FetchSources", return_value=[]):
            result = policy.select(self.home, self.project, now=self.now)

        cache = json.loads((self.project / ".codex-adaptive-agents/state/policy-cache.json").read_text())
        self.assertEqual(result["role"], "adaptive_luna_max")
        self.assertEqual(result["status"], "fallback")
        self.assertTrue(result["stale"])
        self.assertEqual(cache["capabilities_at"], policy._stamp(checked_at))
        self.assertEqual(path.read_bytes(), original)

    def test_expired_snapshot_still_probes_direct_catalogue(self):
        path, original = self.snapshot(self.now - timedelta(hours=73))
        seen = []

        def catalog(home, codex):
            seen.append((home, codex))
            return list(EFFORTS)

        with patch.dict(os.environ, {"TZ": "UTC"}, clear=False), \
                patch.object(policy, "DirectSupportedEfforts", side_effect=catalog), \
                patch.object(policy, "FetchSources", return_value=[]):
            result = policy.select(self.home, self.project, now=self.now)

        self.assertEqual(result["role"], "adaptive_luna_max")
        self.assertEqual(seen, [(self.home, str(self.home / 'codex'))])
        self.assertEqual(result["capabilities_at"], policy._stamp(self.now))
        self.assertEqual(path.read_bytes(), original)

    def test_expired_snapshot_is_not_used_when_probe_times_out(self):
        path, original = self.snapshot(self.now - timedelta(hours=73))

        def slow_catalogue(*_args):
            time.sleep(0.1)
            return list(EFFORTS)

        with patch.dict(os.environ, {"TZ": "UTC"}, clear=False), \
                patch.object(policy, "SOURCE_TIMEOUT", 0.02), \
                patch.object(policy, "DirectSupportedEfforts", side_effect=slow_catalogue), \
                patch.object(policy, "FetchSources", return_value=[]):
            result = policy.select(self.home, self.project, now=self.now)

        self.assertEqual(result["role"], "")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["capabilities_at"], policy._stamp(None))
        self.assertEqual(path.read_bytes(), original)

    def test_future_snapshot_is_not_used_when_probe_times_out(self):
        path, original = self.snapshot(self.now + timedelta(hours=1))

        def slow_catalogue(*_args):
            time.sleep(0.1)
            return list(EFFORTS)

        with patch.dict(os.environ, {"TZ": "UTC"}, clear=False), \
                patch.object(policy, "SOURCE_TIMEOUT", 0.02), \
                patch.object(policy, "DirectSupportedEfforts", side_effect=slow_catalogue), \
                patch.object(policy, "FetchSources", return_value=[]):
            result = policy.select(self.home, self.project, now=self.now)

        self.assertEqual(result["role"], "")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["capabilities_at"], policy._stamp(None))
        self.assertEqual(path.read_bytes(), original)

    def test_timeout_uses_fresh_snapshot_without_extending_checked_at(self):
        checked_at = self.now - timedelta(hours=1)
        path, original = self.snapshot(checked_at)

        def slow_catalogue(*_args):
            time.sleep(0.1)
            return list(EFFORTS)

        with patch.dict(os.environ, {"TZ": "UTC"}, clear=False), \
                patch.object(policy, "SOURCE_TIMEOUT", 0.02), \
                patch.object(policy, "DirectSupportedEfforts", side_effect=slow_catalogue), \
                patch.object(policy, "FetchSources", return_value=[]):
            result = policy.select(self.home, self.project, now=self.now)

        self.assertEqual(result["role"], "adaptive_luna_max")
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["capabilities_at"], policy._stamp(checked_at))
        self.assertEqual(path.read_bytes(), original)

    def test_successful_probe_takes_precedence_over_fresh_snapshot(self):
        checked_at = self.now - timedelta(hours=1)
        self.snapshot(checked_at)

        with patch.dict(os.environ, {"TZ": "UTC"}, clear=False), \
                patch.object(policy, "DirectSupportedEfforts", return_value=list(EFFORTS)), \
                patch.object(policy, "FetchSources", return_value=[]):
            result = policy.select(self.home, self.project, now=self.now)

        cache = json.loads((self.project / ".codex-adaptive-agents/state/policy-cache.json").read_text())
        self.assertEqual(result["role"], "adaptive_luna_max")
        self.assertEqual(cache["capabilities_at"], policy._stamp(self.now))
        self.assertNotIn("used fresh public capability snapshot", " ".join(result["reasons"]))

    def test_missing_capability_blocks_and_does_not_invent_success(self):
        with patch.dict(os.environ, {"TZ": "UTC"}, clear=False), \
                patch.object(policy, "DirectSupportedEfforts",
                              side_effect=policy.PolicyError("catalog unavailable")), \
                patch.object(policy, "FetchSources", return_value=[]):
            result = policy.select(self.home, self.project, now=self.now)

        self.assertEqual(result["role"], "")
        self.assertEqual(result["effort"], "")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["stale"])
        cache = json.loads((self.project / ".codex-adaptive-agents/state/policy-cache.json").read_text())
        self.assertEqual(cache["last_success"], "0001-01-01T00:00:00Z")

    def test_no_iana_database_uses_system_local_date_and_portable_utc(self):
        with patch.dict(os.environ, {"TZ": ""}), \
                patch.object(policy, "ZoneInfo", side_effect=KeyError("database absent")):
            name, zone, day = policy._system_timezone(self.now)
            self.assertTrue(name.startswith('local:'))
            self.assertEqual(day, self.now.astimezone().date().isoformat())
            self.assertEqual(self.now.astimezone(zone).date().isoformat(), day)
        with patch.dict(os.environ, {"TZ": "UTC"}), \
                patch.object(policy, "ZoneInfo", side_effect=KeyError("database absent")):
            self.assertEqual(policy._system_timezone(self.now), ('UTC', timezone.utc, '2026-09-15'))
        with patch.dict(os.environ, {"TZ": "Asia/Shanghai"}), \
                patch.object(policy, "ZoneInfo", side_effect=KeyError("database absent")):
            with self.assertRaisesRegex(policy.PolicyError, 'unset TZ'):
                policy._system_timezone(self.now)


if __name__ == "__main__":
    unittest.main()
