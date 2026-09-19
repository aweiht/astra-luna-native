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
