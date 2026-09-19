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
python3 "${CODEX_HOME:-$HOME/.codex}/astra-luna-native/runtime/astra-luna.py" doctor
python3 "${CODEX_HOME:-$HOME/.codex}/astra-luna-native/runtime/astra-luna.py" uninstall --dry-run
~~~

On Windows PowerShell:

~~~powershell
py -3 "$env:USERPROFILE\.codex\astra-luna-native\runtime\astra-luna.py" doctor
py -3 "$env:USERPROFILE\.codex\astra-luna-native\runtime\astra-luna.py" uninstall --dry-run
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
project policy. refresh updates the global public capability snapshot and
home-level state; it does not immediately rewrite every project's cache.

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
