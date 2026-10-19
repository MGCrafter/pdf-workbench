"""PyInstaller entry point."""
import sys

if "--self-test" in sys.argv:
    sys.argv.remove("--self-test")
    from smoke import main
else:
    from pdf_workbench.app import main

raise SystemExit(main())
