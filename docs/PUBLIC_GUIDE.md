# Codex Adaptive Agents public operator guide

**0.6.0 status: SOURCE_READY.** Codex Adaptive Agents uses the project
repository at <https://github.com/aweiht/codex-adaptive-agents>. The package
contains the Python source used at runtime. No new live model verification was
requested for this revision. Consult [VALIDATION.md](VALIDATION.md) for current
local evidence and the earlier published CI result tied to its exact commit.

## Downloaded package

The package contains one Python entry script, the codex_adaptive_agents modules and
public smoke assets, tests_python, public Markdown, LICENSE, NOTICE, VERSION,
release-manifest.json, the release script, and SHA256SUMS. Source equals
runtime under the checked-in allowlist.

It requires Python 3.11 or newer from the standard library. It has no pip
dependency, Go requirement, or compiled binary. The package does not include
historical implementations, private development evidence, local state, or old
release archives.

The entry script accepts --codex-home PATH and --codex PATH before a subcommand.
The home defaults to CODEX_HOME and then the platform normal user Codex home.
The official client command defaults to codex.

## Installation state

Run the package entry script with Python:

~~~sh
python3 codex-adaptive-agents.py --codex-home "$HOME/.codex" install --dry-run
python3 codex-adaptive-agents.py --codex-home "$HOME/.codex" install --yes
python3 codex-adaptive-agents.py --codex-home "$HOME/.codex" doctor
~~~

On Windows PowerShell, use py -3 and PowerShell environment syntax:

~~~powershell
py -3 .\codex-adaptive-agents.py --codex-home "$env:USERPROFILE\.codex" install --dry-run
py -3 .\codex-adaptive-agents.py --codex-home "$env:USERPROFILE\.codex" install --yes
py -3 .\codex-adaptive-agents.py --codex-home "$env:USERPROFILE\.codex" doctor
~~~

The --yes flag applies a fresh managed plan. Add --plan-id HASH to bind a
previously reviewed preview. Before writing, the installer
checks the official CLI and public model catalogue. It hashes managed inputs,
creates a private backup, writes atomically, and verifies the result. It does
not read authentication files or claim that the account can complete a live
task.

After installation, the standalone entry is:

~~~text
$CODEX_HOME/codex-adaptive-agents/runtime/codex-adaptive-agents.py
~~~

Run it with Python after the original download directory has been moved or
removed:

~~~sh
codex_home="${CODEX_HOME:-$HOME/.codex}"
# If installation used --codex-home, use that same directory here.
python3 "$codex_home/codex-adaptive-agents/runtime/codex-adaptive-agents.py" --codex-home "$codex_home" doctor
python3 "$codex_home/codex-adaptive-agents/runtime/codex-adaptive-agents.py" --codex-home "$codex_home" uninstall --dry-run
~~~

On Windows PowerShell:

~~~powershell
$CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
# If installation used --codex-home, set $CodexHome to that same directory.
$Entry = Join-Path $CodexHome "codex-adaptive-agents\runtime\codex-adaptive-agents.py"
py -3 $Entry --codex-home $CodexHome doctor
py -3 $Entry --codex-home $CodexHome uninstall --dry-run
~~~

A successful uninstall removes the owned runtime and managed installation
files while preserving unrelated user changes.

## Update an existing installation

Use the project repository at
<https://github.com/aweiht/codex-adaptive-agents> and read its `VERSION` from
the default branch. An update does not require a GitHub Release or tag.

For a Git checkout with local edits, preserve the edits and use a separate
fresh checkout when necessary. Set the project remote or clone a new checkout:

~~~sh
git remote set-url origin https://github.com/aweiht/codex-adaptive-agents.git
git pull --ff-only
python3 codex-adaptive-agents.py --version
~~~

ZIP users should fetch and extract a fresh archive from that repository. The
previous download directory is not an update source. Updating a checkout with
`git pull` alone does not update the copied runtime under `CODEX_HOME`.

Use the existing `CODEX_HOME` and installed runtime path. From the new source
checkout, obtain and review a plan, then apply exactly that plan:

~~~sh
python3 codex-adaptive-agents.py --codex-home "$CODEX_HOME" install --dry-run
python3 codex-adaptive-agents.py --codex-home "$CODEX_HOME" install --yes --plan-id <plan_id>
python3 codex-adaptive-agents.py --version
python3 "$CODEX_HOME/codex-adaptive-agents/runtime/codex-adaptive-agents.py" --codex-home "$CODEX_HOME" --version
python3 "$CODEX_HOME/codex-adaptive-agents/runtime/codex-adaptive-agents.py" --codex-home "$CODEX_HOME" doctor
~~~

The installer makes its normal backup and preserves unrelated settings and
roles. A managed conflict requires attention; do not overwrite it. There is no
need to uninstall or reinstall, and there is no automatic or self-update
command. Refresh only when the capability snapshot or policy cache is stale,
then run `doctor` again. Accept the update only when the source and installed
`--version` outputs match the expected version and `doctor` returns `READY` or
`READY_FALLBACK`. New model support arrives through maintainer releases;
arbitrary future models are not promised to work automatically. Do not run
`verify --live` or a model call as part of this update check.

## Offline commands and policy

doctor is an after-install, offline check. A clean home reports NOT_READY
because no installation exists. After installation it checks managed hashes,
the standalone entry, the official CLI version, and local capability and policy
state without a model call or network request.

The other local commands are:

~~~sh
python3 codex-adaptive-agents.py select --project /path/to/project
python3 codex-adaptive-agents.py select --project /path/to/project --explain
python3 codex-adaptive-agents.py refresh
~~~

Each project keeps its policy cache in .codex-adaptive-agents/state; projects should
ignore .codex-adaptive-agents/. A next-day delegation attempt can refresh an expired
project policy. Use refresh --project /path/to/project to retry a blocked
project immediately, including a failed attempt earlier that day. Without
--project, refresh updates home-level policy state. Both forms refresh the
global public capability snapshot and leave unrelated project caches intact.

If public sources are unavailable, the selector retains a valid verified cache
or uses the conservative supported max role when the local capability directory
allows it. The result reports the fallback reason and does not claim that a
dynamic update succeeded.

## Delegation gate

The root agent owns requirements, key decisions, public interfaces,
coordination, and final acceptance. Before substantive work starts, it does
only enough exploration to identify the boundary. A safe work package with an
explicit goal, boundary, artifact or problem ownership, and checkable acceptance
must be handed to a native Luna child when the host permits it, before that
package starts. This is a trial routing policy: it has no total-cost, elapsed-time, or
token-saving threshold, no 2:8 (or other) delegation ratio, and no usage-
collection system. An explicit user request to delegate is honored within host
permissions; a verbal split or a child added after the work is complete is not
delegation.

A complete feature or UI diagnosis stays with one child through implementation,
local tests, and ordinary failure repair. Return a normal failure to that child.
Escalate to the root only for a public-interface change or major design
decision, a failure that remains unresolved after reasonable repair, or a
capability or permission limit. The root independently checks cross-module
integration and high-risk permission, data, or process behavior before final
acceptance.

Direct completion is appropriate for a simple question, a genuinely one-off
small change, a root-only decision, a tightly coupled dependency chain with no
safe independent boundary, or an explicit no-delegation request. Do not split a
substantive batch into small edits to avoid this assessment. Independent
packages may run in parallel; dependent packages run serially. Shared browser,
database, GUI, port, or build-output resources have one owner and are serialized
when needed. One task uses no more than five direct Luna children, and agents
are not started merely to satisfy a ratio or create duplicate work.

| Example | Expected route |
| --- | --- |
| Simple question, one small edit, or a root-only decision | Complete directly |
| Complete feature or UI diagnosis with clear ownership and acceptance | Delegate implementation, local tests, and ordinary repairs to one child |
| Independent work packages with separate resources | Delegate; parallelize when safe |
| Dependent packages or shared GUI, port, or build directory | Delegate; use serial order with one resource owner |
| Cross-module integration or high-risk permission, data, or process behavior | Child performs the package; root performs the independent risk check |
| Public-interface change, major design decision, repeated unresolved failure, or capability/permission block | Escalate to the root |
| The user explicitly asks for no delegation | Complete directly |

If the user specifies a worker count or parallel arrangement that cannot be met
safely, explain the constraint and ask to adjust it; do not silently change the
request. Reassess unfinished work when scope expands, the original plan stops
fitting, troubleshooting repeats, or a new implementation or verification
phase begins. The root may take work back when an escalation condition applies
and should state the reason. The daily selector only chooses the supported role
level. It does not decide whether the task deserves delegation, spawn agents, or
act as a timer or scheduler. If the selector, role, or native tool fails,
report the actual returned error and do not claim unavailability without
evidence. A successful selection is not a child-agent invocation. These are
instructions for the agent, not a host-enforced guarantee of delegation.

For substantive work, keep the context minimal and accurate and prefer bounded,
history-free handoffs. The child returns five concise points: completed work,
file or artifact locations, verification evidence, unresolved issues, and
pending decisions. Keep detailed logs at the agreed local path for on-demand
reading; do not omit failures or risks. The root may work on a different,
non-overlapping task while a child runs and waits when no such work remains.
Record the delegation or direct-completion reason in one sentence in the plan or
progress update and keep the compact execution footer. Do not create a separate
delegation report file.

## Temporary process and long command runs

Ordinary short commands do not need a process run. Replace angle-bracket
placeholders with the values passed by the root. If a work package needs a
temporary service or a bounded long command, the root creates one isolated run
and passes its `project`, `run_id`, entry point, owner, purpose, and resource or
port boundary to each child:

~~~sh
codex_home="${CODEX_HOME:-$HOME/.codex}"
entry="$codex_home/codex-adaptive-agents/runtime/codex-adaptive-agents.py"
python3 "$entry" --codex-home "$codex_home" process-init --project /path/to/project
python3 "$entry" --codex-home "$codex_home" process-run \
  --project /path/to/project --run-id <run_id> --owner <owner> \
  --purpose "<purpose>" -- <command> <arg>
python3 "$entry" --codex-home "$codex_home" process-check \
  --project /path/to/project --run-id <run_id>
python3 "$entry" --codex-home "$codex_home" process-cleanup \
  --project /path/to/project --run-id <run_id> [--retain <entry_id>,<entry_id>]
~~~

`process-init` prints the `run_id`. `process-run` is a foreground wrapper with
a default 600-second timeout; it records the real process identity and purpose
before starting, then flushes a startup event to stderr. It reclaims the command
when it exits or times out and does not create a resident service. When the
command completes, stdout contains one JSON object. `process-run` is
non-interactive: stdin is closed, combined command stdout and stderr are capped
at 2 MiB, `--timeout` defaults to 600 seconds and accepts at most 86400 seconds,
and captured command output is returned in the completion JSON. While it is
running, read an independent snapshot with `process-check` in another window;
do not treat ordinary startup text as the ledger. A child reports its `entry_id` and PID from
the startup event or that snapshot, along with purpose and validation, promptly;
it must not wait for the completion JSON. It stops its own execution session or
command. If a task explicitly authorizes a retained service, report it as
`RETAINED` with its purpose; a run with retained
entries is not `CLEAN`.

After every participant has stopped starting commands, the root performs the
independent final `process-check` and `process-cleanup`, then runs a second
`process-check` to confirm the post-cleanup state. Cleanup closes the run and
rejects new commands, so a child must not clean up a shared run. A retained
entry may be checked or cleaned again later in the same run, but that does not
permit a new command. If a child needs to stop its own entry early, stop its
execution wrapper or report it to the root; do not teach it to kill arbitrary
PIDs. This ledger is scoped to the work package. It does not scan every process
on the machine and is not a host-enforced prompt policy.

Pass the same `--retain` list to the final check when keeping a service.
`CLEAN` confirms that registered processes have stopped; it does not prove the
command or work package succeeded. `DIRTY` means a registered process remains
active, and `UNVERIFIED` means cleanup could not be confirmed. Check and cleanup
return a nonzero exit code for either of those states. An explicitly retained
service remains subject to its original command timeout.

On Windows PowerShell, use the same Codex home and replace `python3` with
`py -3`:

~~~powershell
$CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$Entry = Join-Path $CodexHome "codex-adaptive-agents\runtime\codex-adaptive-agents.py"
py -3 $Entry --codex-home $CodexHome process-init --project C:\path\to\project
py -3 $Entry --codex-home $CodexHome process-run --project C:\path\to\project --run-id <run_id> --owner <owner> --purpose "<purpose>" -- <command> <arg>
py -3 $Entry --codex-home $CodexHome process-check --project C:\path\to\project --run-id <run_id>
py -3 $Entry --codex-home $CodexHome process-cleanup --project C:\path\to\project --run-id <run_id> [--retain <entry_id>,<entry_id>]
~~~

## Live verification

verify --live is explicit, uses real Codex allowance, and starts at most two
direct native Luna children with the selected role. It is bounded at 600
seconds:

~~~sh
python3 codex-adaptive-agents.py verify --live \
  --project /path/to/smoke-project \
  --output /path/to/verification-output
~~~

The command creates a temporary official stdio app-server test channel. That
channel exists only for the check; it is not an installed MCP server, daemon,
Hook, scheduler, or resident process. Live output keeps requested,
client-effective, and server-reported model metadata separate when the official
client exposes those values. The independent verifier checks child roles,
scope, hashes, and smoke contracts; a successful process exit alone does not
prove the task result.

## Process cleanup boundary

The transient client channel is bounded. Windows assigns descendants to a Job
Object. Linux uses a short-lived Python subreaper per command so detached
orphans are still cleaned up. macOS tracks process birth and original-parent
identifiers and cleans up observed descendants even after `setsid` or a parent
exit. A separate cleanup acknowledgement is required before success is reported;
a timeout or supervision failure is not a successful verification.

The original-parent-version field is used only after a live parent/child
capability check. Older macOS versions fall back to birth identities and the
command process group; a detached child whose parent exits before observation
may be missed. On every macOS version, a short-lived intermediary that forks
and is reaped between snapshots can hide deeper lineage. This is not
kernel-enforced containment.
Use the official Codex sandbox for untrusted task code; this process helper is
not a replacement sandbox. It is created only for a command and exits with it,
not an installed service. Current platform-specific test evidence is recorded
in [VALIDATION.md](VALIDATION.md).

## Recovery and uninstall

Use the transaction commands with the same Codex home:

~~~sh
python3 codex-adaptive-agents.py uninstall --dry-run
python3 codex-adaptive-agents.py uninstall --yes
python3 codex-adaptive-agents.py recover --yes
python3 codex-adaptive-agents.py rollback --backup PATH --yes
~~~

`--dry-run` is accepted only by `install` and `uninstall`. Other commands reject
it before execution; it cannot be used to preview `refresh` or `verify --live`.

`recover --yes` restores only an interrupted journal marked `applying`. It
refuses a committed journal and does not accept `--backup`. To reverse a
completed transaction, `rollback --backup PATH --yes` requires its recorded
backup path. Both operations refuse to overwrite an unrelated user change.

Uninstall removes files and empty configuration tables created by the installer
only when their creation was recorded and they contain no later user additions.
Backup history is preserved.

## Package inspection

The checked-in release manifest is the exact allowlist. Build a private
package with:

~~~sh
python3 scripts/release_python.py --output-dir /tmp/codex-adaptive-agents-release
~~~

The in-package SHA256SUMS covers each regular payload file except itself. The
external checksum file covers each archive produced in that invocation. The
builder rejects source symlinks, unsafe archive members, private Markdown
paths, broken local Markdown links, and overwriting an existing archive.

Source tests and package inspection are offline checks. They do not establish
model access, current quota, live delegation, or publication. See LICENSE,
NOTICE, UPSTREAM.md, and VALIDATION.md for the supporting public material.
