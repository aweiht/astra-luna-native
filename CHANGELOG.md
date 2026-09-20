# Changelog

## Unreleased

- Keep a fresh capability snapshot when the shared probe deadline expires,
  without extending its age; make `refresh --project` retry that project's
  same-day failure without changing other project caches.
- Retain sanitized public child model/effort metadata and reject contradictory
  spawn requests; missing public evidence remains unknown.
- Supervise POSIX commands with identity-checked descendant cleanup and an
  explicit completion acknowledgement; document the macOS tracking boundary.
- Add per-work-package `process-init`, `process-run`, `process-check`, and
  `process-cleanup` bookkeeping for bounded long commands; keep root cleanup
  separate from leaf reporting and make explicitly retained work visible.
- Clarify that leaves report requests for new Agents to the root instead of
  starting further delegation themselves.
- Accept case aliases of the same installation directory without accepting
  another home or a symbolic-link alias.
- Run CI on every push and pull request, including translated documentation,
  images and ignore rules; honor custom homes in Windows standalone examples.

- Define a delegation gate for substantive work: assess scope, explicit
  ownership and acceptance, and expected payoff before work starts; honor an
  explicit user request to delegate when the host permits it.
- Clarify direct-completion cases, parallel versus serial packages, the five
  child limit, re-evaluation triggers, and evidence-based selector, role, and
  tool failure reporting.
- Synchronize the English and Simplified Chinese usage guidance without adding
  dependencies or third-party notice entries.

## Codex-assisted setup and implementation scope — 2026-09-20

- Put copyable installation and post-installation verification prompts near
  the top of both READMEs; keep real model verification explicitly optional.
- Clarify the project's own implementation and actual runtime/data dependencies.
- Reduce NOTICE to this distribution's license and runtime statement.

## Documentation and execution summary — 2026-09-19

- Add English and Simplified Chinese quick starts covering installation,
  everyday Codex use, local readiness and optional live verification.
- Add workflow illustrations and a dated CLI output image with public source
  data and explicit validation scope; include the assets in the source ZIP.
- Publish the compact execution summary: root model name, actual child roles,
  effort levels, counts and observed concurrency. The summary does not claim to
  read the root window's reasoning setting.
- Correct the platform evidence links and explain model-catalogue prerequisites.

## 0.4.0 — 2026-09-15 (source published)

This transition defines a Python-only source and runtime ZIP using Python 3.11+
and the standard library.

- The entry script is astra-luna.py; the source and runtime payload use the
  same explicit allowlist.
- The package has no pip dependency, Go requirement, compiled binary, daemon,
  MCP server, lifecycle Hook, or background service.
- Installation keeps the standalone entry at
  astra-luna-native/runtime/astra-luna.py under the selected Codex home.
- The package builder writes an in-package and an external SHA256 checksum set,
  rejects unsafe links and symlinks, and refuses to overwrite existing output.
- The workflow defines Python 3.11 and 3.13 checks on macOS, Linux, and
  Windows. A workflow definition is not a completed CI run.
- Windows, Linux and macOS CI passed source, isolated installation lifecycle
  and ZIP checks under Python 3.11 and 3.13. Real native task evidence remains
  macOS only; see [validation](docs/VALIDATION.md).
- Fixed Windows batch command quote handling. UTC needs no IANA database;
  the default local calendar day follows the host operating system.
- Lifecycle validation now matches every revision turn by its unique ID and
  measures overlap only inside active turn intervals. It accepts either
  ordering of completion and final idle notifications while still requiring
  successful completion and a later idle metadata read.
- Daily selection uses the system local calendar day. An expired public
  capability snapshot can still locate the client for a fresh catalogue
  probe; a failed probe never renews an old snapshot's timestamp.
- Installation previews report the current change, retaining original values
  separately in the receipt for uninstall and recovery.


## 0.3.0 — 2026-09-15 (historical predecessor)

The 0.3.0 work packaged the earlier native route as a pure Go single binary.
Its source, binary archives, and validation records remain repository history;
they are not requirements or live evidence for 0.4.0.

- Added the earlier astra-luna entry point and native delegation commands.
- Added five bounded Luna role definitions selected by a daily policy.
- Added transactional installation, hash checks, private backups, and project
  cache support.
- Added source and prebuilt release manifests with package checksums.

The current 0.4.0 contract and package contents are defined by README.md,
docs/PUBLIC_GUIDE.md, and release-manifest.json.
