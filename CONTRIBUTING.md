# Contributing

Astra / Luna Native 0.4.0 is a Python 3.11+ standard-library distribution.
Keep changes small, observable, and compatible with the official Codex CLI. The
runtime does not add a daemon, MCP server, lifecycle Hook, background
scheduler, or provider switch.

## Development requirements

Use Python 3.11 or newer. The project has no pip dependencies, compiled
binary, or Go requirement for the 0.4.0 runtime and source package.

Run the repository checks before proposing a change:

~~~sh
python3 -m unittest discover -s tests_python -p 'test_*.py' -v
python3 scripts/release_python.py --output-dir /tmp/astra-luna-release
~~~

On Windows, use the Python launcher:

~~~powershell
py -3 -m unittest discover -s tests_python -p 'test_*.py' -v
py -3 scripts/release_python.py --output-dir "$env:TEMP\astra-luna-release"
~~~

The release test builds in an isolated temporary directory and checks the ZIP
member set, package checksums, external checksums, safe links, and refusal to
overwrite an existing archive. Do not use the repository historical dist
packages as current 0.4.0 evidence.

## Release packaging

The checked-in release manifest is the package allowlist. Source equals runtime
for this release: the ZIP contains astra-luna.py, the astra_luna modules and
public smoke assets, tests_python, public documents, license notices, VERSION,
the manifest, the release script, and package checksums. It excludes Go code,
historical Python scripts, private development evidence, local state, templates
and old archives.

Build a private verification ZIP with:

~~~sh
python3 scripts/release_python.py --output-dir /tmp/astra-luna-release
~~~

The builder never scans the whole worktree. It rejects source symlinks, unsafe
archive members, private Markdown paths, broken local Markdown links, and an
output archive or checksum file that already exists. The in-package SHA256SUMS
covers every regular payload file except itself. The external SHA256SUMS
covers every archive produced by that invocation.

## Live verification

A live verification invokes the official Codex CLI through a temporary stdio
app-server test channel. It can consume the user's real Codex allowance and is
bounded at 600 seconds:

~~~sh
python3 astra-luna.py verify --live --project /path/to/project   --output /path/to/verification-output
~~~

Run this only when live evidence is intended. Report source tests, offline
doctor checks, package inspection, local runtime behavior, CI results, and
live model runs as separate evidence layers. The temporary stdio channel is a
test transport; it is not a service installed by this project.

## Scope and reports

Keep credentials, local absolute paths, authentication files, transcripts,
account data, and private evidence out of commits and issue reports. Do not
change user configuration outside the managed installation scope. The
installer creates a private backup, verifies hashes, writes atomically, and
preserves unrelated changes.

Tie user-facing claims to the observed layer. A workflow definition is not a
completed CI run, and a Python or package test does not prove model access or
live delegation. Update public documents and the manifest together when a
shipped command or package file changes. See SECURITY.md for private
vulnerability reporting and docs/VALIDATION.md for the current validation
boundary.
