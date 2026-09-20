# Public operator guide

**0.4.0 status: SOURCE_PUBLISHED.** The package contains the Python source used
at runtime. Windows, Linux and macOS CI passed source, isolated installation
and package checks. Real model-task evidence is macOS only. See
[VALIDATION.md](VALIDATION.md).

## Downloaded package

The package contains one Python entry script, the astra_luna modules and
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
python3 astra-luna.py --codex-home "$HOME/.codex" install --dry-run
python3 astra-luna.py --codex-home "$HOME/.codex" install --yes
python3 astra-luna.py --codex-home "$HOME/.codex" doctor
~~~

On Windows PowerShell, use py -3 and PowerShell environment syntax:

~~~powershell
py -3 .\astra-luna.py --codex-home "$env:USERPROFILE\.codex" install --dry-run
py -3 .\astra-luna.py --codex-home "$env:USERPROFILE\.codex" install --yes
py -3 .\astra-luna.py --codex-home "$env:USERPROFILE\.codex" doctor
~~~

The --yes flag applies a fresh managed plan. Add --plan-id HASH to bind a
previously reviewed preview. Before writing, the installer
checks the official CLI and public model catalogue. It hashes managed inputs,
creates a private backup, writes atomically, and verifies the result. It does
not read authentication files or claim that the account can complete a live
task.

After installation, the standalone entry is:

~~~text
$CODEX_HOME/astra-luna-native/runtime/astra-luna.py
~~~

Run it with Python after the original download directory has been moved or
removed:

~~~sh
codex_home="${CODEX_HOME:-$HOME/.codex}"
# If installation used --codex-home, use that same directory here.
python3 "$codex_home/astra-luna-native/runtime/astra-luna.py" --codex-home "$codex_home" doctor
python3 "$codex_home/astra-luna-native/runtime/astra-luna.py" --codex-home "$codex_home" uninstall --dry-run
~~~

On Windows PowerShell:

~~~powershell
$CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
# If installation used --codex-home, set $CodexHome to that same directory.
$Entry = Join-Path $CodexHome "astra-luna-native\runtime\astra-luna.py"
py -3 $Entry --codex-home $CodexHome doctor
py -3 $Entry --codex-home $CodexHome uninstall --dry-run
~~~

A successful uninstall removes the owned runtime and managed installation
files while preserving unrelated user changes.

## Offline commands and policy

doctor is an after-install, offline check. A clean home reports NOT_READY
because no installation exists. After installation it checks managed hashes,
the standalone entry, the official CLI version, and local capability and policy
state without a model call or network request.

The other local commands are:

~~~sh
python3 astra-luna.py select --project /path/to/project
python3 astra-luna.py select --project /path/to/project --explain
python3 astra-luna.py refresh
~~~

Each project keeps its policy cache in .astra-luna/state; projects should
ignore .astra-luna/. A next-day delegation attempt can refresh an expired
project policy. Use refresh --project /path/to/project to retry a blocked
project immediately, including a failed attempt earlier that day. Without
--project, refresh updates home-level policy state. Both forms refresh the
global public capability snapshot and leave unrelated project caches intact.

If public sources are unavailable, the selector retains a valid verified cache
or uses the conservative supported max role when the local capability directory
allows it. The result reports the fallback reason and does not claim that a
dynamic update succeeded.

## Delegation gate

The root agent owns the task decision and final acceptance. Before substantive
work starts, it assesses scope and expected payoff. A work package must be
evaluated when work crosses modules or layers, spans multiple substantive files,
contains multiple independent workflows, needs cross-component debugging, or
needs independent exploration, external verification, implementation, testing,
or review. When a package has an explicit goal, file or problem ownership, and
checkable acceptance, and its expected value exceeds child startup,
communication, and acceptance overhead, the root dispatches a native child when
the host permits it. An explicit user request to delegate is honored within
those permissions. The root must make the call before doing the package; a
verbal split or a child added after the work is complete is not delegation.

Direct completion is appropriate for a simple single-point change, a repetitive
mechanical rename, a root-only decision, an inseparable dependency chain, or an
explicit no-delegation request. A change being small does not waive a
cross-module assessment. Independent packages may run in parallel; dependent
packages run serially; one task uses no more than five direct Luna children.
Before parallel work, check shared browser, database, port, and output resources;
use explicit read-only or isolated scopes, or serialize conflicting work. If an
explicit worker count or parallel arrangement cannot be met safely, explain the
constraint and ask to adjust it rather than silently changing the request.
Architectural decisions stay with the root, while useful independent
fact-checking can still be delegated. Distinguish argument or permission errors,
temporary failures, and host limitations; one failed call does not establish
that Luna is generally unavailable.

| Example | Expected route |
| --- | --- |
| Changes cross modules or layers with separate ownership and acceptance | Delegate; parallelize only independent packages |
| Separate workflows or cross-component debugging can be isolated | Delegate; keep dependency order when required |
| One local typo, a mechanical rename, or a root-only decision without a useful independent check | Complete directly |
| A tightly coupled fix has no safe independent boundary | Complete directly, then reassess if a boundary appears |
| The user explicitly asks for no delegation | Complete directly |

Reassess unfinished work when scope expands, the original plan stops fitting,
troubleshooting repeats, or a new implementation or verification phase begins.
The daily selector only chooses the supported role level. It does not decide
whether the task deserves delegation, spawn agents, or act as a timer or
scheduler. If the selector, role, or native tool fails, report the actual
returned error and do not claim unavailability without evidence. A successful
selection is not a child-agent invocation. These are instructions for the agent,
not a host-enforced guarantee of delegation.

For substantive work, record the delegation or direct-completion reason in one
sentence in the plan or progress update and keep the existing compact execution
footer. Do not create a separate delegation report file.

## Temporary process and long command runs

Ordinary short commands do not need a process run. Replace angle-bracket
placeholders with the values passed by the root. If a work package needs a
temporary service or a bounded long command, the root creates one isolated run
and passes its `project`, `run_id`, entry point, owner, purpose, and resource or
port boundary to each child:

~~~sh
codex_home="${CODEX_HOME:-$HOME/.codex}"
entry="$codex_home/astra-luna-native/runtime/astra-luna.py"
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
$Entry = Join-Path $CodexHome "astra-luna-native\runtime\astra-luna.py"
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
python3 astra-luna.py verify --live \
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
python3 astra-luna.py uninstall --dry-run
python3 astra-luna.py uninstall --yes
python3 astra-luna.py recover --yes
python3 astra-luna.py rollback --backup PATH --yes
~~~

Recovery is for a journal marked applying. The installer refuses to overwrite
an unrelated user change when restoring a recorded transaction.

## Package inspection

The checked-in release manifest is the exact allowlist. Build a private
package with:

~~~sh
python3 scripts/release_python.py --output-dir /tmp/astra-luna-release
~~~

The in-package SHA256SUMS covers each regular payload file except itself. The
external checksum file covers each archive produced in that invocation. The
builder rejects source symlinks, unsafe archive members, private Markdown
paths, broken local Markdown links, and overwriting an existing archive.

Source tests and package inspection are offline checks. They do not establish
model access, current quota, live delegation, or publication. See LICENSE,
NOTICE, UPSTREAM.md, and VALIDATION.md for the supporting public material.
