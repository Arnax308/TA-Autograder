"""
Web GUI Server for C/C++ TA Autograder.
Provides REST API endpoints for batch grading, assignment management, LLM test generation,
and student report inspection with real-time progress tracking.

Endpoints that do blocking work (grading, compiling, LLM calls, dialogs) are plain `def` so FastAPI
runs them in its thread pool; as `async def` they used to freeze the whole server while running.
"""

from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any, List, Set

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from grader.models import LanguageType, ComparisonMode, TestCase, StudentGradingResult
from grader.config import AssignmentLoader, AssignmentManager, SystemConfig, StudentScope, PROJECT_ROOT, EXEC_BACKENDS
from grader.main import AutograderPipeline, list_submission_items
from grader.generator import TestCaseGenerator
from grader.reporter import Reporter
from grader.gemini import list_models
from grader.marks import suggest_marks, read_late_flag
from grader.cache import ResultsCache
from grader import folders

app = FastAPI(title="C/C++ TA Autograder Dashboard")

_state_lock = threading.Lock()
GRADING_STATE: Dict[str, Any] = {
    "is_running": False,
    "cancel_requested": False,
    "total_students": 0,
    "processed_students": 0,
    "current_students": [],
    "status_message": "Idle",
    "assignment_id": None,
    "submissions_path": None,
    "results": [],
    "summary": None,
    "logs": [],
    "started_at": None,
    "finished_at": None,
}

# Mount static files for GUI
GUI_DIR = Path(__file__).parent / "gui"
app.mount("/gui", StaticFiles(directory=str(GUI_DIR)), name="gui")


def _log(message: str) -> None:
    with _state_lock:
        GRADING_STATE["logs"].append(f"[{time.strftime('%H:%M:%S')}] {message}")
        if len(GRADING_STATE["logs"]) > 600:
            GRADING_STATE["logs"] = GRADING_STATE["logs"][-500:]


def _default_assignment_id() -> Optional[str]:
    assignments = AssignmentManager.list_assignments()
    return assignments[0]["id"] if assignments else None


def _safe_name(value: str) -> str:
    """Strips any path components from an id used to build file names."""
    return Path(value).name


class StartGradingRequest(BaseModel):
    submissions_path: str
    assignment_id: str
    reports_dir: str = "reports"
    results_dir: str = "results"
    log_dir: str = "logs"
    rerun_all: bool = False
    rerun_llm_only: bool = False
    workers: int = 3


class CreateAssignmentRequest(BaseModel):
    name: str
    title: Optional[str] = None
    desc: Optional[str] = None
    format_type: str = "json"


class UpdateAssignmentRequest(BaseModel):
    title: Optional[str] = None
    desc: Optional[str] = None
    marking: Optional[Dict[str, float]] = None


class AddQuestionRequest(BaseModel):
    assignment_id: Optional[str] = None
    question_id: str
    name: str
    description: str = ""
    language: str = "c"


class UpdateQuestionRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    compiler_flags: Optional[List[str]] = None
    timeout_sec: Optional[float] = None
    source_hints: Optional[List[str]] = None
    test_cases: Optional[List[Dict[str, Any]]] = None
    rubric: Optional[List[Dict[str, Any]]] = None
    static_checks: Optional[List[Dict[str, Any]]] = None


class GenerateTestsRequest(BaseModel):
    assignment_id: str
    question_id: str
    problem_description: str
    num_tests: int = 4


class UpdateSettingsRequest(BaseModel):
    students: Optional[str] = None
    exec_backend: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_review: Optional[bool] = None


# ==================== Batch grading worker ====================

def run_batch_grading_task(req: StartGradingRequest):
    with _state_lock:
        GRADING_STATE.update({
            "processed_students": 0, "logs": [], "results": [], "summary": None,
            "current_students": [], "status_message": "Initializing grading pipeline...",
            "assignment_id": req.assignment_id, "submissions_path": req.submissions_path,
            "started_at": time.time(), "finished_at": None, "cancel_requested": False,
        })

    try:
        folders.remember(req.assignment_id, req.submissions_path, req.reports_dir, req.results_dir)
        items = list_submission_items(req.submissions_path)
        with _state_lock:
            GRADING_STATE["total_students"] = len(items)
        if not items:
            with _state_lock:
                GRADING_STATE["status_message"] = f"No .zip archives or student folders found in '{req.submissions_path}'."
            return

        _log(f"Grading {len(items)} submissions for '{req.assignment_id}' using the {SystemConfig.resolve_exec_backend()} execution backend.")
        workers = max(1, min(int(req.workers or 1), 6))
        results: List[StudentGradingResult] = []
        done = 0

        def grade(kind_path):
            kind, p = kind_path
            if GRADING_STATE["cancel_requested"]:
                return None
            # Each worker gets its own pipeline (compiler/LLM clients are not shared across threads)
            pipeline = AutograderPipeline(
                assignment_id_or_path=req.assignment_id, output_dir=req.reports_dir,
                results_dir=req.results_dir, log_dir=req.log_dir, log_callback=_log
            )
            with _state_lock:
                GRADING_STATE["current_students"].append(p.stem)
            try:
                return pipeline.grade_single_student(
                    submission_path=p, is_zip=(kind == "zip"),
                    rerun_all=req.rerun_all, rerun_llm_only=req.rerun_llm_only
                )
            finally:
                with _state_lock:
                    if p.stem in GRADING_STATE["current_students"]:
                        GRADING_STATE["current_students"].remove(p.stem)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(grade, item): item for item in items}
            for fut in as_completed(futures):
                kind, p = futures[fut]
                done += 1
                try:
                    res = fut.result()
                except Exception as e:
                    _log(f"ERROR grading {p.stem}: {e}")
                    res = None
                with _state_lock:
                    GRADING_STATE["processed_students"] = done
                    GRADING_STATE["status_message"] = f"Graded {done}/{len(items)} submissions"
                if res is None:
                    continue
                results.append(res)
                with _state_lock:
                    GRADING_STATE["results"].append(res.model_dump(mode="json"))
                status_str = "needs review" if res.manual_review_recommended else "clean"
                _log(f"Finished {res.student_id}: {res.total_tests_passed}/{res.total_tests_run} tests passed, {status_str}.")

        if GRADING_STATE["cancel_requested"]:
            with _state_lock:
                GRADING_STATE["status_message"] = f"Cancelled after {len(results)} of {len(items)} submissions."
            _log("Batch cancelled by user.")
        else:
            with _state_lock:
                GRADING_STATE["status_message"] = f"Grading complete: {len(items)} submissions processed."

        results.sort(key=lambda r: r.student_id.lower())
        summary = Reporter(req.reports_dir).generate_and_save_batch_summary(results, req.assignment_id)
        with _state_lock:
            GRADING_STATE["summary"] = summary.model_dump(mode="json")
        _log(f"Summary saved to {Path(req.reports_dir) / 'BATCH_SUMMARY.md'}.")

    except Exception as e:
        with _state_lock:
            GRADING_STATE["status_message"] = f"Error during grading: {e}"
        _log(f"ERROR: {e}")
    finally:
        with _state_lock:
            GRADING_STATE["is_running"] = False
            GRADING_STATE["cancel_requested"] = False
            GRADING_STATE["current_students"] = []
            GRADING_STATE["finished_at"] = time.time()


# ==================== API Endpoints ====================

@app.get("/", response_class=HTMLResponse)
def get_index():
    index_path = GUI_DIR / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "<h1>GUI files are missing.</h1>"


@app.get("/api/system/status")
def get_system_status():
    gcc_path, _ = SystemConfig.find_compiler(LanguageType.C)
    gxx_path, _ = SystemConfig.find_compiler(LanguageType.CPP)
    api_key = SystemConfig.get_gemini_api_key()
    wsl = SystemConfig.wsl_toolchain()

    return {
        "gcc_available": bool(gcc_path),
        "gcc_path": gcc_path or "Not found",
        "gxx_available": bool(gxx_path),
        "gxx_path": gxx_path or "Not found",
        "gemini_api_configured": bool(api_key),
        "gemini_model": SystemConfig.get_gemini_model(),
        "api_key_preview": f"{api_key[:4]}…{api_key[-4:]}" if api_key else "Not set",
        "llm_provider": SystemConfig.get_llm_provider(),
        "llm_model": SystemConfig.get_llm_model(),
        "llm_configured": bool(SystemConfig.get_llm_api_key()),
        "llm_key_preview": (lambda k: f"{k[:4]}…{k[-4:]}" if k else "Not set")(SystemConfig.get_llm_api_key()),
        "llm_review": (os.getenv("LLM_REVIEW") or "on").lower() != "off",
        "platform": sys.platform,
        "smart_app_control": SystemConfig.smart_app_control_state(),
        "wsl_available": bool(wsl),
        "wsl_gcc_version": (wsl or {}).get("version"),
        "exec_backend_setting": SystemConfig.get_exec_backend_setting(),
        "exec_backend": SystemConfig.resolve_exec_backend(),
        "project_root": str(PROJECT_ROOT),
        "default_assignment": _default_assignment_id(),
        "students": SystemConfig.get_student_scope_text(),
        "students_summary": SystemConfig.student_scope().describe(),
    }


@app.get("/api/system/models")
def get_models():
    if SystemConfig.get_llm_provider() == "groq":
        from grader.groq_client import list_groq_models
        key = SystemConfig.get_groq_api_key()
        models, err = list_groq_models(key) if key else ([], "No Groq API key configured.")
    else:
        models, err = list_models()
    return {"models": models, "error": err, "current": SystemConfig.get_llm_model(), "provider": SystemConfig.get_llm_provider()}


@app.post("/api/system/settings")
def update_settings(req: UpdateSettingsRequest):
    if req.students is not None:
        try:
            StudentScope.parse(req.students)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        SystemConfig.update_env_setting("TA_STUDENTS", req.students.strip())
    if req.exec_backend is not None:
        if req.exec_backend not in EXEC_BACKENDS:
            raise HTTPException(status_code=400, detail=f"exec_backend must be one of {EXEC_BACKENDS}")
        SystemConfig.update_env_setting("EXEC_BACKEND", req.exec_backend)
    if req.gemini_model is not None:
        SystemConfig.update_env_setting("GEMINI_MODEL", req.gemini_model.strip())
    if req.gemini_api_key is not None and req.gemini_api_key.strip():
        SystemConfig.update_env_setting("GEMINI_API_KEY", req.gemini_api_key.strip())
    if req.llm_provider is not None:
        if req.llm_provider not in ("gemini", "groq"):
            raise HTTPException(status_code=400, detail="llm_provider must be 'gemini' or 'groq'.")
        SystemConfig.update_env_setting("LLM_PROVIDER", req.llm_provider)
    provider = SystemConfig.get_llm_provider()
    if req.llm_model:
        SystemConfig.update_env_setting("GROQ_MODEL" if provider == "groq" else "GEMINI_MODEL", req.llm_model.strip())
    if req.llm_api_key and req.llm_api_key.strip():
        SystemConfig.update_env_setting("GROQ_API_KEY" if provider == "groq" else "GEMINI_API_KEY", req.llm_api_key.strip())
    if req.llm_review is not None:
        SystemConfig.update_env_setting("LLM_REVIEW", "on" if req.llm_review else "off")
    return {"success": True, "status": get_system_status()}


class BrowseFolderRequest(BaseModel):
    initial_dir: Optional[str] = None
    title: Optional[str] = "Select Folder"


@app.post("/api/system/browse-folder")
def browse_folder_dialog(req: Optional[BrowseFolderRequest] = None):
    """Opens the native Windows folder selection dialog and returns the chosen path."""
    if sys.platform != "win32":
        return {"success": False, "message": "Native folder dialog is only available on Windows."}
    init_dir = os.path.abspath(req.initial_dir if req and req.initial_dir else os.getcwd())
    title_text = req.title if req and req.title else "Select Folder"

    # Values are passed through environment variables so quotes in paths cannot break the script
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$f.Description = $env:TA_DIALOG_TITLE; $f.SelectedPath = $env:TA_DIALOG_DIR; $f.ShowNewFolderButton = $true;"
        "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::WriteLine($f.SelectedPath) }"
    )
    env = dict(os.environ, TA_DIALOG_TITLE=title_text, TA_DIALOG_DIR=init_dir)
    try:
        res = subprocess.run(["powershell", "-STA", "-NoProfile", "-Command", ps_script],
                             capture_output=True, text=True, timeout=300, env=env)
        selected_dir = res.stdout.strip()
        if selected_dir and os.path.exists(selected_dir):
            return {"success": True, "path": selected_dir}
        return {"success": False, "message": "No folder selected."}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/system/list-dirs")
def list_directories(path: Optional[str] = ""):
    """Lists subdirectories in a path for the in-app interactive folder picker modal."""
    cwd = Path.cwd().resolve()
    target = Path(path).expanduser().resolve() if path and path.strip() not in (".", "") else cwd
    if not target.exists() or not target.is_dir():
        target = cwd

    subdirs = []
    try:
        for entry in os.scandir(target):
            try:
                if entry.is_dir() and not entry.name.startswith((".", "$")) and entry.name != "__pycache__":
                    subdirs.append(entry.name)
            except OSError:
                continue
    except Exception as e:
        return {"success": False, "error": str(e), "current_path": str(target), "directories": []}

    subdirs.sort(key=lambda s: s.lower())
    home_dir = Path.home()
    shortcuts = [("Project", cwd), ("Home", home_dir)]
    for name in ("Documents", "Desktop", "Downloads", "OneDrive"):
        if (home_dir / name).exists():
            shortcuts.append((name, home_dir / name))
    zip_count = sum(1 for p in target.iterdir() if p.suffix.lower() == ".zip") if target.exists() else 0

    return {
        "success": True,
        "current_path": str(target),
        "parent_path": str(target.parent) if target.parent != target else None,
        "directories": subdirs,
        "zip_count": zip_count,
        "quick_shortcuts": [{"label": label, "path": str(p)} for label, p in shortcuts],
    }


class CreateDirRequest(BaseModel):
    parent_dir: str
    folder_name: str


@app.post("/api/system/create-dir")
def create_directory(req: CreateDirRequest):
    name = req.folder_name.strip()
    if not name or any(c in name for c in '\\/:*?"<>|') or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid folder name.")
    try:
        p = Path(req.parent_dir).resolve() / name
        p.mkdir(parents=True, exist_ok=True)
        return {"success": True, "path": str(p)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----- ASSIGNMENT CRUD -----

def _load_assignment_or_404(assignment_id: str):
    try:
        return AssignmentLoader.load_assignment(assignment_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


def _find_question(cfg, question_id: str):
    for q in cfg.questions:
        if q.id.lower() == question_id.lower():
            return q
    raise HTTPException(status_code=404, detail=f"Question '{question_id}' not found.")


@app.get("/api/assignments")
def get_assignments():
    return {"assignments": AssignmentManager.list_assignments()}


class FoldersRequest(BaseModel):
    submissions: str
    reports: Optional[str] = None
    results: Optional[str] = None


@app.get("/api/assignments/{assignment_id}/folders")
def get_assignment_folders(assignment_id: str):
    """Folders this assignment was graded with (so results are found even when this browser never saw them)."""
    return folders.known(assignment_id)


@app.post("/api/assignments/{assignment_id}/folders")
def set_assignment_folders(assignment_id: str, req: FoldersRequest):
    folders.remember(assignment_id, req.submissions, req.reports, req.results)
    return {"ok": True}


@app.get("/api/assignments/{assignment_id}")
def get_assignment_details(assignment_id: str):
    return _load_assignment_or_404(assignment_id).model_dump(mode="json")


@app.post("/api/assignments/new")
def create_new_assignment(req: CreateAssignmentRequest):
    try:
        path = AssignmentManager.create_new_assignment(
            name=req.name, title=req.title or None, description=req.desc or None, format_type=req.format_type
        )
        return {"success": True, "path": str(path), "id": path.name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/assignments/{assignment_id}")
def update_assignment(assignment_id: str, req: UpdateAssignmentRequest):
    try:
        updated = AssignmentManager.update_assignment(assignment_id=assignment_id, title=req.title, description=req.desc)
        if req.marking is not None:
            from grader.models import MarkingScheme
            updated.marking = MarkingScheme(**{**updated.marking.model_dump(), **req.marking})
            AssignmentManager.save_assignment(updated, AssignmentManager.resolve_assignment_dir(assignment_id))
        return {"success": True, "assignment": updated.model_dump(mode="json")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/assignments/{assignment_id}")
def delete_assignment(assignment_id: str):
    try:
        AssignmentManager.delete_assignment(assignment_id)
        return {"success": True, "message": f"Deleted assignment '{assignment_id}'."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----- QUESTION CRUD -----

@app.post("/api/assignments/{assignment_id}/questions")
def add_question(assignment_id: str, req: AddQuestionRequest):
    try:
        new_q = AssignmentManager.add_question_to_assignment(
            assignment_id=assignment_id, question_id=req.question_id, name=req.name,
            description=req.description, language=req.language
        )
        return {"success": True, "question": new_q.model_dump(mode="json")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/assignments/{assignment_id}/questions/{question_id}")
def update_question(assignment_id: str, question_id: str, req: UpdateQuestionRequest):
    try:
        updated_q = AssignmentManager.update_question(
            assignment_id=assignment_id, question_id=question_id, name=req.name,
            description=req.description, language=req.language, compiler_flags=req.compiler_flags,
            timeout_sec=req.timeout_sec, test_cases=req.test_cases, rubric=req.rubric,
            static_checks=req.static_checks
        )
        if req.source_hints is not None:
            cfg = AssignmentLoader.load_assignment(assignment_id)
            q = _find_question(cfg, question_id)
            q.source_hints = [h.strip() for h in req.source_hints if h.strip()]
            AssignmentManager.save_assignment(cfg, AssignmentManager.resolve_assignment_dir(assignment_id))
            updated_q = q
        return {"success": True, "question": updated_q.model_dump(mode="json")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/assignments/{assignment_id}/questions/{question_id}")
def delete_question(assignment_id: str, question_id: str):
    try:
        AssignmentManager.delete_question(assignment_id, question_id)
        return {"success": True, "message": f"Deleted question '{question_id}'."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----- QUESTION TEST CASES CRUD -----

class SaveTestCaseRequest(BaseModel):
    id: str
    original_id: Optional[str] = None  # set when renaming an existing test
    description: str = ""
    input_data: str = ""
    expected_output: str = ""
    comparison_mode: str = "whitespace_normalized"
    float_tolerance: float = 0.0001
    timeout_sec: float = 2.0
    is_edge_case: bool = False
    sort_order: Optional[str] = None
    sort_type: Optional[str] = None


@app.get("/api/assignments/{assignment_id}/questions/{question_id}/tests")
def get_question_test_cases(assignment_id: str, question_id: str):
    cfg = _load_assignment_or_404(assignment_id)
    q = _find_question(cfg, question_id)
    return {
        "success": True,
        "assignment_id": assignment_id,
        "question_id": q.id,
        "question_name": q.name,
        "test_cases": [t.model_dump(mode="json") for t in (q.test_cases or [])]
    }


@app.post("/api/assignments/{assignment_id}/questions/{question_id}/tests")
def add_or_update_test_case(assignment_id: str, question_id: str, req: SaveTestCaseRequest):
    cfg = _load_assignment_or_404(assignment_id)
    target_q = _find_question(cfg, question_id)
    if not req.id.strip():
        raise HTTPException(status_code=400, detail="Test ID is required.")

    try:
        cm = ComparisonMode(req.comparison_mode.lower())
    except ValueError:
        cm = ComparisonMode.WHITESPACE_NORMALIZED

    new_tc = TestCase(
        id=req.id.strip(),
        description=req.description.strip(),
        input_data=req.input_data,
        expected_output=req.expected_output,
        comparison_mode=cm,
        float_tolerance=req.float_tolerance,
        timeout_sec=max(0.5, req.timeout_sec),
        is_edge_case=req.is_edge_case,
        sort_order=req.sort_order or None,
        sort_type=req.sort_type or None
    )

    tests = list(target_q.test_cases or [])
    match_id = (req.original_id or new_tc.id).lower()
    idx = next((i for i, t in enumerate(tests) if t.id.lower() == match_id), None)
    if req.original_id and req.original_id.lower() != new_tc.id.lower() and any(t.id.lower() == new_tc.id.lower() for t in tests):
        raise HTTPException(status_code=400, detail=f"A test named '{new_tc.id}' already exists.")
    if idx is not None:
        tests[idx] = new_tc
    else:
        tests.append(new_tc)
    target_q.test_cases = tests

    try:
        AssignmentManager.save_assignment(cfg, AssignmentManager.resolve_assignment_dir(assignment_id))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "success": True,
        "message": f"Saved test case '{new_tc.id}'.",
        "test_cases": [t.model_dump(mode="json") for t in target_q.test_cases]
    }


@app.delete("/api/assignments/{assignment_id}/questions/{question_id}/tests/{test_id}")
def delete_question_test_case(assignment_id: str, question_id: str, test_id: str):
    cfg = _load_assignment_or_404(assignment_id)
    target_q = _find_question(cfg, question_id)

    initial_len = len(target_q.test_cases or [])
    target_q.test_cases = [t for t in (target_q.test_cases or []) if t.id.lower() != test_id.lower()]
    if len(target_q.test_cases) == initial_len:
        raise HTTPException(status_code=404, detail=f"Test case '{test_id}' not found.")

    AssignmentManager.save_assignment(cfg, AssignmentManager.resolve_assignment_dir(assignment_id))
    return {
        "success": True,
        "message": f"Deleted test case '{test_id}'.",
        "test_cases": [t.model_dump(mode="json") for t in target_q.test_cases]
    }


# ----- LLM TEST GENERATION -----

@app.post("/api/assignments/generate-tests")
def generate_tests(req: GenerateTestsRequest):
    try:
        gen = TestCaseGenerator()
        tests = gen.generate_and_save_tests(
            assignment_id_or_path=req.assignment_id,
            question_id=req.question_id,
            problem_description=req.problem_description,
            num_tests=max(1, min(req.num_tests, 12))
        )
        return {
            "success": True,
            "test_cases": [t.model_dump(mode="json") for t in tests],
            "message": f"Generated and saved {len(tests)} test cases."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----- BATCH GRADING & RESULTS -----

@app.post("/api/grade/start")
def start_grading(req: StartGradingRequest):
    if not Path(req.submissions_path).is_dir():
        raise HTTPException(status_code=400, detail=f"Submissions folder '{req.submissions_path}' does not exist.")
    _load_assignment_or_404(req.assignment_id)
    with _state_lock:
        if GRADING_STATE["is_running"]:
            return {"success": False, "message": "A grading batch is already running."}
        GRADING_STATE["is_running"] = True  # claim the slot before the thread starts
    threading.Thread(target=run_batch_grading_task, args=(req,), daemon=True).start()
    return {"success": True, "message": "Grading started."}


@app.post("/api/grade/cancel")
def cancel_grading():
    with _state_lock:
        if not GRADING_STATE["is_running"]:
            return {"success": False, "message": "No batch is running."}
        GRADING_STATE["cancel_requested"] = True
    return {"success": True, "message": "Cancelling after the submissions currently in progress finish."}


@app.get("/api/grade/status")
def get_grading_status(include_results: bool = True):
    with _state_lock:
        state = dict(GRADING_STATE)
        state["logs"] = list(GRADING_STATE["logs"][-200:])
        state["current_students"] = list(GRADING_STATE["current_students"])
        if not include_results:
            state.pop("results", None)
        else:
            state["results"] = list(GRADING_STATE["results"])
    return state


class GradeSingleStudentRequest(BaseModel):
    student_id: str
    submissions_path: str
    assignment_id: str
    reports_dir: str = "reports"
    results_dir: str = "results"
    log_dir: str = "logs"
    rerun_all: bool = True
    rerun_llm_only: bool = False
    question_id: Optional[str] = None


@app.post("/api/grade/single")
def grade_single_student_endpoint(req: GradeSingleStudentRequest):
    folders.remember(req.assignment_id, req.submissions_path, req.reports_dir, req.results_dir)
    items = list_submission_items(req.submissions_path)
    target = next(((k, p) for k, p in items if p.stem.lower() == req.student_id.lower()), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Submission '{req.student_id}' not found in '{req.submissions_path}'.")
    kind, target_path = target

    try:
        pipeline = AutograderPipeline(
            assignment_id_or_path=req.assignment_id, output_dir=req.reports_dir,
            results_dir=req.results_dir, log_dir=req.log_dir
        )
        res = pipeline.grade_single_student(
            submission_path=target_path, is_zip=(kind == "zip"), filter_question=req.question_id,
            rerun_all=req.rerun_all, rerun_llm_only=req.rerun_llm_only
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    report_file = Path(req.reports_dir) / f"{res.student_id}.md"
    return {
        "success": True,
        "student_id": res.student_id,
        "result": res.model_dump(mode="json"),
        "report_markdown": report_file.read_text(encoding="utf-8") if report_file.exists() else ""
    }


@app.get("/api/submissions")
def get_submissions_in_folder(path: str = Query(...)):
    items = [{"id": p.stem, "filename": p.name, "type": kind} for kind, p in list_submission_items(path)]
    return {"submissions": items, "count": len(items), "path": path}


@app.get("/api/results")
def get_all_results(
    results_dir: str = Query("results"),
    reports_dir: str = Query("reports"),
    submissions_path: Optional[str] = Query(None),
    assignment_id: Optional[str] = Query(None)
):
    # Discover all student IDs from the submissions folder (only the TA's own students)
    scope = SystemConfig.student_scope()
    submission_ids: Set[str] = set()
    if submissions_path:
        submission_ids = {p.stem for _, p in list_submission_items(submissions_path)}

    results: List[dict] = []
    seen_lower = set()
    r_path = Path(results_dir)
    if r_path.is_dir():
        for f in sorted(r_path.glob("*.json")):
            try:
                with open(f, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
            except Exception:
                continue
            if not isinstance(data, dict) or "student_id" not in data:
                continue
            # Results graded against another assignment are shown as not graded for this one
            if assignment_id and data.get("assignment_id") and data["assignment_id"] != assignment_id:
                continue
            if submission_ids and data["student_id"] not in submission_ids:
                continue
            if scope.active and not scope.includes(data["student_id"]):
                continue
            results.append(data)
            seen_lower.add(data["student_id"].lower())

    # Pending stubs for submissions that haven't been graded yet
    for sid in sorted(submission_ids, key=str.lower):
        if sid.lower() not in seen_lower:
            results.append({
                "student_id": sid, "status": "pending", "question_results": {},
                "total_tests_passed": 0, "total_tests_run": 0, "compilation_failures": 0,
                "runtime_failures": 0, "manual_review_recommended": False,
                "manual_review_reasons": [], "timestamp": None
            })

    results.sort(key=lambda r: r["student_id"].lower())
    _attach_marks(results, assignment_id, submissions_path)
    summary_file = Path(reports_dir) / "BATCH_SUMMARY.md"
    return {
        "results": results,
        "summary_markdown": summary_file.read_text(encoding="utf-8") if summary_file.exists() else "",
        "total": len(results)
    }


def _attach_marks(results: List[dict], assignment_id: Optional[str], submissions_path: Optional[str]) -> None:
    """Adds suggested/confirmed marks to each graded result (computed on read, so old results get them too)."""
    configs: Dict[str, Any] = {}
    for data in results:
        aid = data.get("assignment_id") or assignment_id
        if data.get("status") == "pending" or not aid:
            continue
        if aid not in configs:
            try:
                configs[aid] = AssignmentLoader.load_assignment(aid)
            except Exception:
                configs[aid] = None
        cfg = configs[aid]
        if cfg is None:
            continue
        try:
            res = StudentGradingResult.model_validate(data)
            late = read_late_flag(submissions_path, res.student_id) if submissions_path else None
            data["marks"] = suggest_marks(res, cfg, late=late).model_dump(mode="json")
        except Exception:
            continue


class SaveMarksRequest(BaseModel):
    marks: Dict[str, Optional[float]]  # comp1..comp4; null clears a confirmed mark
    results_dir: str = "results"
    reports_dir: str = "reports"
    submissions_path: Optional[str] = None


@app.post("/api/results/{student_id}/marks")
def save_student_marks(student_id: str, req: SaveMarksRequest):
    """Stores the TA's own marks next to the suggestions (they are what the CSV export uses)."""
    student_id = _safe_name(student_id)
    cache = ResultsCache(req.results_dir)
    res = cache.load_result(student_id)
    if res is None:
        raise HTTPException(status_code=404, detail="Student result not found.")
    cfg = _load_assignment_or_404(res.assignment_id) if res.assignment_id else None
    maxes = {"comp1": 3, "comp2": 3, "comp3": 6, "comp4": 3}
    if cfg:
        maxes = {f"comp{i}": getattr(cfg.marking, f"comp{i}_max") for i in range(1, 5)}
    for key, value in req.marks.items():
        if key not in maxes:
            raise HTTPException(status_code=400, detail=f"Unknown component '{key}'.")
        if value is None:
            res.ta_marks.pop(key, None)
            continue
        if not 0 <= value <= maxes[key]:
            raise HTTPException(status_code=400, detail=f"{key} must be between 0 and {maxes[key]:g}.")
        res.ta_marks[key] = float(value)
    res.ta_marks_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    cache.save_result(res)
    if cfg:
        Reporter(req.reports_dir).save_student_report(res, cfg)
    data = res.model_dump(mode="json")
    _attach_marks([data], res.assignment_id, req.submissions_path)
    return {"success": True, "marks": data.get("marks")}


@app.get("/api/results/{student_id}")
def get_student_result(student_id: str, results_dir: str = Query("results"), reports_dir: str = Query("reports"),
                       submissions_path: Optional[str] = Query(None)):
    student_id = _safe_name(student_id)
    results_file = Path(results_dir) / f"{student_id}.json"
    report_file = Path(reports_dir) / f"{student_id}.md"
    if not results_file.exists():
        raise HTTPException(status_code=404, detail="Student result not found.")

    with open(results_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    _attach_marks([data], data.get("assignment_id"), submissions_path)

    return {
        "student_id": student_id,
        "data": data,
        "report_markdown": report_file.read_text(encoding="utf-8") if report_file.exists() else ""
    }


@app.get("/api/results/{student_id}/source")
def get_student_source(student_id: str, question_id: str, results_dir: str = Query("results")):
    """Returns the source files that were graded for one question, for the in-app code viewer."""
    student_id = _safe_name(student_id)
    results_file = Path(results_dir) / f"{student_id}.json"
    if not results_file.exists():
        raise HTTPException(status_code=404, detail="Student result not found.")
    data = json.loads(results_file.read_text(encoding="utf-8"))
    q = (data.get("question_results") or {}).get(question_id)
    if not q:
        raise HTTPException(status_code=404, detail="Question not found in result.")

    files = []
    for path_str in q.get("source_files", []):
        p = Path(path_str)
        if p.suffix.lower() not in (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp") or not p.is_file():
            files.append({"name": p.name, "path": path_str, "content": None, "missing": True})
            continue
        content = p.read_text(encoding="utf-8", errors="replace")
        files.append({"name": p.name, "path": path_str, "content": content[:200_000], "missing": False})

    driver = None
    if q.get("driver_used") and data.get("assignment_id"):
        for ext in ("cpp", "c"):
            dp = PROJECT_ROOT / "drivers" / data["assignment_id"] / f"{student_id}_{question_id}.{ext}"
            if dp.exists():
                driver = {"name": dp.name, "content": dp.read_text(encoding="utf-8", errors="replace")}
                break
    return {"files": files, "driver": driver}


@app.get("/api/results/{student_id}/compile-log")
def get_compile_log(student_id: str, question_id: str, results_dir: str = Query("results")):
    student_id = _safe_name(student_id)
    results_file = Path(results_dir) / f"{student_id}.json"
    if not results_file.exists():
        raise HTTPException(status_code=404, detail="Student result not found.")
    data = json.loads(results_file.read_text(encoding="utf-8"))
    q = (data.get("question_results") or {}).get(question_id) or {}
    log_path = (q.get("compiler_result") or {}).get("full_log_path")
    if log_path and Path(log_path).is_file():
        return {"log": Path(log_path).read_text(encoding="utf-8", errors="replace")[:100_000]}
    cr = q.get("compiler_result") or {}
    return {"log": f"Command: {cr.get('command', '')}\n\n{cr.get('stderr', '') or 'No compiler output recorded.'}"}


# ==================== Google Classroom import ====================

from grader import classroom  # noqa: E402


def _classroom_call(fn):
    try:
        return fn()
    except classroom.ClassroomError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ClientSecretsRequest(BaseModel):
    content: str


class ClassroomSettingsRequest(BaseModel):
    id_pattern: Optional[str] = None


class ClassroomSyncRequest(BaseModel):
    course_id: str
    coursework_id: str
    dest: str
    only_emails: Optional[List[str]] = None
    overrides: Optional[Dict[str, str]] = None
    assignment_id: Optional[str] = None
    course_name: Optional[str] = None
    coursework_title: Optional[str] = None


@app.get("/api/classroom/status")
def classroom_status():
    settings = classroom.load_settings()
    return {**classroom.status(), "id_pattern": settings["id_pattern"], "links": settings["links"]}


@app.post("/api/classroom/client-secrets")
def classroom_client_secrets(req: ClientSecretsRequest):
    _classroom_call(lambda: classroom.save_client_secrets(req.content))
    return {"success": True}


@app.post("/api/classroom/login")
def classroom_login():
    _classroom_call(classroom.start_login)
    return {"success": True, "message": "A Google sign-in page opened in your browser."}


@app.post("/api/classroom/logout")
def classroom_logout():
    classroom.logout()
    return {"success": True}


@app.post("/api/classroom/settings")
def classroom_settings(req: ClassroomSettingsRequest):
    settings = classroom.load_settings()
    if req.id_pattern is not None:
        _classroom_call(lambda: classroom.derive_roll_id("test1234@x.y", req.id_pattern, {}))
        settings["id_pattern"] = req.id_pattern
    classroom.save_settings(settings)
    return {"success": True}


@app.get("/api/classroom/courses")
def classroom_courses():
    return {"courses": _classroom_call(lambda: classroom.ClassroomClient().courses())}


@app.get("/api/classroom/courses/{course_id}/coursework")
def classroom_coursework(course_id: str):
    return {"coursework": _classroom_call(lambda: classroom.ClassroomClient().coursework(course_id))}


@app.get("/api/classroom/submissions")
def classroom_submissions(course_id: str, coursework_id: str, dest: Optional[str] = None, id_pattern: Optional[str] = None):
    return _classroom_call(lambda: classroom.build_rows(classroom.ClassroomClient(), course_id, coursework_id, dest, pattern=id_pattern))


@app.post("/api/classroom/sync")
def classroom_sync(req: ClassroomSyncRequest):
    if classroom.SYNC_STATE["running"]:
        return {"success": False, "message": "A download is already running."}
    if not req.dest.strip():
        raise HTTPException(status_code=400, detail="Choose a download folder.")
    link = None
    if req.assignment_id:
        link = {"assignment_id": req.assignment_id, "course_id": req.course_id, "coursework_id": req.coursework_id,
                "course_name": req.course_name, "coursework_title": req.coursework_title}
    classroom.SYNC_STATE["running"] = True
    threading.Thread(
        target=classroom.run_sync,
        kwargs=dict(course_id=req.course_id, coursework_id=req.coursework_id, dest=req.dest,
                    only_emails=req.only_emails, overrides=req.overrides, link=link),
        daemon=True,
    ).start()
    return {"success": True}


@app.get("/api/classroom/sync/status")
def classroom_sync_status():
    return dict(classroom.SYNC_STATE)
