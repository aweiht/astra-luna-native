# Codex Adaptive Agents validation

Validation is tied to a source revision and a test environment. Source tests and
package checks do not establish official login, current quota or real model
access on another machine.

## 0.5.0 rename and upgrade compatibility

The project name and repository change to Codex Adaptive Agents. The existing
entry script, installation paths and role IDs are retained for compatibility.
The tests for this revision distinguish an upgrade from actual 0.4.0 source
from simulated future default migrations. Simulated models are test fixtures;
they do not establish real model availability.

On 2026-09-20, the integrated local suite ran **95 offline tests: 94 passed,
one Linux-only test skipped**, on macOS arm64 / Python 3.14.6. It covers trusted
version profiles, unknown or malformed version refusal, downgrade refusal,
managed-key conflicts, original-value preservation, and legacy schema-1/2
receipts without a version field.

Separate CLI acceptance used both the published `a9ae95e` source and the repaired
0.4.0 source package: install, upgrade to 0.5.0, explicit backup rollback,
upgrade again, move the old download, and uninstall all passed. Unrelated
configuration, roles and instructions were preserved; uninstall restored the
original managed values while retaining later user additions. The future-model
migration tests use source-declared synthetic profiles only.

The existing local installation was upgraded from a reviewed plan with a backup.
Its installed entry reports **0.5.0** and `doctor` reports **READY_FALLBACK**.
Configuration, all six existing role files, and instructions outside the managed
block remained byte-identical. All 16 runtime files match source, and a repeated
installation plan contains no changes. The bilingual READMEs each contain four
self-contained prompts with the full repository URL and legacy identity.

No new live model calls were made for this revision. Its Linux/Windows CI must
be checked after publication; the historical run below does not validate 0.5.0.

## Published source at a9ae95e — 2026-09-20

[CI run 35502299459](https://github.com/aweiht/codex-adaptive-agents/actions/runs/35502299459)
passed all **six jobs** for commit `a9ae95e02086711c5e7325698e43445ab44cc05a`.
The suite at that commit contains **74 offline tests**; platform-specific tests
may be skipped outside their target operating system.

| Layer | Evidence for that commit |
| --- | --- |
| macOS, Linux and Windows | macos-14, ubuntu-24.04 and windows-2022 with Python 3.11 and 3.13; six successful CI jobs |
| Isolated installation | Install, repeat install, moved download, doctor, select, refresh and uninstall with simulated public CLI/catalogue data |
| Process handoff | Registered success/failure/timeout, launcher death, retained-service cleanup, stale identities, missing entries and unrelated-process preservation |
| Package | Allowlisted Python ZIP, safe paths, public Markdown links and internal/external SHA256 checks |
| Local macOS | 74 tests: 73 passed, one Linux-only test skipped on arm64 / Python 3.14.6 |
| Real model calls | No additional live run for this commit; historical macOS evidence is described below |

This CI run covers the previously added process commands and adversarial fixes.
It does not validate subsequent source changes. For another commit, check its
own run in the [workflow history](https://github.com/aweiht/codex-adaptive-agents/actions/workflows/native.yml).

## Follow-up regression fixes — 2026-09-20

The follow-up changes separate interrupted recovery from explicit rollback,
reject unsupported dry-run requests before execution, recognize process-project
case aliases by directory identity, validate backup paths before directory
creation, preserve user content during uninstall, and validate receipt types.
The full local suite ran **86 offline tests: 85 passed, one Linux-only test
skipped**, on macOS arm64 / Python 3.14.6. Independent installed-entry checks
confirmed committed-recovery refusal without mutation, explicit rollback,
restoration of original file presence/content after uninstall, structured
malformed-receipt rejection, no outside directory creation through a backup
symlink, and active-process cleanup through a real APFS case alias. New tests
also preserve compatibility with old process ledgers and installation receipts.

The published run above predates these changes; it is not their Linux or Windows
validation. No new live model smoke test is included in these fixes.

The process registry records command identities before launch and rechecks them
against the operating system. `CLEAN` only describes registered process cleanup,
not successful task work. Missing completion reports remain visible. This
cooperative registry cannot guarantee discovery of unregistered or unobservable
descendants; see the [macOS cleanup limit](PUBLIC_GUIDE.md#process-cleanup-boundary).
Older macOS kernels without original-parent-version metadata skip the detached
child/early-parent-exit subcase rather than claim it verified.

## Documentation verification on 2026-09-19

The bilingual READMEs now distinguish installation, normal Codex use, local
readiness and optional live verification. The embedded workflow diagrams are
illustrations. The CLI image contains actual output from the existing macOS
installation after a cache refresh: `doctor` returned `READY_FALLBACK`, and
`select` returned `adaptive_luna_max`. No live verification task was started
for these images. Public image data and scope are in [images](images/README.md).

A separate real installation attempt using an empty Codex home stopped during
preflight because that CLI catalogue lacked the required Astra/Luna roles.
It made no managed installation changes. This is why the quick start requires
a working compatible Codex environment and does not promise model access merely
from downloading the source. The isolated lifecycle CI tests still use mocked
catalogue data; they do not remove this prerequisite.

## Reproduce offline checks

The copyable Codex setup prompts added on 2026-09-20 use the existing managed
installer and checks. Their quick-check commands were exercised on an existing
macOS installation: `doctor` initially reported an expired policy cache;
`refresh` succeeded, `doctor` then returned `READY_FALLBACK`, and `select`
returned `adaptive_luna_max`. No `verify --live` task was started for this
documentation change. Installation lifecycle tests use an isolated home and
simulated public catalogue data; this is not a fresh real-account installation.

On macOS or Linux:

~~~sh
python3 -m unittest discover -s tests_python -p 'test_*.py' -v
python3 scripts/release_python.py --output-dir /tmp/astra-luna-release
~~~

On Windows PowerShell:

~~~powershell
py -3 -m unittest discover -s tests_python -p 'test_*.py' -v
py -3 scripts/release_python.py --output-dir "$env:TEMP\astra-luna-release"
~~~

The tests cover managed configuration preservation, upgrade receipts, stale
plans, conflicts, rollback, uninstall, paths with spaces and non-ASCII text,
bounded processes, child process cleanup, source freshness, local calendar
days, malformed input, lifecycle false positives and package integrity.
CLI lifecycle tests use fake public client data and no model calls.

The builder checks an explicit file allowlist, safe archive paths, symlinks,
public Markdown links, private path leakage, complete SHA256 coverage and
output overwrite protection. Existing release archives are never overwritten.

## Historical real task evidence — 2026-09-15

The real run took 342.20 seconds. An Astra Max root created exactly two direct
adaptive_luna_max children. Each child completed an initial turn and a revision
turn. Their actual turn intervals overlapped for 41.03 seconds in total;
waiting gaps are excluded. This is evidence of overlapping execution, not a
benchmark or a claim of faster delivery.

The first command returned LIVE_FAILED because the verifier incorrectly
required a single turn per child and assumed idle must follow completion.
That failed result is retained. After fixing these assumptions and adding
negative tests, the final verifier replayed the saved sanitized public events
and independently checked the unchanged real artifacts in the official
workspace sandbox. Both checks passed, with no additional model call.
This is recorded as LIVE_EVIDENCE_REVALIDATED, not a second live run.

The independent check passed 132 tag-normalization cases, 132 integer cases
and 5 invalid integer inputs. Initial stubs failed. Both protected contract
files remained unchanged and no extra deliverable files were accepted.

Public metadata showed an Astra/max/openai client configuration, the two
selected native child roles, direct ancestry, depth 1, completed turns and
final idle status. The metadata requests used includeTurns=false. Raw turn
history, internal sessions and authentication files were not read. Server-side
model identity remains unknown; no model self-report is used as proof.

## Install and verify on your machine

~~~sh
python3 astra-luna.py install --yes
python3 astra-luna.py doctor
python3 astra-luna.py verify --live
~~~

doctor checks the installed files and local capability/policy state offline;
it does not call a model. READY_FALLBACK means the installation is usable with
a supported conservative Max role because comparable radar evidence is
insufficient. It does not mean a dynamic recommendation was proven.

verify --live is explicit, may consume Codex allowance and is bounded at 600
seconds. It opens an official temporary stdio test channel, verifies two
native children and their outputs, then closes the channel. No MCP server or
resident service is installed. A successful complete run returns LIVE_VERIFIED.

Windows and Linux installation lifecycles ran on real hosted operating
systems with fake public CLI/catalogue data. This verifies the Python and OS
paths, including Windows batch invocation, locking and process cleanup. It
does not verify a logged-in official Codex client, sandbox permissions or real
model tasks on those two platforms.

The first Windows CI run exposed command-line quote escaping and absent IANA
timezone data. Both were fixed before the successful matrix run. UTC works
without timezone data, and the default uses the operating system's local
timezone. Explicit non-UTC TZ names require available system IANA data; unset
TZ to use the normal system-local behavior without adding dependencies.
