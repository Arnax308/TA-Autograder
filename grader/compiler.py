"""
Compiler interface for GCC and G++.
Compiles student source files safely, supports multi-file linking and header resolution,
produces concise informative error summaries, and preserves full logs.

Two backends are supported:
- native: the Windows (MinGW/w64devkit) or Unix toolchain on this machine.
- wsl:    gcc/g++ inside WSL. The resulting Linux binaries run inside WSL as well, which sidesteps
          Windows Smart App Control / Defender blocking freshly compiled unsigned .exe files.
"""

from __future__ import annotations
import os
import sys
import re
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Set

from grader.config import SystemConfig
from grader.models import CompilerResult, LanguageType

CODE_EXTS = (".c", ".cpp", ".cc", ".cxx")
CPP_EXTS = (".cpp", ".cc", ".cxx", ".hpp")
HEADER_EXTS = (".h", ".hpp")

_MAIN_RE = re.compile(r"\b(?:int|void)\s+main\s*\(")
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_CPP_CONSTRUCT_RE = re.compile(
    r"#\s*include\s*<(?:iostream|string|vector|fstream|sstream|iomanip|algorithm|cstdio|cstdlib|cmath|map)>"
    r"|\bstd::|\busing\s+namespace\b|\bclass\s+\w+\s*[:{]|\btemplate\s*<|\bcout\s*<<|\bcin\s*>>"
)
WINDOWS_ONLY_HEADER_RE = re.compile(r"#\s*include\s*[<\"](?:conio|windows|dos|direct|io|process)\.h[>\"]", re.IGNORECASE)
# gcc quotes identifiers with ASCII quotes or, in UTF-8 locales (e.g. WSL), with ‘curly’ quotes
_BOOL_ERROR_RE = re.compile(r"unknown type name ['‘]bool['’]|['‘](?:true|false|bool)['’] undeclared")


def strip_comments(source: str) -> str:
    return _COMMENT_RE.sub(" ", source)


def read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def has_main(source: str) -> bool:
    """True if the (comment-stripped) source defines main()."""
    return bool(_MAIN_RE.search(strip_comments(source)))


def looks_like_cpp(source: str) -> bool:
    return bool(_CPP_CONSTRUCT_RE.search(strip_comments(source)))


def detect_language(files: List[Path]) -> LanguageType:
    """Language the student actually wrote, from extensions and content (C++ in a .c file counts as C++)."""
    for f in files:
        if f.suffix.lower() in CPP_EXTS:
            return LanguageType.CPP
    for f in files:
        if looks_like_cpp(read_source(f)):
            return LanguageType.CPP
    return LanguageType.C


def uses_windows_only_headers(files: List[Path]) -> bool:
    return any(WINDOWS_ONLY_HEADER_RE.search(read_source(f)) for f in files)


class Compiler:
    """Handles compilation of C and C++ source files with automatic multi-file and header resolution."""

    def __init__(self, log_dir: str | Path = "logs/compilation"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def compile(
        self,
        source_files: List[Path],
        output_executable: Path,
        language: LanguageType = LanguageType.C,
        compiler_flags: Optional[List[str]] = None,
        student_id: str = "unknown",
        question_id: str = "unknown",
        backend: Optional[str] = None
    ) -> CompilerResult:
        """
        Compiles the given source files into output_executable.
        Automatically resolves headers, includes, math library (-lm), and multi-file projects.
        Returns a structured CompilerResult.
        """
        start_time = time.time()
        source_files = [Path(f) for f in source_files]
        backend = backend or SystemConfig.resolve_exec_backend()

        if not source_files:
            return CompilerResult(
                success=False,
                returncode=-1,
                concise_error="No source files provided for compilation.",
                backend=backend
            )

        # Separate compilation units (.c, .cpp) from headers (.h, .hpp). Headers are found through -I
        # and are never passed to the compiler directly (that would try to build precompiled headers).
        code_files = [f for f in source_files if f.suffix.lower() in CODE_EXTS]
        header_files = [f for f in source_files if f.suffix.lower() in HEADER_EXTS]

        # If only headers were mapped (e.g., student named question 'matrix.h'), look for companion .c/.cpp files
        if not code_files and header_files:
            parent_dir = header_files[0].parent
            code_files = sorted(p for ext in ("*.c", "*.cpp", "*.cc") for p in parent_dir.glob(ext))
            if not code_files:
                return CompilerResult(
                    success=False,
                    returncode=-1,
                    concise_error="Only header files (.h/.hpp) found without any executable .c or .cpp implementation.",
                    backend=backend
                )

        # Detect effective language from what the student actually wrote
        detected = detect_language(code_files + header_files)
        is_cpp = language == LanguageType.CPP or detected == LanguageType.CPP
        code_files = self._dedupe_language_variants(code_files, is_cpp)

        # Filter conflicting files with duplicate main() definitions
        files_with_main = [cf for cf in code_files if has_main(read_source(cf))]
        files_without_main = [cf for cf in code_files if cf not in files_with_main]

        if len(files_with_main) > 1:
            qid_lower = str(question_id).lower().replace("_driver", "")
            best_main = next(
                (cf for cf in files_with_main if "drivers" in cf.resolve().parts),
                None
            ) or next(
                (cf for cf in files_with_main if qid_lower in cf.stem.lower() or "main" in cf.stem.lower()),
                files_with_main[0]
            )
            code_files = [best_main] + files_without_main
        elif not files_with_main and code_files:
            # Function-library submission without main(): link against an empty harness so it still builds
            harness_file = output_executable.parent / f"_harness_{question_id}.{'cpp' if is_cpp else 'c'}"
            try:
                output_executable.parent.mkdir(parents=True, exist_ok=True)
                harness_file.write_text("int main(void) {\n    return 0;\n}\n", encoding="utf-8")
                code_files.append(harness_file)
            except Exception:
                pass

        # Collect unique include directories for -I: every file's own folder before any parent folder,
        # so lab11/shape/circle.h wins over a same-named lab11/circle.h from another task (the first
        # -I directory holding the header is the one the compiler uses)
        include_dirs: List[Path] = []
        for d in [f.parent.resolve() for f in code_files + header_files] + [
            f.parent.parent.resolve() for f in code_files + header_files if f.parent.parent.exists()
        ]:
            if d not in include_dirs:
                include_dirs.append(d)

        # Code that needs Windows-only headers (conio.h, windows.h) cannot build in WSL
        if backend == "wsl" and uses_windows_only_headers(code_files + header_files):
            backend = "native"

        compiler_bin = self._compiler_binary(is_cpp, backend)
        if not compiler_bin:
            return CompilerResult(
                success=False,
                returncode=-1,
                concise_error=f"No suitable {'C++ (g++)' if is_cpp else 'C (gcc)'} compiler found in system PATH or standard locations.",
                duration_sec=0.0,
                backend=backend
            )

        flags = self._build_flags(is_cpp, compiler_flags, backend)
        output_executable.parent.mkdir(parents=True, exist_ok=True)
        if output_executable.exists():
            try:
                output_executable.unlink()
            except OSError:
                pass

        working_dir = code_files[0].parent if code_files else Path.cwd()
        cmd: List[str] = []
        attempts_log: List[str] = []

        try:
            cmd = self._build_command(compiler_bin, code_files, output_executable, include_dirs, flags, backend)
            res = self._run(cmd, working_dir, compiler_bin, backend)
            attempts_log.append(self._format_attempt(cmd, res))

            # Retry 1: student relies on bool/true/false without including <stdbool.h>
            if res.returncode != 0 and not is_cpp and _BOOL_ERROR_RE.search(res.stderr or ""):
                retry_flags = flags + ["-include", "stdbool.h"]
                retry_cmd = self._build_command(compiler_bin, code_files, output_executable, include_dirs, retry_flags, backend)
                retry_res = self._run(retry_cmd, working_dir, compiler_bin, backend)
                attempts_log.append(self._format_attempt(retry_cmd, retry_res))
                if retry_res.returncode == 0 or len(retry_res.stderr or "") < len(res.stderr or ""):
                    res, cmd, flags = retry_res, retry_cmd, retry_flags

            # Retry 2: undefined references -> link in other implementation files from the same folder
            if res.returncode != 0 and "undefined reference to" in (res.stderr or ""):
                extra_candidates = []
                for ext in ("*.c", "*.cpp", "*.cc"):
                    for candidate in working_dir.glob(ext):
                        if candidate not in code_files and candidate.is_file() and not has_main(read_source(candidate)):
                            extra_candidates.append(candidate)
                extra_candidates = self._dedupe_language_variants(extra_candidates, is_cpp)

                if extra_candidates:
                    retry_cmd = self._build_command(compiler_bin, code_files + extra_candidates, output_executable, include_dirs, flags, backend)
                    retry_res = self._run(retry_cmd, working_dir, compiler_bin, backend)
                    attempts_log.append(self._format_attempt(retry_cmd, retry_res))
                    if retry_res.returncode == 0:
                        res, cmd = retry_res, retry_cmd

            duration = time.time() - start_time
            success = (res.returncode == 0) and output_executable.exists()

            # On Windows, unblock freshly compiled binary (removes Mark-of-the-Web style zone info)
            if success and backend == "native" and sys.platform == "win32":
                try:
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command", f"Unblock-File -LiteralPath '{output_executable.resolve()}'"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5.0
                    )
                except Exception:
                    pass

            log_file = self._write_log(student_id, question_id, backend, attempts_log)

            concise_err = None
            if not success:
                concise_err = self._extract_concise_error(res.stderr or res.stdout or "")
                if res.returncode == 0 and not output_executable.exists():
                    concise_err = "Compiler reported success but produced no executable (it may have been quarantined by antivirus)."

            return CompilerResult(
                success=success,
                returncode=res.returncode,
                stdout=res.stdout or "",
                stderr=res.stderr or "",
                concise_error=concise_err,
                full_log_path=str(log_file),
                duration_sec=duration,
                command=" ".join(cmd),
                backend=backend,
                language_used=LanguageType.CPP if is_cpp else LanguageType.C
            )

        except subprocess.TimeoutExpired:
            return CompilerResult(
                success=False,
                returncode=-1,
                concise_error="Compiler process timed out after 90 seconds.",
                duration_sec=time.time() - start_time,
                command=" ".join(cmd),
                backend=backend
            )
        except Exception as e:
            return CompilerResult(
                success=False,
                returncode=-1,
                concise_error=f"Compilation execution failed: {str(e)}",
                duration_sec=time.time() - start_time,
                command=" ".join(cmd),
                backend=backend
            )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _compiler_binary(is_cpp: bool, backend: str) -> Optional[str]:
        if backend == "wsl":
            tc = SystemConfig.wsl_toolchain()
            return ("g++" if is_cpp else "gcc") if tc else None
        compiler_bin, _ = SystemConfig.find_compiler(LanguageType.CPP if is_cpp else LanguageType.C)
        return compiler_bin

    @staticmethod
    def _build_flags(is_cpp: bool, compiler_flags: Optional[List[str]], backend: str) -> List[str]:
        flags = ["-std=c++17", "-Wall"] if is_cpp else ["-std=gnu11", "-Wall"]
        if backend == "native" and sys.platform == "win32":
            # Static runtime so binaries do not depend on MinGW DLLs being on PATH
            flags += ["-static-libgcc", "-static-libstdc++"] if is_cpp else ["-static-libgcc"]

        extra = list(compiler_flags or [])
        i = 0
        while i < len(extra):
            flag = extra[i]
            # A C standard flag is meaningless for g++ (and vice versa), and question packs often carry -std=c11
            if flag.startswith("-std="):
                std = flag[5:].lower()
                wants_cpp = "++" in std
                if wants_cpp == is_cpp:
                    flags = [f for f in flags if not f.startswith("-std=")] + [flag]
                i += 1
                continue
            if flag in ("-I", "-include") and i + 1 < len(extra):
                flags += [flag, extra[i + 1]]
                i += 2
                continue
            if flag not in flags:
                flags.append(flag)
            i += 1
        return flags

    @staticmethod
    def _build_command(
        compiler_bin: str,
        code_files: List[Path],
        output_executable: Path,
        include_dirs: List[Path],
        flags: List[str],
        backend: str
    ) -> List[str]:
        def p(path: Path | str) -> str:
            return SystemConfig.to_wsl_path(path) if backend == "wsl" else str(Path(path).resolve())

        # LC_ALL=C keeps gcc's messages in plain ASCII (no curly quotes that render as mojibake)
        cmd = (["wsl.exe", "-e", "env", "LC_ALL=C", compiler_bin] if backend == "wsl" else [compiler_bin])
        cmd += [p(cf) for cf in code_files]
        cmd += ["-o", p(output_executable)]
        for idir in include_dirs:
            cmd += ["-I", p(idir)]

        i = 0
        while i < len(flags):
            if flags[i] == "-I" and i + 1 < len(flags):
                cmd += ["-I", p(flags[i + 1])]
                i += 2
                continue
            if flags[i] == "-include" and i + 1 < len(flags):
                # A bare header name (stdbool.h) is looked up by the compiler; only a file path is translated
                arg = flags[i + 1]
                cmd += ["-include", p(arg) if Path(arg).is_absolute() else arg]
                i += 2
                continue
            cmd.append(flags[i])
            i += 1
        cmd.append("-lm")  # math library must come after the sources
        return cmd

    @staticmethod
    def _run(cmd: List[str], working_dir: Path, compiler_bin: str, backend: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        if backend == "native":
            # Ensure compiler bin directory is in PATH for internal tools (as.exe, ld.exe)
            env["PATH"] = str(Path(compiler_bin).parent.resolve()) + os.pathsep + env.get("PATH", "")
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90.0,
            cwd=str(working_dir),
            env=env
        )

    @staticmethod
    def _dedupe_language_variants(code_files: List[Path], is_cpp: bool) -> List[Path]:
        """
        Students often submit both Rational.c and Rational.cpp (or a copy) of the same unit, which
        fails to link with 'multiple definition'. Keep one file per stem and folder, preferring the
        extension that matches the language being compiled.
        """
        preferred = (".cpp", ".cc", ".cxx", ".c") if is_cpp else (".c", ".cpp", ".cc", ".cxx")
        chosen: dict = {}
        for f in code_files:
            key = (str(f.parent).lower(), f.stem.lower())
            current = chosen.get(key)
            if current is None or preferred.index(f.suffix.lower()) < preferred.index(current.suffix.lower()):
                chosen[key] = f
        keep = set(chosen.values())
        return [f for f in code_files if f in keep]

    @staticmethod
    def _format_attempt(cmd: List[str], res: subprocess.CompletedProcess) -> str:
        return (
            f"Command: {' '.join(cmd)}\n"
            f"Exit Code: {res.returncode}\n"
            f"--- STDOUT ---\n{res.stdout}\n"
            f"--- STDERR ---\n{res.stderr}\n"
        )

    def _write_log(self, student_id: str, question_id: str, backend: str, attempts: List[str]) -> Path:
        log_file = self.log_dir / f"{student_id}_{question_id}.log"
        try:
            with open(log_file, "w", encoding="utf-8") as lf:
                lf.write(f"Backend: {backend}\n\n")
                lf.write("\n".join(attempts))
        except OSError:
            pass
        return log_file

    def _extract_concise_error(self, raw_log: str) -> str:
        """
        Parses raw compiler stderr to extract concise, informative, and actionable error lines.
        Prioritizes specific syntax errors, missing functions, and undefined references over
        generic linker exit lines like 'collect2.exe: error: ld returned 1 exit status'.
        """
        if not raw_log.strip():
            return "Compilation failed with non-zero exit code (no error output captured)."

        lines = raw_log.splitlines()
        error_lines = []
        linker_detail_lines = []

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Ignore generic collect2 wrapper line if we can find the real root cause
            if re.search(r"collect2(?:\.exe)?: error: ld returned", line_str):
                continue

            # Check for undefined reference / linker error details
            if "undefined reference to" in line_str or "multiple definition of" in line_str or "cannot find" in line_str:
                cleaned_ld = re.sub(r"^.*?:\s*((?:undefined reference to|multiple definition of).*)$", r"\1", line_str)
                cleaned_ld = re.sub(r";\s*\S+:\(\.text.*$", "", cleaned_ld)
                if cleaned_ld not in linker_detail_lines:
                    linker_detail_lines.append(cleaned_ld)
                continue

            # Look for lines with 'error:' or 'fatal error:'
            if "error:" in line_str.lower():
                cleaned = re.sub(r"^.*?([^\\/]+:\d+:\d+:\s*(?:fatal\s+)?error:.*)$", r"\1", line_str)
                error_lines.append(cleaned or line_str)
                if len(error_lines) >= 3:
                    break

        if error_lines:
            return " | ".join(error_lines[:3])

        if linker_detail_lines:
            return f"Linker Error: {' | '.join(linker_detail_lines[:3])}"

        # Fallback to first non-empty lines
        first_non_empty = [l.strip() for l in lines if l.strip()][:2]
        return " | ".join(first_non_empty) if first_non_empty else "Compilation failed."
