"""
Remembers which folders each assignment uses (submissions, reports, results), on the server side.

The dashboard used to keep these only in the browser, so results graded elsewhere (another browser,
a script, a cleared cache) were on disk but invisible in the app. Grading runs record their folders
here, and the dashboard falls back to this registry when the browser has nothing saved.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Dict, Optional

from grader.config import PROJECT_ROOT

REGISTRY = PROJECT_ROOT / "assignment_folders.json"
_lock = threading.Lock()


def _abs(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    path = Path(p)
    return str(path if path.is_absolute() else (PROJECT_ROOT / path).resolve())


def _load() -> Dict[str, Dict[str, str]]:
    try:
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(assignment_id: str, submissions: Optional[str], reports: Optional[str], results: Optional[str]) -> None:
    """Record the folders an assignment was graded with (absolute paths)."""
    if not assignment_id or not submissions:
        return
    entry = {"submissions": _abs(submissions), "reports": _abs(reports or "reports"), "results": _abs(results or "results")}
    with _lock:
        data = _load()
        if data.get(assignment_id) == entry:
            return
        data[assignment_id] = entry
        tmp = REGISTRY.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, REGISTRY)


def guess_from_submissions(submissions: str) -> Dict[str, str]:
    """'Week 5 Submissions' -> sibling 'Week 5 Check/Results' (or 'Result') and 'Week 5 Check/Reports' when they exist."""
    sub = Path(submissions)
    out = {"submissions": str(sub)}
    m = re.match(r"(.*?)\s*Submissions?$", sub.name, re.IGNORECASE)
    if m:
        check = sub.parent / f"{m.group(1)} Check".strip()
        if check.is_dir():
            for name in ("Results", "Result"):
                if (check / name).is_dir():
                    out["results"] = str(check / name)
                    break
            if (check / "Reports").is_dir():
                out["reports"] = str(check / "Reports")
    return out


def known(assignment_id: str) -> Dict[str, str]:
    """Folders for an assignment: recorded ones first, else guessed from its Classroom download folder."""
    entry = _load().get(assignment_id)
    if entry:
        return entry
    try:
        from grader.classroom import load_settings
        link = (load_settings().get("links") or {}).get(assignment_id) or {}
    except Exception:
        link = {}
    if link.get("dest"):
        return guess_from_submissions(link["dest"])
    return {}
