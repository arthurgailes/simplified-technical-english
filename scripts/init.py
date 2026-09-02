#!/usr/bin/env python
"""One-time setup: turn your licensed ASD-STE100 PDF into the data the checker needs.

The ASD-STE100 standard is copyright ASD and is not part of this repository, so
the dictionary and rule files the checker reads are absent until you build them
from your own copy of the PDF. This script does that build, and tells you where
to get the PDF if it is missing.

    python scripts/init.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "references" / "ASD-STE100_ISSUE9.pdf"
DICT = ROOT / "assets" / "ste_dictionary.json"
LINK = "https://asd-ste100.org"


def missing_pdf_message() -> str:
    return f"""
------------------------------------------------------------------------
  The ASD-STE100 standard is not in this repository (it is copyright ASD).
  You supply your own copy. It is free.

    1. Get the PDF here:   {LINK}
       (fill in the short request form; ASD sends you the PDF)

    2. Save it exactly as:
       {PDF}

    3. Run this script again:
       python scripts/init.py
------------------------------------------------------------------------
"""


def main() -> int:
    if not PDF.exists():
        print(missing_pdf_message())
        return 1

    print(f"Found {PDF.name}. Building the dictionary and rule files...")
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_dictionary.py")],
                       check=True)
    except FileNotFoundError:
        print("Python could not start build_dictionary.py. Is Python on your PATH?")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"\nThe build failed (exit {exc.returncode}).")
        print("build_dictionary.py needs PyMuPDF. Install it:  pip install pymupdf")
        return 1

    if not DICT.exists():
        print("\nThe build ran but produced no dictionary. Check the messages above.")
        return 1

    print("\nSetup is complete. Check a file like this:")
    print("    python scripts/ste_check.py YOURFILE.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
