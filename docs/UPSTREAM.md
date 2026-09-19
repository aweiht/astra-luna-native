# Implementation and dependencies

Astra / Luna Native contains its own installer, policy selector, managed
instructions, and verification implementation. It does not install, import,
or execute another agent-orchestration project. The public source package is
built from the explicit file list in [release-manifest.json](../release-manifest.json).

## Implementation in this repository

| Component | Responsibility |
| --- | --- |
| `astra_luna/cli.py` | Command parsing, installation flow, and local readiness checks |
| `astra_luna/install.py` | Managed files, plans, receipts, backups, conflicts, and recovery |
| `astra_luna/toml_edit.py` | Targeted Codex configuration edits that preserve unrelated content |
| `astra_luna/policy.py` | Public capability checks, daily role selection, cache, and fallback |
| `astra_luna/platform.py` | Portable files, locks, command invocation, and process lifetime |
| `astra_luna/transport.py` | Temporary official stdio channel for explicit live verification |
| `astra_luna/verify.py` and `astra_luna/assets/` | Native task contracts, independent result checks, and managed agent instructions |
| `scripts/release_python.py` | Source-package allowlist, checksums, and archive validation |

The runtime imports Python standard-library modules and its own local modules.
There are no pip dependencies, vendored orchestration packages, external
installer scripts, or Git submodules in the distribution.

## Actual requirements and data inputs

- **Python 3.11+** runs the scripts. No build or compilation step is needed.
- **The official Codex CLI and a compatible Codex client** supply authentication,
  model access, and native child-agent execution. This project does not implement
  or bundle Codex itself.
- **Public radar data** is fetched from the endpoints declared in `policy.py`
  to inform role selection. Those responses are data, never executable code
  or agent instructions. Missing comparable evidence can lead to a supported
  conservative Max fallback; capability checks still have to pass.
- **Git** is useful for cloning and updating this repository, but downloading
  its source ZIP is also supported.

No other orchestration repository has to be cloned, installed, or kept on disk.
Local readiness, source/installation tests, and real model execution remain
separate checks; see [validation](VALIDATION.md).

## License

The distribution uses the [Apache License 2.0](../LICENSE). Its accompanying
[NOTICE](../NOTICE) describes this distribution and its runtime requirements.
