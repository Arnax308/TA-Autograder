#!/usr/bin/env python3
"""Converts an LMS 'download all submissions' archive into <roll>.zip files. See grader/lms_import.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from grader.lms_import import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
