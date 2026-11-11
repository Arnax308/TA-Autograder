"""
Turns an LMS bulk download into one `<roll>.zip` per student, the layout the grader expects.

Typical LMS (e.g. Moodle) "download all submissions" archives look like

    Lab3-12345.zip
      Asha Rao_101_assignsubmission_file_/cs23b1001_lab3.zip
      Ravi K_102_assignsubmission_file_/lexer.c
      ...

or a flat list of uploaded files. Every top-level folder (or top-level file) is one student. The roll number is
found with a regex in the folder name, then in the file names inside it; failing that, a CSV of
`name,roll` rows maps the LMS display name; failing that the student is reported and skipped.

Usage:
    python scripts/import_lms_download.py "<download.zip or folder>" "<Lab 3 Submissions>" [--id-regex REGEX]
                                         [--names names.csv] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# e.g. cs23b1001 (also inside "cs23b1001_lab3.zip", where \b would fail on the underscore); use --id-regex otherwise
DEFAULT_ID_REGEX = r"(?i)(?<![a-z0-9])([a-z]{2}\d{2}[a-z]\d{4})(?!\d)"
JUNK = {"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"}


@dataclass
class Entry:
    source: str                  # top-level folder or file name in the download
    roll: Optional[str] = None
    how: str = ""                # where the roll number came from
    files: List[Path] = field(default_factory=list)
    note: str = ""


def _safe_extract(zip_path: Path, dest: Path) -> None:
    """Top-level extraction only (inner zips stay zipped: the grader unpacks them), with zip-slip protection."""
    with zipfile.ZipFile(zip_path) as zf:
        for m in zf.infolist():
            name = m.filename
            if not m.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            norm = os.path.normpath(name.replace("\\", "/"))
            if os.path.isabs(norm) or re.match(r"^[A-Za-z]:", norm) or norm.startswith(".."):
                continue
            target = (dest / norm).resolve()
            if not target.is_relative_to(dest.resolve()):
                continue
            if m.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _lms_name(folder_name: str) -> str:
    """'Asha Rao_101_assignsubmission_file_' -> 'asha rao' (the display name part)."""
    return re.split(r"_\d+_assign", folder_name, maxsplit=1)[0].split("_")[0].strip().lower()


def _load_names(csv_path: Optional[Path]) -> Dict[str, str]:
    if not csv_path:
        return {}
    out = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if len(row) >= 2 and row[0].strip() and row[1].strip() and row[0].strip().lower() != "name":
                out[row[0].strip().lower()] = row[1].strip().lower()
    return out


def _find_roll(texts: List[str], id_re: re.Pattern) -> Optional[str]:
    for t in texts:
        m = id_re.search(t)
        if m:
            return (m.group(1) if m.groups() else m.group(0)).lower()
    return None


def scan(download: Path, id_regex: str = DEFAULT_ID_REGEX, names: Optional[Dict[str, str]] = None) -> List[Entry]:
    """Groups the download into one Entry per student (download must be an extracted folder)."""
    id_re = re.compile(id_regex)
    names = names or {}
    tops = [p for p in sorted(download.iterdir(), key=lambda p: p.name.lower()) if p.name not in JUNK and not p.name.startswith(".")]
    # A download that is a single wrapper folder ("Lab3-12345/") holds the real per-student entries one level down
    while len(tops) == 1 and tops[0].is_dir() and not id_re.search(tops[0].name):
        tops = [p for p in sorted(tops[0].iterdir(), key=lambda p: p.name.lower()) if p.name not in JUNK and not p.name.startswith(".")]
    entries = []
    for top in tops:
        files = [top] if top.is_file() else [f for f in sorted(top.rglob("*")) if f.is_file()
                                              and not any(part in JUNK or part.startswith(".") for part in f.relative_to(top).parts)]
        e = Entry(source=top.name, files=files)
        roll = _find_roll([top.name], id_re)
        if roll:
            e.roll, e.how = roll, "name"
        else:
            roll = _find_roll([f.name for f in files], id_re)
            if roll:
                e.roll, e.how = roll, "file name"
            elif _lms_name(top.name) in names:
                e.roll, e.how = names[_lms_name(top.name)], "names.csv"
        if not files:
            e.note = "empty submission"
        entries.append(e)
    return entries


def write_zips(entries: List[Entry], out_dir: Path, dry_run: bool = False) -> List[Entry]:
    """Writes <roll>.zip per matched entry. Duplicate roll numbers keep the first and are reported."""
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    done: Dict[str, Entry] = {}
    for e in entries:
        if not e.roll or not e.files:
            continue
        if e.roll in done:
            e.note = f"duplicate of {done[e.roll].source}; skipped"
            continue
        done[e.roll] = e
        if dry_run:
            continue
        target = out_dir / f"{re.sub(r'[^A-Za-z0-9_-]', '_', e.roll)}.zip"
        tmp = target.with_suffix(".zip.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            base = e.files[0].parent if len(e.files) == 1 else Path(os.path.commonpath([str(f.parent) for f in e.files]))
            for f in e.files:
                zf.write(f, arcname=str(f.relative_to(base)) if f.is_relative_to(base) else f.name)
        if target.exists() and _same_contents(tmp, target):
            tmp.unlink()  # unchanged: keep the old file (and its date), so the grader does not re-grade this student
            e.note = ""
            e.how += ", unchanged"
        else:
            os.replace(tmp, target)
    return entries


def _same_contents(a: Path, b: Path) -> bool:
    """Same file names and CRCs inside both zips (zip timestamps ignored)."""
    try:
        with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
            key = lambda z: sorted((i.filename, i.CRC, i.file_size) for i in z.infolist() if not i.is_dir())  # noqa: E731
            return key(za) == key(zb)
    except zipfile.BadZipFile:
        return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Convert an LMS bulk download into <roll>.zip files for the grader.")
    ap.add_argument("download", help="The LMS 'download all submissions' zip, or the folder it was extracted to")
    ap.add_argument("out", help="Submissions folder to write <roll>.zip files into (e.g. 'Lab 3 Submissions')")
    ap.add_argument("--id-regex", default=DEFAULT_ID_REGEX, help=f"Regex for the roll number (default {DEFAULT_ID_REGEX})")
    ap.add_argument("--names", type=Path, help="CSV of 'name,roll' rows for students whose files carry no roll number")
    ap.add_argument("--dry-run", action="store_true", help="Only show what would be written")
    a = ap.parse_args(argv)

    src = Path(a.download)
    work = None
    if src.is_file():
        work = Path(tempfile.mkdtemp(prefix="lms_import_"))
        _safe_extract(src, work)
        root = work
    else:
        root = src
    try:
        entries = write_zips(scan(root, a.id_regex, _load_names(a.names)), Path(a.out), a.dry_run)
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)

    ok = [e for e in entries if e.roll and not e.note]
    bad = [e for e in entries if not e.roll or e.note]
    print(f"{'Would write' if a.dry_run else 'Wrote'} {len(ok)} submission(s) to {a.out}")
    for e in ok:
        print(f"  {e.roll:<14} <- {e.source}  ({len(e.files)} file(s), roll from {e.how})")
    if bad:
        print(f"\nNeeds attention ({len(bad)}):")
        for e in bad:
            print(f"  {e.source}: {e.note or 'no roll number found (add it to --names CSV or fix --id-regex)'}")
    report = Path(a.out) / "import_report.csv"
    if not a.dry_run:
        with open(report, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["roll", "source", "files", "roll_from", "note"])
            for e in entries:
                w.writerow([e.roll or "", e.source, len(e.files), e.how, e.note or ("" if e.roll else "no roll number found")])
        print(f"\nReport: {report}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
