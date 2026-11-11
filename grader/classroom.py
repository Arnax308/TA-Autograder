"""
Google Classroom import: lists a course's assignments and student submissions, then downloads each
student's attachments into the submissions folder as <roll-id>.zip.

Roll IDs come from the student's email (configurable regex, e.g. cs25b1089@... -> 1089) or from a
manual override saved per email. A small manifest in the download folder remembers which Drive files
were saved, so re-syncing only downloads new submissions and resubmissions.

Only read-only Google scopes are requested. Credentials and tokens live in <project>/.secrets/.
"""

from __future__ import annotations
import io
import json
import os
import re
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from grader.config import PROJECT_ROOT, SystemConfig

SECRETS_DIR = PROJECT_ROOT / ".secrets"
CLIENT_FILE = SECRETS_DIR / "google_client.json"
TOKEN_FILE = SECRETS_DIR / "google_token.json"
SETTINGS_FILE = PROJECT_ROOT / "classroom.json"
MANIFEST_NAME = ".classroom_sync.json"

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    "https://www.googleapis.com/auth/classroom.profile.emails",
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

# Google may grant a scope under an older alias (e.g. coursework.students -> student-submissions.students).
# oauthlib treats any difference as an error and discards the token, so tolerate it and check access ourselves.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

# Each required permission, with every scope name Google may return for it
REQUIRED_ACCESS = {
    "see your courses": ["classroom.courses.readonly", "classroom.courses"],
    "see students' submissions": ["classroom.coursework.students.readonly", "classroom.student-submissions.students.readonly",
                                  "classroom.coursework.students"],
    "see the class roster": ["classroom.rosters.readonly", "classroom.rosters"],
    "see students' email addresses": ["classroom.profile.emails"],
    "download submitted files from Drive": ["drive.readonly", "drive"],
}

DEFAULT_ID_PATTERN = r"(\d{4})@"
SUBMITTED_STATES = {"TURNED_IN", "RETURNED"}
ARCHIVE_EXTS = (".zip",)
UNSUPPORTED_ARCHIVES = (".rar", ".7z", ".tar", ".gz", ".tgz")

_lock = threading.Lock()


class ClassroomError(Exception):
    """A problem the user can act on (missing setup, permission denied, ...)."""


# ------------------------------------------------------------------ settings

def load_settings() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    data.setdefault("id_pattern", DEFAULT_ID_PATTERN)
    data.setdefault("overrides", {})
    data.setdefault("links", {})
    return data


def save_settings(data: Dict[str, Any]) -> None:
    with _lock:
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)


def derive_roll_id(email: str, pattern: str, overrides: Dict[str, str]) -> Optional[str]:
    """Roll ID for a student: a saved override wins, otherwise the first capture group of the pattern."""
    email = (email or "").strip().lower()
    if email in overrides and overrides[email].strip():
        return sanitize_id(overrides[email])
    if not email or not pattern:
        return None
    try:
        m = re.search(pattern, email, re.IGNORECASE)
    except re.error as e:
        raise ClassroomError(f"Roll ID pattern is not a valid regular expression: {e}")
    if not m:
        return None
    return sanitize_id(m.group(1) if m.groups() else m.group(0))


def sanitize_id(value: str) -> Optional[str]:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]", "", str(value or "").strip())
    return cleaned or None


# ------------------------------------------------------------------ auth

_login_state: Dict[str, Any] = {"running": False, "error": None}


def save_client_secrets(content: str) -> None:
    try:
        data = json.loads(content)
    except ValueError:
        raise ClassroomError("That file is not valid JSON. Download the OAuth client JSON from Google Cloud Console.")
    if "installed" not in data:
        if "web" in data:
            raise ClassroomError("This is a 'Web application' client. Create an OAuth client of type 'Desktop app' instead.")
        raise ClassroomError("This JSON is not an OAuth client file (expected an 'installed' section).")
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT_FILE.write_text(json.dumps(data), encoding="utf-8")


def _load_credentials():
    if not TOKEN_FILE.exists():
        return None
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    try:
        # Use the scopes Google actually granted (stored in the token), not the names we asked for
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
    except Exception:
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            return None
    return creds if creds and creds.valid else None


def status() -> Dict[str, Any]:
    creds = _load_credentials()
    email = None
    if creds:
        try:
            meta = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            email = meta.get("account_email")
        except Exception:
            pass
    return {
        "client_configured": CLIENT_FILE.exists(),
        "connected": bool(creds),
        "account_email": email,
        "login_running": _login_state["running"],
        "login_error": _login_state["error"],
    }


def start_login() -> None:
    """Runs Google's installed-app OAuth flow in the background (opens the consent page in the browser)."""
    if not CLIENT_FILE.exists():
        raise ClassroomError("Upload your Google OAuth client file first.")
    if _login_state["running"]:
        return

    def run():
        _login_state.update(running=True, error=None)
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
            creds = flow.run_local_server(
                port=0, open_browser=True, timeout_seconds=300, prompt="consent",
                success_message="Signed in. You can close this tab and return to the autograder."
            )
            granted = getattr(creds, "granted_scopes", None) or creds.scopes or []
            if isinstance(granted, str):
                granted = granted.split()
            missing = missing_access(granted)
            if missing:
                raise ClassroomError(
                    "Google did not grant access to " + ", ".join(missing) +
                    ". Sign in again and tick every permission box on the consent screen."
                )
            token = json.loads(creds.to_json())
            token["scopes"] = list(granted)
            token["account_email"] = _fetch_account_email(creds)
            SECRETS_DIR.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(json.dumps(token), encoding="utf-8")
        except Exception as e:
            _login_state["error"] = _friendly_error(e)
        finally:
            _login_state["running"] = False

    threading.Thread(target=run, daemon=True).start()


def missing_access(granted_scopes) -> List[str]:
    """Names of required permissions not covered by the granted scopes (any known alias counts)."""
    if isinstance(granted_scopes, str):
        granted_scopes = granted_scopes.split()
    short = {s.rsplit("/", 1)[-1] for s in granted_scopes}
    return [label for label, names in REQUIRED_ACCESS.items() if not short.intersection(names)]


def _fetch_account_email(creds) -> Optional[str]:
    try:
        import requests
        r = requests.get("https://openidconnect.googleapis.com/v1/userinfo",
                         headers={"Authorization": f"Bearer {creds.token}"}, timeout=15)
        return r.json().get("email") if r.ok else None
    except Exception:
        return None


def logout() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def _friendly_error(e: Exception) -> str:
    text = str(e)
    try:
        from googleapiclient.errors import HttpError
        if isinstance(e, HttpError):
            status = e.resp.status
            reason = e._get_reason() if hasattr(e, "_get_reason") else text
            if status == 403 and "has not been used" in reason:
                return "The Classroom or Drive API is not enabled in your Google Cloud project. Enable both APIs, wait a minute, and retry."
            if status == 403:
                return f"Google denied access ({reason}). You must be a teacher in this course, and your institution must allow the Classroom API."
            if status == 401:
                return "Your Google sign-in expired. Sign in again."
            return f"Google API error {status}: {reason}"
    except ImportError:
        pass
    if "access_denied" in text:
        return "Sign-in was cancelled or blocked. If your app is in testing mode, add your account as a test user."
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "Sign-in timed out before it was completed. Try again."
    return text


# ------------------------------------------------------------------ API access

class ClassroomClient:
    """Thin wrapper over the Classroom and Drive APIs (inject fake services in tests)."""

    def __init__(self, classroom=None, drive=None):
        if classroom is None or drive is None:
            creds = _load_credentials()
            if not creds:
                raise ClassroomError("Not signed in to Google. Connect your account on the Classroom page.")
            from googleapiclient.discovery import build
            classroom = classroom or build("classroom", "v1", credentials=creds, cache_discovery=False)
            drive = drive or build("drive", "v3", credentials=creds, cache_discovery=False)
        self.classroom = classroom
        self.drive = drive

    @staticmethod
    def _paged(method, key: str, **kwargs) -> List[dict]:
        items, token = [], None
        while True:
            resp = method(pageToken=token, **kwargs).execute()
            items.extend(resp.get(key, []))
            token = resp.get("nextPageToken")
            if not token:
                return items

    def _call(self, fn):
        try:
            return fn()
        except ClassroomError:
            raise
        except Exception as e:
            raise ClassroomError(_friendly_error(e))

    def courses(self) -> List[dict]:
        items = self._call(lambda: self._paged(self.classroom.courses().list, "courses", teacherId="me", courseStates=["ACTIVE"], pageSize=100))
        return [{"id": c["id"], "name": c.get("name", ""), "section": c.get("section", "")} for c in items]

    def coursework(self, course_id: str) -> List[dict]:
        items = self._call(lambda: self._paged(self.classroom.courses().courseWork().list, "courseWork",
                                               courseId=course_id, courseWorkStates=["PUBLISHED"], pageSize=100))
        out = []
        for w in items:
            due = w.get("dueDate")
            out.append({
                "id": w["id"], "title": w.get("title", ""), "type": w.get("workType", ""),
                "due": f"{due['year']:04d}-{due['month']:02d}-{due['day']:02d}" if due and due.get("year") else None,
                "created": w.get("creationTime"), "link": w.get("alternateLink"),
            })
        out.sort(key=lambda w: w.get("created") or "", reverse=True)
        return out

    def roster(self, course_id: str) -> Dict[str, dict]:
        items = self._call(lambda: self._paged(self.classroom.courses().students().list, "students", courseId=course_id, pageSize=100))
        return {
            s["userId"]: {"name": (s.get("profile") or {}).get("name", {}).get("fullName", ""),
                          "email": ((s.get("profile") or {}).get("emailAddress") or "").lower()}
            for s in items
        }

    def submissions(self, course_id: str, coursework_id: str) -> List[dict]:
        return self._call(lambda: self._paged(self.classroom.courses().courseWork().studentSubmissions().list,
                                              "studentSubmissions", courseId=course_id, courseWorkId=coursework_id, pageSize=100))

    def download(self, file_id: str) -> tuple:
        """Returns (filename, bytes) for a Drive file attached to a submission."""
        def run():
            from googleapiclient.http import MediaIoBaseDownload
            meta = self.drive.files().get(fileId=file_id, fields="id,name,mimeType,size", supportsAllDrives=True).execute()
            if meta.get("mimeType", "").startswith("application/vnd.google-apps"):
                raise ClassroomError(f"'{meta.get('name')}' is a Google Docs/Sheets file, not an uploaded file.")
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, self.drive.files().get_media(fileId=file_id, supportsAllDrives=True))
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return meta.get("name") or file_id, buf.getvalue()
        return self._call(run)


# ------------------------------------------------------------------ submissions table

def _turned_in_time(sub: dict) -> Optional[str]:
    latest = None
    for h in sub.get("submissionHistory", []) or []:
        st = h.get("stateHistory") or {}
        if st.get("state") == "TURNED_IN":
            latest = st.get("stateTimestamp") or latest
    return latest


def _attachments(sub: dict) -> List[dict]:
    out = []
    for a in ((sub.get("assignmentSubmission") or {}).get("attachments") or []):
        f = a.get("driveFile")
        if f and f.get("id"):
            out.append({"id": f["id"], "title": f.get("title") or f["id"]})
    return out


def _fingerprint(attachments: List[dict]) -> str:
    return "|".join(sorted(a["id"] for a in attachments))


def load_manifest(dest: Path) -> Dict[str, Any]:
    f = Path(dest) / MANIFEST_NAME
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"students": {}}


def save_manifest(dest: Path, manifest: Dict[str, Any]) -> None:
    f = Path(dest) / MANIFEST_NAME
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def build_rows(client: ClassroomClient, course_id: str, coursework_id: str, dest: Optional[str],
               pattern: Optional[str] = None, overrides: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """One row per enrolled student with their submission state and whether it is already downloaded."""
    settings = load_settings()
    pattern = pattern if pattern is not None else settings["id_pattern"]
    overrides = {k.lower(): v for k, v in {**settings["overrides"], **(overrides or {})}.items()}

    roster = client.roster(course_id)
    subs = {s.get("userId"): s for s in client.submissions(course_id, coursework_id)}
    manifest = load_manifest(Path(dest)) if dest and Path(dest).is_dir() else {"students": {}}
    saved_by_email = {v.get("email"): (k, v) for k, v in manifest.get("students", {}).items()}

    scope = SystemConfig.student_scope()
    rows = []
    for user_id in set(roster) | set(subs):
        person = roster.get(user_id, {"name": "(not on roster)", "email": ""})
        sub = subs.get(user_id, {})
        state = sub.get("state", "NEW")
        atts = _attachments(sub)
        roll = derive_roll_id(person["email"], pattern, overrides)
        submitted = state in SUBMITTED_STATES and bool(atts)

        local = "none"
        saved = saved_by_email.get(person["email"])
        if submitted:
            if not saved:
                local = "new"
            else:
                saved_roll, info = saved
                exists = dest and (Path(dest) / info.get("saved_as", "")).exists()
                local = "up_to_date" if (info.get("fingerprint") == _fingerprint(atts) and saved_roll == roll and exists) else "updated"
        elif saved:
            local = "kept"  # previously downloaded, no longer turned in

        rows.append({
            "user_id": user_id, "name": person["name"], "email": person["email"], "roll_id": roll,
            "state": state, "late": bool(sub.get("late")), "submitted": submitted,
            "turned_in_at": _turned_in_time(sub), "attachments": atts, "local": local,
            "submission_id": sub.get("id"), "override": person["email"] in overrides,
            "in_scope": scope.includes(roll, person["email"]),
        })

    # Two students resolving to the same roll ID would overwrite each other's zip
    # (your own students are only compared with each other, so another batch's same number never blocks them)
    counts_all: Dict[str, int] = {}
    counts_mine: Dict[str, int] = {}
    for r in rows:
        if r["roll_id"]:
            counts_all[r["roll_id"]] = counts_all.get(r["roll_id"], 0) + 1
            if r["in_scope"]:
                counts_mine[r["roll_id"]] = counts_mine.get(r["roll_id"], 0) + 1
    for r in rows:
        counts = counts_mine if r["in_scope"] else counts_all
        r["duplicate_id"] = bool(r["roll_id"] and counts.get(r["roll_id"], 0) > 1)

    rows.sort(key=lambda r: (not r["in_scope"], r["roll_id"] is None, r["roll_id"] or "", r["name"].lower()))
    mine = [r for r in rows if r["in_scope"]]
    return {
        "rows": rows,
        "pattern": pattern,
        "scope": scope.describe(),
        # Counts cover only the TA's own students; everyone else is listed but never downloaded by default
        "counts": {
            "students": len(mine),
            "submitted": sum(r["submitted"] for r in mine),
            "late": sum(r["submitted"] and r["late"] for r in mine),
            "missing": sum(not r["submitted"] for r in mine),
            "to_download": sum(r["local"] in ("new", "updated") for r in mine),
            "no_id": sum(r["submitted"] and not r["roll_id"] for r in mine),
            "duplicates": sum(r["duplicate_id"] for r in mine),
            "out_of_scope": len(rows) - len(mine),
        },
    }


# ------------------------------------------------------------------ downloading

SYNC_STATE: Dict[str, Any] = {"running": False, "total": 0, "done": 0, "message": "Idle", "log": [], "result": None}


def _sync_log(msg: str) -> None:
    SYNC_STATE["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    SYNC_STATE["log"] = SYNC_STATE["log"][-300:]


def save_submission(client: ClassroomClient, row: dict, dest: Path) -> str:
    """Downloads one student's attachments to dest/<roll>.zip (wrapping loose files in a zip). Returns the saved name."""
    roll = row["roll_id"]
    files = [client.download(a["id"]) for a in row["attachments"]]

    if len(files) == 1 and files[0][0].lower().endswith(ARCHIVE_EXTS):
        name, data = files[0]
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise ClassroomError(f"'{name}' is named .zip but is not a valid zip archive.")
        out_name, payload = f"{roll}.zip", data
    elif len(files) == 1 and files[0][0].lower().endswith(UNSUPPORTED_ARCHIVES):
        name, data = files[0]
        out_name, payload = f"{roll}{Path(name).suffix.lower()}", data
    else:
        # Loose source files (or several archives): pack them into one zip; nested zips are extracted by the grader
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            used = set()
            for name, data in files:
                arc = Path(name).name or "file"
                while arc.lower() in used:
                    arc = f"_{arc}"
                used.add(arc.lower())
                zf.writestr(arc, data)
        out_name, payload = f"{roll}.zip", buf.getvalue()

    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / f".{out_name}.part"
    tmp.write_bytes(payload)
    os.replace(tmp, dest / out_name)
    return out_name


def run_sync(course_id: str, coursework_id: str, dest: str, only_emails: Optional[List[str]] = None,
             overrides: Optional[Dict[str, str]] = None, link: Optional[Dict[str, Any]] = None,
             client: Optional[ClassroomClient] = None) -> Dict[str, Any]:
    """Downloads new/updated submissions (or exactly `only_emails`). Safe to call repeatedly."""
    SYNC_STATE.update(running=True, total=0, done=0, message="Reading submissions from Classroom…", log=[], result=None)
    summary: Dict[str, Any] = {"downloaded": [], "updated": [], "skipped": [], "errors": [], "unsupported": []}
    try:
        settings = load_settings()
        if overrides:
            for email, roll in overrides.items():
                email = email.strip().lower()
                if roll and sanitize_id(roll):
                    settings["overrides"][email] = sanitize_id(roll)
                else:
                    settings["overrides"].pop(email, None)
        if link and link.get("assignment_id"):
            settings["links"][link["assignment_id"]] = {**link, "dest": dest}
        save_settings(settings)

        client = client or ClassroomClient()
        dest_path = Path(dest)
        dest_path.mkdir(parents=True, exist_ok=True)
        table = build_rows(client, course_id, coursework_id, dest)
        rows = table["rows"]
        wanted = {e.lower() for e in only_emails} if only_emails else None
        # Explicitly ticked students are always downloaded; otherwise only the TA's own new/updated ones
        todo = [r for r in rows if r["submitted"] and (
            r["email"] in wanted if wanted is not None else (r["in_scope"] and r["local"] in ("new", "updated")))]

        SYNC_STATE["total"] = len(todo)
        manifest = load_manifest(dest_path)
        for r in todo:
            label = r["roll_id"] or r["email"] or r["name"]
            if not r["roll_id"]:
                summary["skipped"].append({"email": r["email"], "name": r["name"], "reason": "no roll ID"})
                _sync_log(f"Skipped {r['name']} <{r['email']}>: no roll ID (set one in the table).")
            elif r["duplicate_id"]:
                summary["skipped"].append({"email": r["email"], "name": r["name"], "reason": f"roll ID {r['roll_id']} is used by two students"})
                _sync_log(f"Skipped {r['name']}: roll ID {r['roll_id']} is shared with another student.")
            else:
                SYNC_STATE["message"] = f"Downloading {label}…"
                try:
                    saved_as = save_submission(client, r, dest_path)
                    # Drop an old file saved under a different roll ID / extension for this student
                    for old_roll, info in list(manifest["students"].items()):
                        if info.get("email") == r["email"] and (old_roll != r["roll_id"] or info.get("saved_as") != saved_as):
                            old_file = dest_path / info.get("saved_as", "")
                            if old_file.is_file() and old_file.name != saved_as:
                                old_file.unlink()
                            manifest["students"].pop(old_roll, None)
                    manifest["students"][r["roll_id"]] = {
                        "email": r["email"], "name": r["name"], "saved_as": saved_as,
                        "fingerprint": _fingerprint(r["attachments"]),
                        "files": [a["title"] for a in r["attachments"]],
                        "late": r["late"], "turned_in_at": r["turned_in_at"], "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                    save_manifest(dest_path, manifest)
                    bucket = "updated" if r["local"] == "updated" else "downloaded"
                    summary[bucket].append(r["roll_id"])
                    if not saved_as.endswith(".zip"):
                        summary["unsupported"].append(saved_as)
                    _sync_log(f"Saved {r['name']} as {saved_as}" + (" (resubmission)" if bucket == "updated" else "") + (" (late)" if r["late"] else ""))
                except ClassroomError as e:
                    summary["errors"].append({"roll_id": r["roll_id"], "error": str(e)})
                    _sync_log(f"ERROR {label}: {e}")
            SYNC_STATE["done"] += 1

        n = len(summary["downloaded"]) + len(summary["updated"])
        SYNC_STATE["message"] = f"Downloaded {n} submission{'s' if n != 1 else ''}" + (f", {len(summary['errors'])} failed" if summary["errors"] else "") + "."
        if not todo:
            SYNC_STATE["message"] = "Everything is already up to date."
    except ClassroomError as e:
        SYNC_STATE["message"] = f"Error: {e}"
        summary["errors"].append({"roll_id": None, "error": str(e)})
    except Exception as e:
        SYNC_STATE["message"] = f"Error: {_friendly_error(e)}"
        summary["errors"].append({"roll_id": None, "error": _friendly_error(e)})
    finally:
        SYNC_STATE["result"] = summary
        SYNC_STATE["running"] = False
    return summary
