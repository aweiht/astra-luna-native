# Codex Adaptive Agents validation

Evidence applies to a specific source revision and environment. Offline tests
and local readiness do not establish model access or quota on another machine.

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
