"""Google Classroom import, exercised against an in-memory fake of the Classroom API."""

import io
import json
import zipfile

import pytest

from grader import classroom


class _Req:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _Lister:
    """Mimics googleapiclient's chained resource().list(...).execute() with one page."""

    def __init__(self, key, items):
        self.key, self.items = key, items

    def list(self, pageToken=None, **kwargs):
        return _Req({self.key: self.items})


class FakeClassroom:
    def __init__(self, data):
        self.data = data

    def courses(self):
        d = self.data
        outer = self

        class Courses(_Lister):
            def courseWork(self_inner):
                class CW(_Lister):
                    def studentSubmissions(self_cw):
                        return _Lister("studentSubmissions", d["submissions"])
                return CW("courseWork", d["coursework"])

            def students(self_inner):
                return _Lister("students", d["students"])

        return Courses("courses", d["courses"])


class FakeClient(classroom.ClassroomClient):
    def __init__(self, data, files):
        super().__init__(classroom=FakeClassroom(data), drive=object())
        self.files = files
        self.downloads = []

    def download(self, file_id):
        self.downloads.append(file_id)
        return self.files[file_id]


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, c in files.items():
            zf.writestr(n, c)
    return buf.getvalue()


def _student(uid, name, email):
    return {"userId": uid, "profile": {"name": {"fullName": name}, "emailAddress": email}}


def _sub(uid, state, files, late=False):
    return {"id": f"s{uid}", "userId": uid, "state": state, "late": late,
            "assignmentSubmission": {"attachments": [{"driveFile": {"id": fid, "title": t}} for fid, t in files]}}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(classroom, "SETTINGS_FILE", tmp_path / "classroom.json")
    monkeypatch.setenv("TA_STUDENTS", "")
    data = {
        "courses": [{"id": "c1", "name": "OOPS Lab"}],
        "coursework": [{"id": "w1", "title": "Lab 4", "creationTime": "2026-10-01T00:00:00Z"}],
        "students": [
            _student("u1", "Asha", "CS25B1089@iiitdm.ac.in"),
            _student("u2", "Ravi", "cs25b1090@iiitdm.ac.in"),
            _student("u3", "Meera", "meera.k@iiitdm.ac.in"),
            _student("u4", "Kiran", "cs25b2004@iiitdm.ac.in"),
            _student("u5", "Dev", "cs25b2005@iiitdm.ac.in"),
        ],
        "submissions": [
            _sub("u1", "TURNED_IN", [("f1", "my_lab4 final.zip")]),
            _sub("u2", "TURNED_IN", [("f2", "main.c"), ("f3", "rational.h")], late=True),
            _sub("u3", "TURNED_IN", [("f4", "lab.zip")]),
            _sub("u4", "CREATED", []),
            _sub("u5", "TURNED_IN", [("f5", "lab4.rar")]),
        ],
    }
    files = {
        "f1": ("my_lab4 final.zip", _zip_bytes({"Lab4/q2/main.c": "int main(){return 0;}"})),
        "f2": ("main.c", b"int main(){return 0;}"),
        "f3": ("rational.h", b"#pragma once"),
        "f4": ("lab.zip", _zip_bytes({"a.c": "int main(){return 0;}"})),
        "f5": ("lab4.rar", b"Rar!fake"),
    }
    return data, files, tmp_path / "Week 4 Submissions"


def test_roll_ids_come_from_email():
    assert classroom.derive_roll_id("CS25B1089@iiitdm.ac.in", classroom.DEFAULT_ID_PATTERN, {}) == "1089"
    assert classroom.derive_roll_id("meera.k@iiitdm.ac.in", classroom.DEFAULT_ID_PATTERN, {}) is None
    assert classroom.derive_roll_id("meera.k@iiitdm.ac.in", classroom.DEFAULT_ID_PATTERN, {"meera.k@iiitdm.ac.in": "2010"}) == "2010"
    assert classroom.derive_roll_id("cs25b1089@x.in", r"(cs\d{2}b\d{4})", {}) == "cs25b1089"
    with pytest.raises(classroom.ClassroomError):
        classroom.derive_roll_id("a@b", "(", {})


def test_rows_show_states_and_ids(env):
    data, files, dest = env
    table = classroom.build_rows(FakeClient(data, files), "c1", "w1", str(dest))
    by_name = {r["name"]: r for r in table["rows"]}
    assert by_name["Asha"]["roll_id"] == "1089" and by_name["Asha"]["local"] == "new"
    assert by_name["Ravi"]["late"] is True
    assert by_name["Meera"]["roll_id"] is None
    assert by_name["Kiran"]["submitted"] is False
    c = table["counts"]
    assert (c["students"], c["submitted"], c["missing"], c["no_id"], c["late"]) == (5, 4, 1, 1, 1)


def test_sync_downloads_and_renames(env):
    data, files, dest = env
    client = FakeClient(data, files)
    summary = classroom.run_sync("c1", "w1", str(dest), client=client)

    assert sorted(summary["downloaded"]) == ["1089", "1090", "2005"]
    assert [s["name"] for s in summary["skipped"]] == ["Meera"]
    assert summary["unsupported"] == ["2005.rar"]
    assert zipfile.ZipFile(dest / "1089.zip").namelist() == ["Lab4/q2/main.c"]
    # Loose files are packed into one zip named after the roll ID
    assert sorted(zipfile.ZipFile(dest / "1090.zip").namelist()) == ["main.c", "rational.h"]
    assert (dest / "2005.rar").exists()
    assert json.loads((dest / classroom.MANIFEST_NAME).read_text())["students"]["1090"]["late"] is True


def test_resync_only_fetches_new_and_resubmitted(env):
    data, files, dest = env
    classroom.run_sync("c1", "w1", str(dest), client=FakeClient(data, files))

    # Nothing changed: nothing is downloaded again
    again = FakeClient(data, files)
    assert classroom.run_sync("c1", "w1", str(dest), client=again)["downloaded"] == []
    assert again.downloads == []

    # Asha resubmits a new file and Kiran turns in late
    data["submissions"][0] = _sub("u1", "TURNED_IN", [("f9", "fixed.zip")])
    data["submissions"][3] = _sub("u4", "TURNED_IN", [("f4", "lab.zip")], late=True)
    files["f9"] = ("fixed.zip", _zip_bytes({"fixed.c": "int main(){return 1;}"}))
    third = FakeClient(data, files)
    summary = classroom.run_sync("c1", "w1", str(dest), client=third)
    assert summary["updated"] == ["1089"]
    assert summary["downloaded"] == ["2004"]
    assert zipfile.ZipFile(dest / "1089.zip").namelist() == ["fixed.c"]


def test_override_and_duplicate_ids(env):
    data, files, dest = env
    # Meera gets a manual ID; it is remembered for later syncs
    summary = classroom.run_sync("c1", "w1", str(dest), overrides={"meera.k@iiitdm.ac.in": "2010"}, client=FakeClient(data, files))
    assert "2010" in summary["downloaded"] and (dest / "2010.zip").exists()
    assert classroom.load_settings()["overrides"]["meera.k@iiitdm.ac.in"] == "2010"

    # Two students resolving to the same ID are never written over each other
    table = classroom.build_rows(FakeClient(data, files), "c1", "w1", str(dest), overrides={"meera.k@iiitdm.ac.in": "1089"})
    dupes = [r["name"] for r in table["rows"] if r["duplicate_id"]]
    assert sorted(dupes) == ["Asha", "Meera"]


def test_changing_an_id_renames_the_saved_file(env):
    data, files, dest = env
    classroom.run_sync("c1", "w1", str(dest), client=FakeClient(data, files))
    classroom.run_sync("c1", "w1", str(dest), overrides={"cs25b1090@iiitdm.ac.in": "1091"},
                       only_emails=["cs25b1090@iiitdm.ac.in"], client=FakeClient(data, files))
    assert (dest / "1091.zip").exists() and not (dest / "1090.zip").exists()


def test_link_is_saved_for_one_click_sync(env):
    data, files, dest = env
    classroom.run_sync("c1", "w1", str(dest), link={"assignment_id": "week_4", "course_id": "c1", "coursework_id": "w1"},
                       client=FakeClient(data, files))
    link = classroom.load_settings()["links"]["week_4"]
    assert link["coursework_id"] == "w1" and link["dest"] == str(dest)


def test_client_secrets_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(classroom, "SECRETS_DIR", tmp_path)
    monkeypatch.setattr(classroom, "CLIENT_FILE", tmp_path / "c.json")
    with pytest.raises(classroom.ClassroomError):
        classroom.save_client_secrets('{"web": {}}')
    with pytest.raises(classroom.ClassroomError):
        classroom.save_client_secrets("not json")
    classroom.save_client_secrets('{"installed": {"client_id": "x"}}')
    assert (tmp_path / "c.json").exists()


def test_nested_zip_is_extracted(tmp_path):
    from grader.extractor import Extractor
    outer = tmp_path / "1090.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("inner.zip", _zip_bytes({"src/main.c": "int main(){return 0;}"}))
    extracted, err = Extractor.extract_zip(outer, target_dir=tmp_path / "out")
    assert err is None
    assert [p.name for p in Extractor.collect_source_files(extracted)] == ["main.c"]


def test_scope_aliases_count_as_granted():
    granted = ("https://www.googleapis.com/auth/classroom.student-submissions.students.readonly "
               "https://www.googleapis.com/auth/classroom.profile.emails https://www.googleapis.com/auth/drive.readonly "
               "https://www.googleapis.com/auth/classroom.rosters.readonly openid "
               "https://www.googleapis.com/auth/classroom.courses.readonly https://www.googleapis.com/auth/userinfo.email")
    assert classroom.missing_access(granted) == []
    assert classroom.missing_access(granted.replace("https://www.googleapis.com/auth/drive.readonly", "")) == ["download submitted files from Drive"]
    import os
    assert os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE") == "1"


def test_only_my_students_are_downloaded(env, monkeypatch):
    data, files, dest = env
    data["students"].append(_student("u6", "Other Batch", "cs24b1089@iiitdm.ac.in"))
    data["submissions"].append(_sub("u6", "TURNED_IN", [("f4", "lab.zip")]))
    monkeypatch.setenv("TA_STUDENTS", "cs25b1089-cs25b1103")

    table = classroom.build_rows(FakeClient(data, files), "c1", "w1", str(dest))
    mine = sorted(r["name"] for r in table["rows"] if r["in_scope"])
    assert mine == ["Asha", "Ravi"]
    assert table["counts"]["students"] == 2 and table["counts"]["out_of_scope"] == 4
    # cs24b1089 also resolves to 1089 but must not block the real 1089 as a "duplicate"
    assert not next(r for r in table["rows"] if r["name"] == "Asha")["duplicate_id"]

    summary = classroom.run_sync("c1", "w1", str(dest), client=FakeClient(data, files))
    assert sorted(summary["downloaded"]) == ["1089", "1090"]
    assert not (dest / "2005.rar").exists()
