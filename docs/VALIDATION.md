# 0.4.0 validation

**Published baseline: SOURCE_PUBLISHED.** Observed on 2026-09-15 with macOS arm64,
Python 3.14.6 and official Codex CLI 0.147.0. These are 0.4.0 observations;
older releases are not used as runtime evidence for this version.

| Layer | Published baseline |
| --- | --- |
| Python source tests | 42 tests on each CI matrix target |
| Package | One allowlisted Python ZIP; internal and external SHA256 checks |
| macOS runtime | Clean install, repeat install, moved download, doctor, select, refresh, recovery and uninstall checked |
| Existing macOS installation | Managed upgrade to 0.4.0; unrelated configuration and role retained |
| Real native tasks | Two direct Luna Max children completed; real artifact and public event revalidation passed |
| Linux runtime | Ubuntu 24.04: isolated CLI lifecycle and source/package tests passed; client/model data mocked |
| Windows runtime | Windows Server 2022: isolated CLI lifecycle and source/package tests passed; client/model data mocked |
| Python 3.11 and 3.13 | Tests and package build passed on all three hosted operating systems |
| Remote CI | [Current workflow runs](https://github.com/aweiht/astra-luna-native/actions/workflows/native.yml); check the run for the source commit you use |

The release is a readable Python 3.11+ standard-library ZIP. No Go compiler,
custom binary or pip package is needed. Source tests and package inspection do
not prove official login, current quota or model access on another machine.


## Local process handoff on 2026-09-20 (not yet pushed)

The current local source ran **74 offline tests: 73 passed, one Linux-only test
skipped**, on macOS arm64 / Python 3.14.6. Ten process-registry regressions cover
real CLI success/failure/timeout, concurrent entry registration, launcher death,
retained services followed by later cleanup, other-run and unrelated-process
preservation, stale identity rejection, missing entry files, Darwin exec identity
and scalar Tracker birth tokens. The installed-entry lifecycle also checks the
new commands after the original download moves.

Independent real-process checks confirmed retained-service cleanup and recovery
after a killed launcher, with an unrelated process kept running. Records are
written before launch, and parent checks re-read OS process identities. A missing
completion report remains visible after successful cleanup; `CLEAN` is a process
result, not evidence of successful task work.

This revision has not been pushed or run in Linux/Windows CI. The platform
branches are implemented; the published CI results below are historical and do
not validate these new commands on those operating systems. No new live model
smoke test was run. The cooperative registry does not guarantee detection of
unregistered or unobservable descendants; the macOS containment boundary remains.

## Local adversarial fixes on 2026-09-20 (not yet pushed)

The revised source ran 64 offline tests on macOS arm64 / Python 3.14.6:
63 passed and the Linux-only double-fork/subreaper test was skipped. The tests
cover deadline fallback with original snapshot age, same-day project recovery,
contradictory public model/effort metadata, APFS case-alias lifecycle, detached
children after timeout or parent exit, unrelated-process preservation and
signal exit status. Cleanup-failure, non-UTF-8 process-name parsing, zero-PID
rejection and old schema-4 upgrade regressions also passed. Fixture scripts ran against the independent 132 + 132 + 5
case oracle; no live model task was started.

Linux subreaper code and Windows compatibility remain pending this revision's
hosted CI. Earlier matrix results in this document describe the published
baseline, not the unpushed changes. The workflow now runs on all pushes and pull
requests, so translated README, image and ignore-rule changes cannot bypass it.
Older macOS kernels without original-parent-version metadata explicitly skip
the detached-child/early-parent-exit subcase; that boundary is not claimed as
verified on those kernels. See the [macOS cleanup limit](PUBLIC_GUIDE.md#process-cleanup-boundary).

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

## Real task evidence

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
