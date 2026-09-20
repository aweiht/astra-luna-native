# Security policy

Please keep vulnerability reports private until a fix or mitigation is
available. Use the repository host's private vulnerability reporting channel
when one is available. Otherwise contact the maintainer privately before
opening a public issue.

Do not include Codex auth.json, API keys, access tokens, cookies, transcripts,
internal session files, account data, local absolute paths, or private
verification evidence in a report. Include the release version, operating
system and architecture, the exact command, a minimal redacted reproduction,
and the observed impact.

The 0.5.0 runtime is a Python 3.11+ standard-library program. It invokes the
official Codex CLI only when a command requires it and does not provide a
daemon, MCP server, lifecycle Hook, background scheduler, provider switch, or
independent service. The installer limits writes to the selected Codex home
and project cache, verifies managed hashes, creates a private backup, writes
atomically, and preserves unrelated changes.

A useful report identifies whether the issue occurs during dry-run,
installation, offline doctor, selection, refresh, live verification, recovery,
or uninstall. Never attach credentials or unredacted model or account output.
