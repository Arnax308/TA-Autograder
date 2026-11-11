"""LMS bulk download -> one <roll>.zip per student."""
import zipfile

from grader.extractor import Extractor
from grader.lms_import import main, scan
from grader.main import list_submission_items


def _moodle_download(path):
    """A Moodle-style 'download all submissions' archive with the usual mess."""
    inner = path.parent / "inner.zip"
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("lab3/lexer.c", "int main(void){return 0;}\n")
    with zipfile.ZipFile(path, "w") as z:
        root = "CD-Lab3-12345/"
        z.write(inner, root + "Asha Rao_101_assignsubmission_file_/cs23b1001_lab3.zip")         # roll in file name
        z.writestr(root + "Ravi K_102_assignsubmission_file_/lexer.c", "int main(void){return 1;}\n")  # no roll: names.csv
        z.writestr(root + "Meera S_103_assignsubmission_file_/q1/CS23B1003 parser.c", "int main(void){}\n")
        z.writestr(root + "Dup Copy_104_assignsubmission_file_/cs23b1003.c", "int main(void){}\n")  # duplicate roll
        z.writestr(root + "Nobody_105_assignsubmission_file_/main.c", "int main(void){}\n")       # unmatched
        z.writestr(root + "cs23b1006.c", "int main(void){}\n")                                     # loose file
        z.writestr("__MACOSX/._junk", "x")


def test_import_moodle_style_download(tmp_path, monkeypatch):
    monkeypatch.setenv("TA_STUDENTS", "")
    dl, out = tmp_path / "download.zip", tmp_path / "Lab 3 Submissions"
    _moodle_download(dl)
    names = tmp_path / "names.csv"
    names.write_text("name,roll\nRavi K,cs23b1002\n", encoding="utf-8")

    code = main([str(dl), str(out), "--names", str(names)])
    assert code == 1  # one unmatched student and one duplicate are reported

    assert sorted(p.name for p in out.glob("*.zip")) == ["cs23b1001.zip", "cs23b1002.zip", "cs23b1003.zip", "cs23b1006.zip"]
    report = (out / "import_report.csv").read_text(encoding="utf-8")
    assert "Nobody_105" in report and "no roll number found" in report and "duplicate of" in report

    # The grader sees one submission per student, and unpacks the student's own inner zip when grading
    assert [p.stem for _, p in list_submission_items(out)] == ["cs23b1001", "cs23b1002", "cs23b1003", "cs23b1006"]
    extracted, err = Extractor.extract_zip(out / "cs23b1001.zip", target_dir=tmp_path / "x")
    assert err is None
    assert [f.name for f in Extractor.collect_source_files(extracted)] == ["lexer.c"]


def test_reimport_keeps_unchanged_zips(tmp_path, monkeypatch):
    """Re-importing a newer download must only touch students whose files changed (others are not re-graded)."""
    import os
    src, out = tmp_path / "dl", tmp_path / "subs"
    for roll, body in (("cs23b1001", "int main(void){return 0;}\n"), ("cs23b1002", "int main(void){return 1;}\n")):
        (src / f"{roll}_x").mkdir(parents=True)
        (src / f"{roll}_x" / "a.c").write_text(body)
    main([str(src), str(out)])
    old = {p.name: p.stat().st_mtime_ns for p in out.glob("*.zip")}
    for p in out.glob("*.zip"):
        os.utime(p, ns=(1_000_000_000, 1_000_000_000))  # make "old" unmistakable
    (src / "cs23b1002_x" / "a.c").write_text("int main(void){return 2;}\n")  # resubmission
    main([str(src), str(out)])
    assert (out / "cs23b1001.zip").stat().st_mtime_ns == 1_000_000_000  # unchanged: left alone
    assert (out / "cs23b1002.zip").stat().st_mtime_ns > 1_000_000_000  # changed: rewritten
    assert set(old) == {"cs23b1001.zip", "cs23b1002.zip"}


def test_custom_regex_and_folder_input(tmp_path):
    src = tmp_path / "download"
    (src / "2023CSE0042 - Kiran").mkdir(parents=True)
    (src / "2023CSE0042 - Kiran" / "a.c").write_text("int main(void){}\n")
    entries = scan(src, r"(\d{4}CSE\d{4})")
    assert [(e.roll, e.how) for e in entries] == [("2023cse0042", "name")]
