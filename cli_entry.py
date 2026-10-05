"""Console entry point used by the PyInstaller build (``blazeng-cli.exe``)."""

import sys

from src.cli import main

if __name__ == "__main__":
    sys.exit(main())
