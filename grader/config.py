"""
Configuration manager for assignments, compilers, environments, and LLM settings.
Supports JSON and YAML assignment packs and dynamic weekly scaffolding.
"""

from __future__ import annotations
import os
import json
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import yaml
from dotenv import load_dotenv

from grader.models import (
    AssignmentConfig,
    QuestionConfig,
    TestCase,
    StaticCheckRule,
    QuestionRequirement,
    LanguageType,
    ComparisonMode,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Load .env file automatically (from the project root, regardless of the current directory)
load_dotenv(ENV_FILE)

EXEC_BACKENDS = ("auto", "native", "wsl")


class SystemConfig:
    """Manages system paths, compiler discovery, execution backends, and environment variables."""

    @staticmethod
    def get_gemini_api_key() -> Optional[str]:
        key = os.getenv("GEMINI_API_KEY") or os.getenv("LLM_API_KEY")
        if not key or key.strip() in ("", "your_gemini_api_key_here", "your_api_key_here"):
            return None
        return key.strip()

    @staticmethod
    def get_gemini_model() -> str:
        return os.getenv("GEMINI_MODEL") or "gemini-3.6-flash"

    @staticmethod
    def get_groq_api_key() -> Optional[str]:
        key = (os.getenv("GROQ_API_KEY") or "").strip()
        return key or None

    @staticmethod
    def get_llm_provider() -> str:
        """'groq' or 'gemini'. LLM_PROVIDER wins; otherwise Groq when only its key is set."""
        value = (os.getenv("LLM_PROVIDER") or "").strip().lower()
        if value in ("groq", "gemini"):
            return value
        if SystemConfig.get_groq_api_key() and not SystemConfig.get_gemini_api_key():
            return "groq"
        return "gemini"

    @staticmethod
    def get_llm_api_key() -> Optional[str]:
        return SystemConfig.get_groq_api_key() if SystemConfig.get_llm_provider() == "groq" else SystemConfig.get_gemini_api_key()

    @staticmethod
    def get_llm_model() -> str:
        if SystemConfig.get_llm_provider() == "groq":
            return (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b").strip()
        return SystemConfig.get_gemini_model()

    @staticmethod
    def update_env_setting(key: str, value: Optional[str]) -> None:
        """Persists a setting into the project .env file and the running process."""
        from dotenv import set_key, unset_key
        ENV_FILE.touch(exist_ok=True)
        if value is None or value == "":
            unset_key(str(ENV_FILE), key)
            os.environ.pop(key, None)
        else:
            set_key(str(ENV_FILE), key, value, quote_mode="never")
            os.environ[key] = value

    # ---------------- Execution backend (native Windows vs WSL) ----------------

    @staticmethod
    @lru_cache(maxsize=1)
    def smart_app_control_state() -> str:
        """
        Windows 11 Smart App Control silently blocks unsigned, freshly compiled executables
        (WinError 4551). Returns 'on', 'evaluation', 'off', or 'unknown'.
        """
        if sys.platform != "win32":
            return "off"
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
                value, _ = winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")
            return {0: "off", 1: "on", 2: "evaluation"}.get(int(value), "unknown")
        except Exception:
            return "unknown"

    @staticmethod
    @lru_cache(maxsize=1)
    def wsl_toolchain() -> Optional[Dict[str, str]]:
        """Returns WSL toolchain info if WSL with gcc, g++ and coreutils timeout is usable, else None."""
        if sys.platform != "win32" or not shutil.which("wsl.exe"):
            return None
        try:
            res = subprocess.run(
                ["wsl.exe", "-e", "sh", "-c", "command -v gcc && command -v g++ && command -v timeout && gcc -dumpfullversion"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45
            )
            lines = [l.strip() for l in (res.stdout or "").splitlines() if l.strip()]
            if res.returncode == 0 and len(lines) >= 4:
                return {"gcc": lines[0], "gxx": lines[1], "version": lines[3]}
        except Exception:
            pass
        return None

    @staticmethod
    def get_exec_backend_setting() -> str:
        value = (os.getenv("EXEC_BACKEND") or "auto").strip().lower()
        return value if value in EXEC_BACKENDS else "auto"

    @staticmethod
    def resolve_exec_backend() -> str:
        """
        Picks where student code is compiled and run:
        - 'wsl': inside WSL (Linux binaries are not subject to Smart App Control / Defender exe scanning).
        - 'native': the local Windows/Linux/macOS toolchain.
        'auto' prefers WSL on Windows whenever it is available, falling back to native.
        """
        setting = SystemConfig.get_exec_backend_setting()
        if setting == "native" or sys.platform != "win32":
            return "native"
        if SystemConfig.wsl_toolchain():
            return "wsl"
        return "native"

    # ---------------- Students this TA is responsible for ----------------

    @staticmethod
    def get_student_scope_text() -> str:
        return (os.getenv("TA_STUDENTS") or "").strip()

    @staticmethod
    def student_scope() -> "StudentScope":
        return StudentScope.parse(SystemConfig.get_student_scope_text())

    @staticmethod
    def to_wsl_path(path: str | Path) -> str:
        """Converts a Windows path (C:\\Users\\x) to its WSL mount path (/mnt/c/Users/x)."""
        p = str(Path(path).resolve())
        m = re.match(r"^([A-Za-z]):[\\/]*(.*)$", p)
        if not m:
            return p.replace("\\", "/")
        return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")

    @staticmethod
    def find_compiler(language: LanguageType = LanguageType.C) -> Tuple[Optional[str], str]:
        """
        Locates the appropriate compiler binary.
        Returns: (binary_path, compiler_type)
        """
        is_cpp = language in (LanguageType.CPP, LanguageType.AUTO)

        # 1. Check explicit environment override
        env_var = "GXX_PATH" if is_cpp else "GCC_PATH"
        env_path = os.getenv(env_var)
        if env_path and os.path.isfile(env_path):
            return env_path, "custom"

        # 2. Check known reliable portable MinGW / w64devkit directories first
        user_home = Path.home()
        portable_paths = [
            user_home / ".w64devkit" / "w64devkit" / "bin" / ("g++.exe" if is_cpp else "gcc.exe"),
            Path("C:/w64devkit/bin") / ("g++.exe" if is_cpp else "gcc.exe"),
            Path("C:/msys64/ucrt64/bin") / ("g++.exe" if is_cpp else "gcc.exe"),
            Path("C:/msys64/mingw64/bin") / ("g++.exe" if is_cpp else "gcc.exe"),
            Path("C:/MinGW/bin") / ("g++.exe" if is_cpp else "gcc.exe"),
        ]

        for p in portable_paths:
            if p.exists() and p.is_file():
                return str(p), "gcc"

        # 3. Check standard system PATH
        candidates = ["g++", "clang++"] if is_cpp else ["gcc", "clang"]
        for cand in candidates:
            found = shutil.which(cand)
            if found:
                return found, cand

        return None, "none"


class StudentScope:
    """
    The students a TA grades, written as ranges and single IDs, e.g.
    "cs25b1089-cs25b1103, cs25b2001-cs25b2008" or "1089-1103, 2001-2008, 2010".
    Matching uses the number at the end of an ID; a letter prefix (cs25b) must also match the
    student's email when one is known, so another batch's cs24b1095 is not mistaken for 1095.
    An empty scope includes everyone.
    """

    _ITEM = re.compile(r"^([A-Za-z0-9]*?)(\d+)$")

    def __init__(self, items: List[Tuple[str, int, int]], text: str = ""):
        self.items = items
        self.text = text

    @classmethod
    def parse(cls, text: str) -> "StudentScope":
        items: List[Tuple[str, int, int]] = []
        for part in re.split(r"[,;\n]+", text or ""):
            part = part.strip().lower()
            if not part:
                continue
            ends = [e.strip() for e in re.split(r"\s*(?:-|–|\bto\b)\s*", part) if e.strip()]
            if len(ends) not in (1, 2):
                raise ValueError(f"Could not read '{part}'. Use ranges like cs25b1089-cs25b1103 or single IDs like 2010.")
            parsed = []
            for e in ends:
                m = cls._ITEM.match(e)
                if not m:
                    raise ValueError(f"'{e}' is not a roll number (expected something like cs25b1089 or 1089).")
                parsed.append((m.group(1), int(m.group(2))))
            prefix = parsed[0][0] or (parsed[-1][0] if len(parsed) == 2 else "")
            lo, hi = parsed[0][1], parsed[-1][1]
            if lo > hi:
                lo, hi = hi, lo
            items.append((prefix, lo, hi))
        return cls(items, text or "")

    @property
    def active(self) -> bool:
        return bool(self.items)

    def size(self) -> int:
        return sum(hi - lo + 1 for _, lo, hi in self.items)

    def includes(self, roll_id: Optional[str], email: Optional[str] = None) -> bool:
        if not self.items:
            return True
        m = re.search(r"(\d+)\D*$", str(roll_id or ""))
        local = (email or "").split("@")[0].lower()
        if not m and local:
            m = re.search(r"(\d+)\D*$", local)
        if not m:
            return False
        number = int(m.group(1))
        for prefix, lo, hi in self.items:
            if not lo <= number <= hi:
                continue
            # With an email we can check the batch prefix; a bare roll ID like "1089" is matched on the number
            if prefix and local and not local.startswith(prefix):
                continue
            return True
        return False

    def describe(self) -> str:
        if not self.items:
            return "All students"
        return ", ".join(f"{p}{lo}" if lo == hi else f"{p}{lo}-{p}{hi}" for p, lo, hi in self.items)


class AssignmentLoader:
    """Loads and serializes assignment configurations from YAML or JSON."""

    @staticmethod
    def load_assignment(assignment_dir_or_file: str | Path) -> AssignmentConfig:
        path = Path(assignment_dir_or_file)

        if not path.exists():
            # Check inside 'assignments/' folder (relative to cwd, then to the project root)
            candidates = [Path("assignments") / assignment_dir_or_file, PROJECT_ROOT / "assignments" / assignment_dir_or_file]
            for alt_path in candidates:
                if alt_path.exists():
                    path = alt_path
                    break
            else:
                raise FileNotFoundError(f"Assignment '{assignment_dir_or_file}' not found (looked in '{path}' and 'assignments/').")

        if path.is_file():
            return AssignmentLoader._load_from_single_file(path)
        elif path.is_dir():
            return AssignmentLoader._load_from_directory(path)
        else:
            raise ValueError(f"Invalid path: {path}")

    @staticmethod
    def _read_data_file(path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            if path.suffix.lower() in (".yaml", ".yml"):
                return yaml.safe_load(f) or {}
            else:
                return json.load(f)

    @staticmethod
    def _load_from_single_file(path: Path) -> AssignmentConfig:
        raw = AssignmentLoader._read_data_file(path)
        return AssignmentConfig.model_validate(raw)

    @staticmethod
    def _load_from_directory(dir_path: Path) -> AssignmentConfig:
        # Check for unified assignment.json / assignment.yaml
        unified_json = dir_path / "assignment.json"
        unified_yaml = dir_path / "assignment.yaml"
        unified_yml = dir_path / "assignment.yml"

        base_data: Dict[str, Any] = {}
        if unified_json.exists():
            base_data = AssignmentLoader._read_data_file(unified_json)
        elif unified_yaml.exists():
            base_data = AssignmentLoader._read_data_file(unified_yaml)
        elif unified_yml.exists():
            base_data = AssignmentLoader._read_data_file(unified_yml)
        else:
            base_data = {
                "id": dir_path.name,
                "title": dir_path.name.replace("_", " ").title(),
                "description": f"Assignment pack for {dir_path.name}",
                "questions": []
            }

        # Check for modular files: rubric.json/yaml, tests.json/yaml, static_checks.json/yaml
        rubrics_data = AssignmentLoader._load_optional_modular_file(dir_path, "rubric")
        tests_data = AssignmentLoader._load_optional_modular_file(dir_path, "tests")
        static_data = AssignmentLoader._load_optional_modular_file(dir_path, "static_checks")

        # Merge modular pieces into questions if questions are defined
        questions_dict: Dict[str, Dict[str, Any]] = {}
        for q in base_data.get("questions", []):
            qid = q.get("id")
            if qid:
                questions_dict[qid] = q

        # Apply rubrics
        if rubrics_data:
            for qid, rub_items in rubrics_data.items():
                if qid in questions_dict:
                    questions_dict[qid]["rubric"] = rub_items
                else:
                    questions_dict[qid] = {
                        "id": qid,
                        "name": qid.upper(),
                        "rubric": rub_items
                    }

        # Apply tests
        if tests_data:
            for qid, test_items in tests_data.items():
                if qid in questions_dict:
                    questions_dict[qid]["test_cases"] = test_items
                else:
                    if qid not in questions_dict:
                        questions_dict[qid] = {"id": qid, "name": qid.upper()}
                    questions_dict[qid]["test_cases"] = test_items

        # Apply static checks
        if static_data:
            for qid, check_items in static_data.items():
                if qid in questions_dict:
                    questions_dict[qid]["static_checks"] = check_items

        base_data["questions"] = list(questions_dict.values())
        return AssignmentConfig.model_validate(base_data)

    @staticmethod
    def _load_optional_modular_file(dir_path: Path, stem: str) -> Optional[Dict[str, Any]]:
        for ext in [".json", ".yaml", ".yml"]:
            candidate = dir_path / f"{stem}{ext}"
            if candidate.exists():
                return AssignmentLoader._read_data_file(candidate)
        return None

    @staticmethod
    def save_assignment_tests(assignment_id_or_path: str | Path, tests_by_question: Dict[str, List[Dict[str, Any]]]) -> Path:
        """Saves or updates the tests.json file for a given assignment."""
        path = Path(assignment_id_or_path)
        if not path.is_dir():
            alt_path = Path("assignments") / assignment_id_or_path
            if alt_path.is_dir():
                path = alt_path
            else:
                path.mkdir(parents=True, exist_ok=True)

        target_file = path / "tests.json"
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(tests_by_question, f, indent=2)

        return target_file


class AssignmentManager:
    """Provides CLI and programmatic methods for scaffolding and managing weekly assignments."""

    @staticmethod
    def list_assignments(base_dir: str = "assignments") -> List[Dict[str, Any]]:
        p = Path(base_dir)
        if not p.exists():
            return []

        results = []
        for item in sorted(p.iterdir()):
            if item.is_dir() and not item.name.startswith((".", "_")):
                try:
                    cfg = AssignmentLoader.load_assignment(item)
                    results.append({
                        "id": cfg.id,
                        "title": cfg.title,
                        "description": cfg.description,
                        "path": str(item),
                        "questions_count": len(cfg.questions),
                        "questions": [q.model_dump(mode="json") for q in cfg.questions],
                        "marking": cfg.marking.model_dump()
                    })
                except Exception as e:
                    results.append({
                        "id": item.name,
                        "title": item.name,
                        "path": str(item),
                        "questions_count": 0,
                        "questions": [],
                        "error": str(e)
                    })
        return results

    @staticmethod
    def create_new_assignment(
        name: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        base_dir: str = "assignments",
        format_type: str = "json"  # "json" or "yaml"
    ) -> Path:
        """Creates a new assignment pack directory with template files."""
        clean_name = re.sub(r"[^a-z0-9_\-]", "", name.strip().replace(" ", "_").lower())
        if not clean_name:
            raise ValueError("Assignment name must contain letters or digits.")
        target_dir = Path(base_dir) / clean_name
        if (target_dir / "assignment.json").exists() or (target_dir / "assignment.yaml").exists():
            raise ValueError(f"Assignment '{clean_name}' already exists.")
        target_dir.mkdir(parents=True, exist_ok=True)

        title = title or clean_name.replace("_", " ").title()
        description = description or f"Weekly lab assignment: {title}"

        template_assignment = {
            "id": clean_name,
            "title": title,
            "description": description,
            "default_language": "c",
            "default_timeout_sec": 2.0,
            "questions": [
                {
                    "id": "q1",
                    "name": "Question 1",
                    "description": "Example lab problem description",
                    "language": "c",
                    "source_hints": ["q1", "question1"],
                    "compiler_flags": ["-std=c11", "-Wall"],
                    "timeout_sec": 2.0,
                    "rubric": [
                        {"id": "r1", "name": "Basic Correctness", "description": "Solves primary requirement"},
                        {"id": "r2", "name": "Edge Cases", "description": "Handles zero/negative inputs"}
                    ],
                    "static_checks": [
                        {
                            "rule_id": "check_no_goto",
                            "rule_type": "forbidden_regex",
                            "description": "Must not use goto statements",
                            "forbidden_pattern": r"\bgoto\b",
                            "severity": "warning"
                        }
                    ],
                    "test_cases": [
                        {
                            "id": "t1_normal",
                            "description": "Normal sample input",
                            "input_data": "5\n",
                            "expected_output": "10\n",
                            "comparison_mode": "whitespace_normalized",
                            "is_edge_case": False
                        }
                    ]
                }
            ]
        }

        if format_type.lower() == "yaml":
            out_file = target_dir / "assignment.yaml"
            with open(out_file, "w", encoding="utf-8") as f:
                yaml.dump(template_assignment, f, sort_keys=False, indent=2)
        else:
            out_file = target_dir / "assignment.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(template_assignment, f, indent=2)

        return target_dir

    @staticmethod
    def add_question_to_assignment(
        assignment_id: str,
        question_id: str,
        name: str,
        description: str,
        language: str = "c",
        source_hints: Optional[List[str]] = None,
        base_dir: str = "assignments"
    ) -> QuestionConfig:
        """Appends a new question to an existing assignment."""
        target_dir = AssignmentManager.resolve_assignment_dir(assignment_id, base_dir)
        cfg = AssignmentLoader.load_assignment(target_dir)

        question_id = question_id.strip().lower()
        if not re.fullmatch(r"[a-z0-9_\-]+", question_id):
            raise ValueError("Question ID may only contain letters, digits, '_' and '-'.")
        if any(q.id.lower() == question_id for q in cfg.questions):
            raise ValueError(f"Question '{question_id}' already exists in '{assignment_id}'. Edit it instead.")

        new_q = QuestionConfig(
            id=question_id.lower(),
            name=name,
            description=description,
            language=LanguageType(language.lower()),
            source_hints=source_hints or [question_id.lower(), f"question_{question_id.lower()}"],
            compiler_flags=["-std=c11", "-Wall"] if language.lower() == "c" else ["-std=c++17", "-Wall"],
            timeout_sec=2.0,
            rubric=[
                QuestionRequirement(id="req_core", name="Core Logic", description="Correct implementation of assignment requirements")
            ],
            static_checks=[],
            test_cases=[]
        )

        cfg.questions.append(new_q)

        # Save back
        AssignmentManager.save_assignment(cfg, target_dir)
        return new_q

    @staticmethod
    def save_assignment(cfg: AssignmentConfig, target_dir: Path):
        """Persists AssignmentConfig back to JSON or YAML."""
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        json_path = target_dir / "assignment.json"
        yaml_path = target_dir / "assignment.yaml"
        if not yaml_path.exists() and (target_dir / "assignment.yml").exists():
            yaml_path = target_dir / "assignment.yml"

        # mode="json" turns enums into plain strings so YAML output stays loadable with safe_load
        dump_dict = cfg.model_dump(mode="json")

        if yaml_path.exists():
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(dump_dict, f, sort_keys=False, indent=2, allow_unicode=True)
        else:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(dump_dict, f, indent=2)

        # Modular override files (tests.json, rubric.json, static_checks.json) take precedence on load,
        # so keep them in sync or edits made here would be silently discarded on the next load.
        modular_fields = {"tests": "test_cases", "rubric": "rubric", "static_checks": "static_checks"}
        for stem, field in modular_fields.items():
            for ext in (".json", ".yaml", ".yml"):
                mod_file = target_dir / f"{stem}{ext}"
                if not mod_file.exists():
                    continue
                payload = {q["id"]: q.get(field, []) for q in dump_dict.get("questions", [])}
                with open(mod_file, "w", encoding="utf-8") as f:
                    if ext == ".json":
                        json.dump(payload, f, indent=2)
                    else:
                        yaml.safe_dump(payload, f, sort_keys=False, indent=2, allow_unicode=True)
                break

    @staticmethod
    def resolve_assignment_dir(assignment_id: str, base_dir: str = "assignments") -> Path:
        """Finds the directory that holds an assignment pack."""
        for candidate in (Path(base_dir) / assignment_id, PROJECT_ROOT / base_dir / assignment_id, Path(assignment_id)):
            if candidate.is_dir():
                return candidate
        raise FileNotFoundError(f"Assignment '{assignment_id}' not found.")

    @staticmethod
    def get_assignment(assignment_id: str, base_dir: str = "assignments") -> AssignmentConfig:
        """Retrieves an assignment configuration."""
        target_dir = AssignmentManager.resolve_assignment_dir(assignment_id, base_dir)
        return AssignmentLoader.load_assignment(target_dir)

    @staticmethod
    def update_assignment(
        assignment_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        base_dir: str = "assignments"
    ) -> AssignmentConfig:
        """Updates assignment-level metadata."""
        target_dir = AssignmentManager.resolve_assignment_dir(assignment_id, base_dir)

        cfg = AssignmentLoader.load_assignment(target_dir)
        if title is not None:
            cfg.title = title
        if description is not None:
            cfg.description = description

        AssignmentManager.save_assignment(cfg, target_dir)
        return cfg

    @staticmethod
    def delete_assignment(assignment_id: str, base_dir: str = "assignments") -> bool:
        """Deletes an assignment pack directory safely on Windows and Unix."""
        import shutil
        import stat
        import os
        import time

        # Only ever delete folders that live directly inside the assignments directory
        base = Path(base_dir).resolve()
        target_dir = (base / assignment_id).resolve()
        if target_dir.parent != base or not target_dir.is_dir():
            raise FileNotFoundError(f"Assignment '{assignment_id}' not found in '{base_dir}'.")

        def _handle_remove_readonly(func, path, exc_info):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                time.sleep(0.15)
                try:
                    func(path)
                except Exception:
                    pass

        try:
            shutil.rmtree(target_dir, onexc=_handle_remove_readonly)
        except Exception:
            # Fallback for older python
            shutil.rmtree(target_dir, ignore_errors=True)

        return True

    @staticmethod
    def update_question(
        assignment_id: str,
        question_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        language: Optional[str] = None,
        compiler_flags: Optional[List[str]] = None,
        timeout_sec: Optional[float] = None,
        test_cases: Optional[List[Dict[str, Any]]] = None,
        rubric: Optional[List[Dict[str, Any]]] = None,
        static_checks: Optional[List[Dict[str, Any]]] = None,
        base_dir: str = "assignments"
    ) -> QuestionConfig:
        """Updates an existing question within an assignment."""
        target_dir = AssignmentManager.resolve_assignment_dir(assignment_id, base_dir)

        cfg = AssignmentLoader.load_assignment(target_dir)
        target_q: Optional[QuestionConfig] = None

        for q in cfg.questions:
            if q.id.lower() == question_id.lower():
                target_q = q
                break

        if not target_q:
            raise ValueError(f"Question '{question_id}' not found in assignment '{assignment_id}'.")

        if name is not None:
            target_q.name = name
        if description is not None:
            target_q.description = description
        if language is not None:
            target_q.language = LanguageType(language.lower())
        if compiler_flags is not None:
            target_q.compiler_flags = compiler_flags
        if timeout_sec is not None:
            target_q.timeout_sec = float(timeout_sec)
        if test_cases is not None:
            target_q.test_cases = [TestCase(**t) if isinstance(t, dict) else t for t in test_cases]
        if rubric is not None:
            target_q.rubric = [QuestionRequirement(**r) if isinstance(r, dict) else r for r in rubric]
        if static_checks is not None:
            target_q.static_checks = [StaticCheckRule(**s) if isinstance(s, dict) else s for s in static_checks]

        AssignmentManager.save_assignment(cfg, target_dir)
        return target_q

    @staticmethod
    def delete_question(assignment_id: str, question_id: str, base_dir: str = "assignments") -> bool:
        """Deletes a question from an assignment."""
        target_dir = AssignmentManager.resolve_assignment_dir(assignment_id, base_dir)

        cfg = AssignmentLoader.load_assignment(target_dir)
        initial_len = len(cfg.questions)
        cfg.questions = [q for q in cfg.questions if q.id.lower() != question_id.lower()]

        if len(cfg.questions) == initial_len:
            raise ValueError(f"Question '{question_id}' not found in assignment '{assignment_id}'.")

        AssignmentManager.save_assignment(cfg, target_dir)
        return True

