# Codex Adaptive Agents validation

Evidence applies to a specific source revision and environment. Offline tests
and local readiness do not establish model access or quota on another machine.

## 2026-09-24: compact process output and execution handoffs

The complete offline suite ran **106 tests: 105 passed, one Linux-only test
skipped**, on macOS arm64 / Python 3.14.6. The focused process and CLI suite
passed 22 tests, including default-output compatibility, nonzero exits,
timeouts, output overflow, partial log writes, unsafe log paths, and rejecting
`--summary` for other commands.

The installed runtime was exercised with the same real command in full and
summary modes. Each command produced 30 samples over at least 15 seconds,
with 245,760 stdout bytes and 16 stderr bytes. Completion JSON measured
246,076 bytes in full mode and 812 bytes in summary mode; saved log bytes
matched the full output exactly. Each invocation emitted one startup event
and one completion JSON, with no agent reads of intermediate progress.
These are output-size measurements, not model-token savings or evidence of
a changed daily Astra/Luna usage ratio.

An isolated upgrade, exact rollback, reapply, repeat installation, and
uninstall preserved user settings and instructions. The reviewed local
installation changed no configuration keys; unrelated roles and instructions
outside the managed block were preserved. All 16 runtime files match source,
a repeat installation plan has no changes, and `doctor` reports
**READY_FALLBACK**. Two native Luna/xhigh children handled implementation and
documentation; no separate `verify --live` test was run. This new revision
has not yet run on Windows or Linux CI.

## 0.7.0: GPT-6 Luna and xhigh fallback

On 2026-09-23, the complete offline suite ran **101 tests: 100 passed, one
Linux-only test skipped**, on macOS arm64 / Python 3.14.6. Tests cover missing
scores, old-model radar rows, exact-model cache identity, same-day migration,
failed capability probes, and preserving rating-based choices. Installation
coverage includes 0.6.0 roles using `gpt-5.6-luna`, upgrade to `gpt-6-luna`,
root setting preservation, strict child configuration conflicts, rollback, and
uninstall. Public-event validation rejects the previous child model while
accepting explicitly selected supported reasoning levels.

The existing local installation was upgraded with a reviewed plan and backup.
All five role files now specify `gpt-6-luna`; the default child effort is
`xhigh`. The root Astra/xhigh settings, unrelated configuration, other roles,
and instructions outside the managed block were preserved. All 16 installed
runtime files match source, and a repeat installation plan has zero changes.
The official CLI 0.155.1 public catalogue exposes GPT-6 Luna with all five
configured reasoning levels. Actual `doctor` returned **READY_FALLBACK** and
`select` returned **adaptive_luna_xhigh**, because comparable radar evidence
was insufficient.

These checks establish local installation and selection readiness. Two native
children were requested as GPT-6 Luna/xhigh for development work; no separate
`verify --live` acceptance was run. This revision has not been validated by a
new Windows/Linux CI run or published release. Earlier results below remain
historical evidence for their stated revisions.

## 2026-09-22 delegation-policy trial

The parent and leaf templates plus the English and Simplified Chinese public
guidance were reviewed together for simple questions, one-off small changes,
complete feature or UI diagnosis, serial shared resources, ordinary failure
repair, cross-module integration, and high-risk permission, data, or process
acceptance. The current policy requires a safe package with explicit goal,
boundary, ownership, and checkable acceptance to be dispatched before its work
starts when the host permits it. It has no cost, elapsed-time, token-saving, or
fixed-ratio gate and adds no usage-collection system. This is a routing trial;
real-task token or completion-time changes remain unverified.

No wording-only tests were added. The existing offline suite ran with
`python3 -m unittest discover -s tests_python -p 'test_*.py' -v`: **91 tests,
90 passed, one Linux-only test skipped** on macOS arm64 / Python 3.14.6. The
existing release builder also produced `codex-adaptive-agents-0.6.0-python.zip`
with `source_equals_runtime: true` and validated the public Markdown links and
checksums. No additional `verify --live` model smoke test was run for this
instruction update; ordinary development delegation is separate from that test.

## 0.6.0: one project identity

On 2026-09-20, the complete local suite ran **91 tests: 90 passed, one Linux-only
test skipped**, on macOS arm64 / Python 3.14.6. This includes 24 installation
tests and separate-process CLI lifecycle checks with simulated public catalogue
inputs. Removed tests covered only the discarded cross-project migration paths.

The actual local installation now runs the new entry at version **0.6.0**.
`doctor` returned **READY_FALLBACK** and `select` returned `adaptive_luna_max`.
All 16 installed runtime files match source; a repeat installation plan has no
changes. Configuration, six existing role files and instructions outside the
managed block were preserved. These checks started no model tasks.

The source entry is `codex-adaptive-agents.py`, the Python package is
`codex_adaptive_agents`, installed resources are under
`CODEX_HOME/codex-adaptive-agents`, and project state is under
`.codex-adaptive-agents`. The distribution contains one installation contract;
it does not include a migration layer for another project identity.

The supported lifecycle is fresh install, repeat install, source-directory move,
local readiness, selection, version upgrade, backup rollback, and uninstall.
Tests preserve unrelated configuration, instructions and custom roles. Future
model migrations use synthetic test profiles, not claims of model availability.

Public commands and installation receipts reject unsupported versions,
conflicting managed edits and unsafe paths. `READY_FALLBACK` is a successful
local installation check with a conservative supported role. `select` does not
start a model task. See [the operating guide](PUBLIC_GUIDE.md) for commands.

No new live model verification was requested for this revision. Check its own
published CI run for Linux and Windows results; an earlier run does not validate
changed source. The cooperative process registry only checks registered
processes and is not an operating-system security sandbox. See
[process cleanup boundaries](PUBLIC_GUIDE.md#process-cleanup-boundary).

## Earlier published CI

[Run 35509779531](https://github.com/aweiht/codex-adaptive-agents/actions/runs/35509779531)
passed six jobs for `e9024b9e7f49a5edd60f84ad01ac8fd721928749` on macOS 14,
Ubuntu 24.04 and Windows Server 2022 with Python 3.11 and 3.13. The jobs run the
offline suite, isolated installation lifecycle and source ZIP validation. This
is evidence for that commit only.

## Documentation images

The workflow SVGs illustrate use of the project. The CLI illustration displays
selected actual command output with private paths omitted; its public input and
capture scope are described in [the image notes](images/README.md).
