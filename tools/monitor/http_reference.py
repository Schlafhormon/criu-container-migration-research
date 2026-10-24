#!/usr/bin/env python3
"""Repository entry point for the independent HTTP reference client."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from clm.http_reference import main

if __name__ == "__main__":
    raise SystemExit(main())
