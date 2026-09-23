from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest

from codex_adaptive_agents import transport, verify


PASSING_TAGS = r'''#!/usr/bin/env python3
import sys

seen = set()
for raw in sys.stdin.buffer:
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    raw = raw.strip(b" \t\r")
    raw = bytes(c + 32 if 65 <= c <= 90 else c for c in raw)
    if raw and raw not in seen:
        seen.add(raw)
        sys.stdout.buffer.write(raw + b"\n")
'''


PASSING_NUMBERS = r'''#!/usr/bin/env python3
import re
import sys

seen = set()
for raw in sys.stdin.buffer:
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if not re.fullmatch(br"[+-]?[0-9]+", raw):
        raise SystemExit(2)
    try:
        value = int(raw.decode("ascii"), 10)
    except (UnicodeDecodeError, ValueError, OverflowError):
        raise SystemExit(2)
    if value < -10000 or value > 10000:
        raise SystemExit(2)
    if value not in seen:
        seen.add(value)
        print(value)
'''


def good_events(role="adaptive_luna_xhigh", effort="xhigh") -> list[dict]:
    # Keep root/thread-started before root/client-effective to cover the
    # ordering seen in the public app-server stream.
    events: list[dict] = [
        {
            "event": "thread/started",
            "thread": {"id": "root", "parentThreadId": "", "status": {"type": "active"}},
        },
        {
            "event": "root/client-effective",
            "threadId": "root",
            "metadata": {
                "model": "gpt-6-astra",
                "reasoningEffort": "max",
                "modelProvider": "openai",
            },
        },
        {
            "event": "turn/started",
            "threadId": "root",
            "turn": {"id": "root-turn", "status": "inProgress"},
            "elapsed_seconds": 0.1,
        },
    ]
    for child, start, end in (("child-a", 1.0, 4.0), ("child-b", 2.0, 5.0)):
        events.extend(
            [
                {
                    "event": "item/started",
                    "threadId": "root",
                    "item": {"id": "activity-" + child, "type": "subAgentActivity", "agentThreadId": child},
                    "elapsed_seconds": start - 0.1,
                },
                {
                    "event": "thread/status/changed",
                    "threadId": child,
                    "status": {"type": "active"},
                    "elapsed_seconds": start,
                },
                {
                    "event": "turn/started",
                    "threadId": child,
                    "turn": {"id": "turn-" + child, "status": "inProgress"},
                    "elapsed_seconds": start,
                },
                {
                    "event": "turn/completed",
                    "threadId": child,
                    "turn": {"id": "turn-" + child, "status": "completed"},
                    "elapsed_seconds": end,
                },
                {
                    "event": "thread/status/changed",
                    "threadId": child,
                    "status": {"type": "idle"},
                    "elapsed_seconds": end + 0.1,
                },
                {
                    "event": "public-request",
                    "method": "thread/read",
                    "params": {"threadId": child, "includeTurns": False},
                    "elapsed_seconds": end + 0.2,
                },
                {
                    "event": "child/public-metadata",
                    "thread": {
                        "id": child,
                        "parentThreadId": "root",
                        "agentRole": role,
                        "model": "gpt-6-luna",
                        "reasoningEffort": effort,
                        "status": {"type": "idle"},
                        "spawn": {
                            "parent_thread_id": "root",
                            "agent_role": role,
                            "depth": 1,
                        },
                    },
                    "elapsed_seconds": end + 0.2,
                },
            ]
        )
    events.append(
        {
            "event": "turn/completed",
            "threadId": "root",
            "turn": {"id": "root-turn", "status": "completed"},
            "elapsed_seconds": 6.0,
        }
    )
    return events


class VerifyPublicEventsTests(unittest.TestCase):
    @staticmethod
    def _collab_events(*, tool="spawnAgent", model="gpt-6-luna", effort="xhigh") -> list[dict]:
        item = {
            "id": "call-spawn",
            "type": "collabAgentToolCall",
            "tool": tool,
            "status": "inProgress",
            "senderThreadId": "root",
            "receiverThreadIds": ["child-a", "child-b"] if tool == "spawnAgent" else [],
            "model": model,
            "reasoningEffort": effort,
        }
        completed = deepcopy(item)
        completed["status"] = "completed"
        return [
            {"event": "item/started", "threadId": "root", "item": item, "elapsed_seconds": 0.2},
            {"event": "item/completed", "threadId": "root", "item": completed, "elapsed_seconds": 0.3},
        ]

    def test_spawn_requested_model_effort_binds_role_and_allows_repeated_events(self) -> None:
        events = good_events("adaptive_luna_max", "max")
        events[3:3] = self._collab_events(effort="max")
        result = verify.validate_public_events(events, "adaptive_luna_max")
        self.assertTrue(result["ok"], result)

        for field, value in (("model", "different-model"), ("model", "gpt-5.6-luna"),
                             ("reasoningEffort", "low")):
            with self.subTest(field=field):
                mutated = deepcopy(events)
                next(
                    event["item"] for event in mutated
                    if event.get("event") == "item/completed"
                )[field] = value
                result = verify.validate_public_events(mutated, "adaptive_luna_max")
                self.assertFalse(result["ok"], result)

    def test_non_spawn_collab_model_effort_does_not_bind_role(self) -> None:
        events = good_events("adaptive_luna_max", "max")
        events[3:3] = self._collab_events(tool="sendInput", model="different-model", effort="low")
        result = verify.validate_public_events(events, "adaptive_luna_max")
        self.assertTrue(result["ok"], result)

    def test_transport_metadata_keeps_child_effective_conflict_visible(self) -> None:
        events = deepcopy(good_events("adaptive_luna_max", "max"))
        for event in events:
            if event.get("event") == "child/public-metadata":
                event["thread"] = transport.metadata(
                    {
                        **event["thread"],
                        "effective": {
                            "model": "different-model",
                            "reasoningEffort": "low",
                            "prompt": "must not persist",
                        },
                    }
                )
        result = verify.validate_public_events(events, "adaptive_luna_max")
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("child effective model" in error for error in result["errors"]))
        self.assertTrue(any("child effective effort" in error for error in result["errors"]))

    @staticmethod
    def multiple_turn_events(windows=None) -> list[dict]:
        windows = windows or {"child-a": [(1.0, 4.0), (10.0, 12.0)],
                              "child-b": [(2.0, 5.0), (11.0, 13.0)]}
        base = good_events()
        events = base[:3]
        for child, turns in windows.items():
            for index, (start, end) in enumerate(turns):
                turn_id = f"turn-{child}-{index}"
                events.extend([
                    dict(event="thread/status/changed", threadId=child,
                         status={"type": "active"}, elapsed_seconds=start),
                    dict(event="turn/started", threadId=child,
                         turn={"id": turn_id, "status": "inProgress"}, elapsed_seconds=start),
                    # Actual Codex ordering observed in the live stream.
                    dict(event="thread/status/changed", threadId=child,
                         status={"type": "idle"}, elapsed_seconds=end - 0.0001),
                    dict(event="turn/completed", threadId=child,
                         turn={"id": turn_id, "status": "completed"}, elapsed_seconds=end),
                ])
        events.append(dict(event="turn/completed", threadId="root",
                           turn={"id": "root-turn", "status": "completed"}, elapsed_seconds=20.0))
        for event in base:
            if event.get("event") in ("public-request", "child/public-metadata"):
                event["elapsed_seconds"] = 21.0
                events.append(event)
        return sorted(events, key=lambda event: event.get("elapsed_seconds", 0.0))

    def test_multiple_revision_turns_match_and_overlap_excludes_gaps(self) -> None:
        result = verify.validate_public_events(self.multiple_turn_events())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["overlap_seconds"], 3.0)
        self.assertEqual(len(result["child_runtime_windows"]["child-a"]["turns"]), 2)
        gap_only = self.multiple_turn_events({"child-a": [(1., 2.), (5., 6.)],
                                              "child-b": [(3., 4.), (7., 8.)]})
        result = verify.validate_public_events(gap_only)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["overlap_seconds"], 0.0)

    def test_revision_failures_missing_pairs_and_stale_idle_are_rejected(self) -> None:
        for mutation in ("missing-start", "missing-end", "failed", "duplicate-id", "stale-idle", "early-metadata"):
            with self.subTest(mutation=mutation):
                events = self.multiple_turn_events()
                start = next(e for e in events if e.get("event") == "turn/started"
                             and e.get("turn", {}).get("id") == "turn-child-a-1")
                end = next(e for e in events if e.get("event") == "turn/completed"
                           and e.get("turn", {}).get("id") == "turn-child-a-1")
                if mutation == "missing-start":
                    events.remove(start)
                elif mutation == "missing-end":
                    events.remove(end)
                elif mutation == "failed":
                    end["turn"]["status"] = "failed"
                elif mutation == "duplicate-id":
                    start["turn"]["id"] = end["turn"]["id"] = "turn-child-a-0"
                elif mutation == "stale-idle":
                    events = [e for e in events if not (e.get("event") == "thread/status/changed"
                              and e.get("threadId") == "child-a" and e.get("status", {}).get("type") == "idle"
                              and e["elapsed_seconds"] > 10)]
                else:
                    next(e for e in events if e.get("event") == "child/public-metadata"
                         and e["thread"]["id"] == "child-a")["elapsed_seconds"] = 5.0
                self.assertFalse(verify.validate_public_events(events)["ok"])

    def test_accepts_strict_success_and_reports_actual_overlap(self) -> None:
        result = verify.validate_public_events(good_events("adaptive_luna_max", "max"), "adaptive_luna_max")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["native_child_ids"], ["child-a", "child-b"])
        self.assertEqual(result["overlap_seconds"], 2.0)
        self.assertEqual(result["public_thread_reads"], 2)

    def test_rejects_known_false_positive_evidence(self) -> None:
        mutations = {
            "rootwrongmodel": lambda events: events[1]["metadata"].update(model="gpt-6-luna"),
            "missingchildturn": lambda events: next(
                event for event in events if event.get("event") == "turn/completed" and event.get("threadId") == "child-a"
            ).pop("turn"),
            "nonoverlap": lambda events: self._set_child_window(events, "child-b", 10.0, 12.0),
            "staleidle": lambda events: self._set_idle_elapsed(events, "child-a", 0.5),
            "depth": lambda events: self._child_meta(events, "child-a")["spawn"].update(depth=2),
            "role": lambda events: self._child_meta(events, "child-a").update(agentRole="adaptive_luna_low"),
            "wrongparent": lambda events: self._child_meta(events, "child-a").update(parentThreadId="other-root"),
            "testsfailed": lambda events: events[0].update(tests_failed=True),
            "include-missing": lambda events: next(
                event for event in events if event.get("event") == "public-request"
            )["params"].pop("includeTurns"),
            "include-true": lambda events: next(
                event for event in events if event.get("event") == "public-request"
            )["params"].update(includeTurns=True),
            "mismatchedturn": lambda events: next(
                event for event in events if event.get("event") == "turn/completed" and event.get("threadId") == "child-b"
            )["turn"].update(id="different"),
            "unexpected-approval": lambda events: events.append(
                {"event": "approval/request", "threadId": "root"}
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                events = deepcopy(good_events("adaptive_luna_max", "max"))
                mutate(events)
                result = verify.validate_public_events(events, "adaptive_luna_max")
                self.assertFalse(result["ok"], result)

    @staticmethod
    def _child_meta(events: list[dict], child: str) -> dict:
        return next(
            event["thread"]
            for event in events
            if event.get("event") == "child/public-metadata" and event["thread"].get("id") == child
        )

    @staticmethod
    def _set_child_window(events: list[dict], child: str, start: float, end: float) -> None:
        for event in events:
            if event.get("threadId") == child and event.get("event") == "turn/started":
                event["elapsed_seconds"] = start
            if event.get("threadId") == child and event.get("event") == "turn/completed":
                event["elapsed_seconds"] = end

    @staticmethod
    def _set_idle_elapsed(events: list[dict], child: str, elapsed: float) -> None:
        for event in events:
            if event.get("threadId") == child and event.get("event") == "thread/status/changed":
                if event.get("status", {}).get("type") == "idle":
                    event["elapsed_seconds"] = elapsed

    def test_rejects_missing_explicit_false_and_nonfinite_timestamps(self) -> None:
        events = good_events()
        read = next(event for event in events if event.get("event") == "public-request")
        read["params"].pop("includeTurns")
        self.assertFalse(verify.validate_public_events(events)["ok"])
        events = good_events()
        next(event for event in events if event.get("event") == "turn/started" and event.get("threadId") == "child-a")[
            "elapsed_seconds"
        ] = float("nan")
        self.assertFalse(verify.validate_public_events(events)["ok"])

    def test_output_parser_accepts_crlf_without_stripping_content_cr(self) -> None:
        self.assertEqual(verify._split_output(b"alpha\r\nbeta\n"), [b"alpha", b"beta"])
        self.assertEqual(verify._split_output(b"alpha\rbeta\n"), [b"alpha\rbeta"])


class VerifyFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="native-python-verify-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve() / "project"
        self.project.mkdir()

    def _write_base(self, *, passing: bool) -> None:
        (self.project / "AGENTS.md").write_bytes(verify._AGENTS_SEED)
        (self.project / "contract.md").write_bytes(verify._asset("contract.md"))
        tag = PASSING_TAGS if passing else verify._asset("normalize_tags.py").decode()
        numbers = PASSING_NUMBERS if passing else verify._asset("unique_numbers.py").decode()
        (self.project / "normalize_tags.py").write_text(tag, encoding="utf-8")
        (self.project / "unique_numbers.py").write_text(numbers, encoding="utf-8")
        os.chmod(self.project / "normalize_tags.py", 0o700)
        os.chmod(self.project / "unique_numbers.py", 0o700)

    def test_initial_stubs_fail_and_independent_golden_corpus_passes_real_scripts(self) -> None:
        self._write_base(passing=False)
        initial = verify.check(self.project)
        self.assertFalse(initial["ok"], initial)

        self._write_base(passing=True)
        result = verify.check(self.project)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["normalize_cases"], 132)
        self.assertEqual(result["number_cases"], 132)
        self.assertEqual(result["invalid_number_cases"], 5)
        self.assertTrue(result["invalid_number_rejected"])

    def test_scope_and_immutable_contract_are_enforced(self) -> None:
        self._write_base(passing=True)
        (self.project / "unexpected.txt").write_text("extra\n", encoding="utf-8")
        self.assertFalse(verify.check(self.project)["ok"])
        (self.project / "unexpected.txt").unlink()
        (self.project / "contract.md").write_text("tampered\n", encoding="utf-8")
        self.assertFalse(verify.check(self.project)["ok"])


if __name__ == "__main__":
    unittest.main()
