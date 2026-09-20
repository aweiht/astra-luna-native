# Codex Adaptive Agents README illustrations

- `workflow-en.svg` and `workflow-zh.svg` are usage diagrams, not screenshots of a running Codex task.
- `cli-check.png` presents selected fields from actual local `doctor` output and the actual `select` result captured on 2026-09-19, using version 0.4.0 and Codex CLI 0.147.0 on macOS arm64. It is a typeset terminal-output illustration, not a screenshot of the Codex app; the historical capture remains unchanged.
- `cli-check.json` contains the public data used for that image. `doctor` fields are extracted from their nested JSON objects for readability. Absolute private paths and unrelated response fields are omitted; the source-entry commands shown are equivalent to the installed-entry commands used for capture.

The illustrated installation already existed. Its public capability and policy caches were refreshed before capture. `READY_FALLBACK` means local readiness with a conservative supported role; `live_verified: false` is intentional. These commands did not start a model task, and the image is not evidence of a fresh installation or real model delegation. A different machine or date can select a different role or return a different status.

All images are original project documentation assets and use the repository's [Apache-2.0 license](../../LICENSE).
