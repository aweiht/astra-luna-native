#!/usr/bin/env python3
"""Run with Python 3.11+: python3 astra-luna.py --help."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8')
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required; no pip packages are needed.")

if __name__ == "__main__":
    # Also support the verifier's isolated interpreter (-I): import only the
    # package beside this trusted entry, never the generated project directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from astra_luna.cli import main
    raise SystemExit(main())
