Native Python smoke contract

The root must delegate exactly two direct native Luna children, using the
preselected role supplied by the selector.  The two children run concurrently
and must not delegate further.  One child owns only normalize_tags.py and the
other owns only unique_numbers.py.  The root does not implement either
function itself.

normalize_tags.py is a Python 3.11 standard-library program.  It reads text
lines from stdin and writes normalized text lines.  A value is trimmed only
for ASCII spaces, tabs and carriage returns, ASCII A-Z is lowercased, empty
values are dropped, and duplicates are removed in first appearance order.

unique_numbers.py is a Python 3.11 standard-library program.  It reads text
lines from stdin and writes one canonical decimal integer per output line.
Values must be signed decimal integers in the inclusive range -10000..10000;
leading zeroes and a leading plus are accepted.  Duplicates are removed in
first appearance order.  Any invalid input must exit nonzero.

The initial scripts are deliberately failing stubs.  An external trusted
verifier performs the independent check after the root turn through the
official workspace sandbox:

  codex sandbox -P :workspace -C PROJECT -- python EXE internal-smoke-check --project PROJECT

Only normalize_tags.py and unique_numbers.py may change.  AGENTS.md and
contract.md are immutable.  Do not create tests, reports, helper files or
caches outside .codex-adaptive-agents, and do not use network requests, Go, or synthetic
waits.  The verifier does not read session stores, authentication files,
messages, reasoning, or raw command output.
