#!/usr/bin/env python3
"""Run the source checkout using dependencies installed in the active environment."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
try:
    from pdf_workbench.app import main
except ImportError as error:
    print(f"Eine Abhängigkeit fehlt: {error}\nBitte Python 3.11+ verwenden und im Projekt `python -m pip install .` ausführen.", file=sys.stderr)
    raise SystemExit(1) from error

raise SystemExit(main())
