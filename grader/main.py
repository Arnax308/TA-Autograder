"""
Main pipeline orchestrator and CLI entrypoint for the C/C++ TA Autograder.
"""

from __future__ import annotations
import argparse
import re
import os
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Dict, Optional, Tuple

import colorama
from colorama import Fore, Style

from grader.models import (
    AssignmentConfig,
    QuestionConfig,
    StudentGradingResult,
    QuestionGradingResult,
    CompilerResult,
    TestEvaluation,
    CheckStatus,
    LanguageType,
)
from grader.config import AssignmentLoader, AssignmentManager, SystemConfig, PROJECT_ROOT
from grader.extractor import Extractor
from grader.compiler import Compiler, CODE_EXTS, HEADER_EXTS, detect_language, has_main, read_source, strip_comments
from grader.runner import SandboxRunner, BLOCKED_ERROR_TYPES
from grader.tester import Tester
from grader.analyzer import StaticAnalyzer
from grader.llm import GeminiReviewer
from grader.generator import TestCaseGenerator
from grader.reporter import Reporter
from grader.cache import ResultsCache
from grader.marks import read_late_flag

colorama.init(autoreset=True)

ARCHIVE_EXTS = (".zip",)


def list_submission_items(submissions_path: str | Path, only_my_students: bool = True) -> List[Tuple[str, Path]]:
    """
    Returns [(kind, path)] for every student .zip archive or folder in the submissions directory.
    By default only students inside the TA's configured range (TA_STUDENTS) are returned, so nobody
    else is compiled, run, or sent to Gemini.
    """
    items = _all_submission_items(submissions_path)
    scope = SystemConfig.student_scope()
    if only_my_students and scope.active:
        items = [(k, p) for k, p in items if scope.includes(p.stem)]
    return items


def _all_submission_items(submissions_path: str | Path) -> List[Tuple[str, Path]]:
    sub_dir = Path(submissions_path)
    items: List[Tuple[str, Path]] = []
    seen = set()
    if not sub_dir.is_dir():
        return items
    for p in sorted(sub_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in ARCHIVE_EXTS:
            items.append(("zip", p))
            seen.add(p.stem.lower())
    for p in sorted(sub_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir() and not p.name.startswith((".", "_")) and p.name.lower() not in seen:
            items.append(("dir", p))
            seen.add(p.name.lower())
    items.sort(key=lambda kp: kp[1].stem.lower())
    return items


def hardcoded_answers(code: str, question) -> List[str]:
    """Expected answer lines (with a real value, not just a label or a lone 0) that appear as string literals in a driver."""
    literals = "\n".join(re.findall(r'"((?:[^"\\\n]|\\.)*)"', strip_comments(code or "")))
    literals = literals.replace("\\n", "\n").replace("\\t", "\t")
    literal_lines = {ln.strip() for ln in literals.splitlines() if ln.strip()}
    hits: List[str] = []
    for tc in question.test_cases:
        given = re.sub(r"\s+", " ", tc.input_data or "")
        for line in (tc.expected_output or "").splitlines():
            line = line.strip()
            value = line.split(":", 1)[1] if ":" in line else line
            if re.sub(r"\s+", " ", value).strip() in given:
                continue  # it is part of the test's input (e.g. a matrix fed through a string stream)
            if len(re.findall(r"\d", value)) >= 2 and line in literal_lines and line not in hits:
                hits.append(line)
    return hits


def private_access_error(stderr: Optional[str]) -> bool:
    """True when every compile error is about accessing private/protected members."""
    errors = [ln for ln in (stderr or "").splitlines() if ": error:" in ln]
    return bool(errors) and all(re.search(r"is (private|protected) within this context", ln) for ln in errors)


def open_access_flags(bin_dir: Path) -> List[str]:
    """
    Flags that make private/protected members public for a TA driver. A plain -Dprivate=public also
    rewrites the standard library headers (<sstream> then fails with "redeclared with different
    access"), so a prelude header pulls in the whole standard library first and only then defines
    the keywords; later #includes of std headers are no-ops thanks to their include guards.
    """
    prelude = bin_dir / "_ta_open_access.h"
    prelude.parent.mkdir(parents=True, exist_ok=True)
    prelude.write_text(
        "#ifdef __cplusplus\n#include <bits/stdc++.h>\n#endif\n"
        "#define private public\n#define protected public\n",
        encoding="utf-8",
    )
    return ["-include", str(prelude)]


def cheat_feedback(cheats: List[str]) -> str:
    return ("REJECTED: the driver printed expected answers as string literals instead of computing them with the "
            "student's code: " + " | ".join(cheats[:5]) + ". Print only labels yourself; every value must come from "
            "calling the student's functions/classes. If something is missing print `MISSING: ...`. If the student's "
            "function prints extra lines, let it print them.")


class AutograderPipeline:
    """Orchestrates end-to-end grading across all three evaluation layers."""

    def __init__(
        self,
        assignment_id_or_path: str,
        output_dir: str = "reports",
        results_dir: str = "results",
        log_dir: str = "logs",
        verbose: bool = False,
        log_callback: Optional[Callable[[str], None]] = None,
        llm_review: Optional[bool] = None
    ):
        self.assignment = AssignmentLoader.load_assignment(assignment_id_or_path)
        self.reporter = Reporter(output_dir)
        self.cache = ResultsCache(results_dir)
        self.compiler = Compiler(Path(log_dir) / "compilation")
        self.llm_reviewer = GeminiReviewer()
        self.verbose = verbose
        self.log_callback = log_callback
        # The qualitative LLM review can be switched off to save tokens; Comp 1/4 then come from code metrics
        self.llm_review_enabled = llm_review if llm_review is not None else os.getenv("LLM_REVIEW", "on").lower() != "off"
        self._is_recheck = False  # Set True when re-checking a specific student to force driver regeneration
        self.scratch_root = PROJECT_ROOT / "scratch"
        self.drivers_root = PROJECT_ROOT / "drivers"

    def _log(self, message: str) -> None:
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass
        elif self.verbose:
            print(f"    {message}")

    def grade_batch(
        self,
        submissions_path: str | Path,
        filter_student: Optional[str] = None,
        filter_question: Optional[str] = None,
        rerun_all: bool = False,
        rerun_llm_only: bool = False
    ) -> List[StudentGradingResult]:
        """Processes all student submissions in the given directory or archive list."""
        sub_dir = Path(submissions_path)
        if not sub_dir.exists():
            print(f"{Fore.RED}Error: Submissions directory '{sub_dir}' not found.{Style.RESET_ALL}")
            return []

        items_to_process = list_submission_items(sub_dir)
        if filter_student:
            items_to_process = [(k, p) for k, p in items_to_process if filter_student.lower() in p.stem.lower()]

        if not items_to_process:
            print(f"{Fore.YELLOW}Warning: No .zip archives or student folders found in '{sub_dir}'.{Style.RESET_ALL}")
            return []

        print(f"\n{Fore.CYAN}Starting evaluation for assignment: {Style.BRIGHT}{self.assignment.title}{Style.RESET_ALL}")
        print(f"Execution backend: {SystemConfig.resolve_exec_backend()}")
        print(f"Found {len(items_to_process)} submissions to grade.\n")

        results: List[StudentGradingResult] = []

        for idx, (kind, path) in enumerate(items_to_process, 1):
            student_id = path.stem
            print(f"[{idx}/{len(items_to_process)}] Processing: {Fore.WHITE}{Style.BRIGHT}{student_id}{Style.RESET_ALL}...", end=" ", flush=True)

            res = self.grade_single_student(
                submission_path=path,
                is_zip=(kind == "zip"),
                filter_question=filter_question,
                rerun_all=rerun_all,
                rerun_llm_only=rerun_llm_only
            )
            results.append(res)

            # Status indicator
            if res.error_message:
                print(f"{Fore.RED}ERROR ({res.error_message}){Style.RESET_ALL}")
            elif res.manual_review_recommended:
                print(f"{Fore.YELLOW}MANUAL REVIEW NEEDED ({res.total_tests_passed}/{res.total_tests_run} tests){Style.RESET_ALL}")
            else:
                print(f"{Fore.GREEN}OK ({res.total_tests_passed}/{res.total_tests_run} tests){Style.RESET_ALL}")

        # Batch reporting
        batch_summary = self.reporter.generate_and_save_batch_summary(results, self.assignment.id)
        self.reporter.print_batch_console_summary(batch_summary)

        return results

    def grade_single_student(
        self,
        submission_path: Path,
        is_zip: bool = True,
        filter_question: Optional[str] = None,
        rerun_all: bool = False,
        rerun_llm_only: bool = False
    ) -> StudentGradingResult:
        """Grades a single student across all questions."""
        self._is_recheck = rerun_all  # Flag for _grade_question to know if it should regenerate drivers
        submission_path = Path(submission_path)
        student_id = submission_path.stem

        # Check intermediate cache for --rerun-llm or idempotency. A cached result from a different
        # assignment (e.g. last week's run in the same results folder) is never reused.
        cached = self.cache.load_result(student_id)
        # TA-confirmed marks survive re-grading, unless the student resubmitted after they were entered
        prior_ta_marks, prior_ta_at = {}, None
        if cached and cached.ta_marks and (not cached.assignment_id or cached.assignment_id == self.assignment.id):
            try:
                entered = datetime.fromisoformat(cached.ta_marks_at).timestamp() if cached.ta_marks_at else 0
            except ValueError:
                entered = 0
            if submission_path.exists() and submission_path.stat().st_mtime <= entered:
                prior_ta_marks, prior_ta_at = dict(cached.ta_marks), cached.ta_marks_at
        if cached and cached.assignment_id and cached.assignment_id != self.assignment.id:
            cached = None
        if cached and not rerun_all and not rerun_llm_only and not filter_question:
            # A resubmission (e.g. re-downloaded from Classroom) is newer than its cached result: grade it again
            try:
                graded_at = datetime.fromisoformat(cached.timestamp).timestamp() if cached.timestamp else 0
            except ValueError:
                graded_at = 0
            if submission_path.stat().st_mtime <= graded_at:
                return cached
            cached = None

        # Step 1: Extract submission files (to a stable per-student folder, so source stays viewable)
        extracted_dir: Optional[Path] = None
        if is_zip:
            target = self.scratch_root / "extracted" / self.assignment.id / student_id
            extracted_dir, extract_err = Extractor.extract_zip(submission_path, target_dir=target)
            if extract_err or not extracted_dir:
                res = StudentGradingResult(
                    student_id=student_id,
                    assignment_id=self.assignment.id,
                    zip_path=str(submission_path),
                    manual_review_recommended=True,
                    manual_review_reasons=[f"Extraction error: {extract_err}"],
                    timestamp=datetime.now().isoformat(),
                    error_message=extract_err
                )
                self.cache.save_result(res)
                self.reporter.save_student_report(res, self.assignment)
                return res
        else:
            extracted_dir = submission_path

        # Step 2: Map source files to assignment questions
        # Only extracted copies are touched; a submission folder on disk is never renamed in place
        renamed_files = Extractor.fix_double_extensions(extracted_dir) if is_zip else []
        source_files = Extractor.collect_source_files(extracted_dir)
        question_file_map = Extractor.map_files_to_questions(source_files, self.assignment)

        # Step 3: Evaluate each question. When only one question is re-graded, keep the others from cache.
        question_results: Dict[str, QuestionGradingResult] = {}
        if filter_question and cached:
            question_results.update(cached.question_results)

        scratch_bin_dir = self.scratch_root / "binaries" / student_id
        scratch_bin_dir.mkdir(parents=True, exist_ok=True)

        for q in self.assignment.questions:
            if filter_question and filter_question.lower() not in q.id.lower():
                continue

            # Check if we can reuse cached objective data for --rerun-llm
            cached_q = cached.question_results.get(q.id) if (cached and rerun_llm_only) else None
            self._log(f"{student_id} · {q.id.upper()}: grading")
            try:
                q_res = self._grade_question(
                    student_id=student_id,
                    question=q,
                    mapped_files=question_file_map.get(q.id, []),
                    bin_dir=scratch_bin_dir,
                    all_source_files=source_files,
                    cached_result=cached_q,
                    rerun_llm=rerun_llm_only
                )
            except Exception as e:
                # One broken question must never take down the whole student (or the batch)
                q_res = QuestionGradingResult(
                    question_id=q.id,
                    question_name=q.name,
                    language=q.language,
                    source_files=[str(f) for f in question_file_map.get(q.id, [])],
                    needs_manual_review=True,
                    manual_review_reasons=[f"Internal grading error: {e}"],
                    grading_error=traceback.format_exc(limit=4)
                )
            question_results[q.id] = q_res

        # Keep questions in assignment order
        order = {q.id: i for i, q in enumerate(self.assignment.questions)}
        question_results = dict(sorted(question_results.items(), key=lambda kv: order.get(kv[0], 999)))

        total_tests_passed = sum(q.passed_tests_count for q in question_results.values())
        total_tests_run = sum(q.total_tests_count for q in question_results.values())
        compilation_failures = sum(1 for q in question_results.values() if q.compiler_result and not q.compiler_result.success)
        runtime_failures = sum(
            1 for q in question_results.values()
            if any(t.status in (CheckStatus.RUNTIME_ERROR, CheckStatus.TIMEOUT) for t in q.test_results)
        )
        manual_review_flags: List[str] = []
        if renamed_files:
            manual_review_flags.append(f"Files had doubled extensions and were renamed to compile: {', '.join(renamed_files[:4])}"
                                       + (" ..." if len(renamed_files) > 4 else ""))
        for qid, q_res in question_results.items():
            if q_res.needs_manual_review:
                manual_review_flags.extend([f"{qid.upper()}: {r}" for r in q_res.manual_review_reasons])

        # Assemble student result
        student_result = StudentGradingResult(
            student_id=student_id,
            assignment_id=self.assignment.id,
            zip_path=str(submission_path) if is_zip else None,
            extracted_dir=str(extracted_dir),
            question_results=question_results,
            total_tests_passed=total_tests_passed,
            total_tests_run=total_tests_run,
            compilation_failures=compilation_failures,
            runtime_failures=runtime_failures,
            manual_review_recommended=bool(manual_review_flags),
            manual_review_reasons=manual_review_flags,
            timestamp=datetime.now().isoformat(),
            late=read_late_flag(submission_path.parent, student_id),
            ta_marks=prior_ta_marks,
            ta_marks_at=prior_ta_at
        )

        # Save machine-readable JSON and human-readable reports
        self.cache.save_result(student_result)
        self.reporter.save_student_report(student_result, self.assignment)

        # Clean up temporary binary files
        shutil.rmtree(scratch_bin_dir, ignore_errors=True)

        return student_result

    # ------------------------------------------------------------------ per question

    def _grade_question(
        self,
        student_id: str,
        question: QuestionConfig,
        mapped_files: List[Path],
        bin_dir: Path,
        all_source_files: Optional[List[Path]] = None,
        cached_result: Optional[QuestionGradingResult] = None,
        rerun_llm: bool = False
    ) -> QuestionGradingResult:
        """Evaluates Layer A, Layer B, and Layer C for a single question."""

        # If reusing objective execution data for LLM rerun
        if cached_result and rerun_llm and cached_result.found_source:
            source_content = self._read_source_files(mapped_files)
            cached_result.llm_review = self.llm_reviewer.review_question_submission(
                question=question,
                source_code=source_content,
                compiler_result=cached_result.compiler_result or CompilerResult(success=False, returncode=-1),
                test_evaluations=cached_result.test_results,
                static_results=cached_result.static_results,
                marking=self.assignment.marking
            )
            return cached_result

        if not mapped_files:
            return QuestionGradingResult(
                question_id=question.id,
                question_name=question.name,
                language=question.language,
                source_files=[],
                found_source=False,
                needs_manual_review=True,
                manual_review_reasons=["Source file not found in submission."]
            )

        source_code = self._read_source_files(mapped_files)
        detected_lang = detect_language(mapped_files)

        # Classify: implementation files (no main) are linked with the TA driver; the student's own
        # main() files are only used for the direct run.
        code_files = [f for f in mapped_files if f.suffix.lower() in CODE_EXTS]
        library_files = [f for f in code_files if not has_main(read_source(f))]

        # This question's own folders come first: other task folders often hold same-named headers
        # (header.h, declarations.h), and the first -I directory that has the file wins.
        include_dirs: List[Path] = []
        for f in list(mapped_files) + sorted(all_source_files or []):
            d = f.parent.resolve()
            if d not in include_dirs:
                include_dirs.append(d)
        include_flags: List[str] = []
        for idir in include_dirs:
            include_flags += ["-I", str(idir)]

        notes: List[str] = []
        direct_res: Optional[CompilerResult] = None
        direct_evals: List[TestEvaluation] = []
        driver_res: Optional[CompilerResult] = None
        driver_evals: List[TestEvaluation] = []
        driver_note: Optional[str] = None

        # --- Layer A (1): the student's own program, fed each test's stdin ---
        exe_file = bin_dir / f"{question.id}.exe"
        direct_res, direct_evals = self._build_and_run(
            files=mapped_files,
            flags=list(question.compiler_flags or []),
            language=question.language,
            student_id=student_id,
            label=question.id,
            exe_file=exe_file,
            # driver_only: still compile the student's own program, but its main() is not fed test stdin
            question=question.model_copy(update={"test_cases": []}) if question.driver_only else question,
            stdin_for=lambda idx, tc: tc.input_data
        )

        # --- Layer A (2): LLM-synthesised driver that calls the student's functions directly ---
        # Only possible when the student's functions live outside their main() file; otherwise the
        # driver would be testing code the LLM wrote rather than the student's.
        direct_all_passed = bool(direct_evals) and all(t.passed for t in direct_evals)
        # Classes written entirely inside headers (bodies inline) are the student's code too: a driver
        # that #includes them tests their functions, even when every .cpp file has its own main()
        header_impls = [
            f for f in mapped_files
            if f.suffix.lower() in HEADER_EXTS
            and re.search(r"\)\s*(?:const\s*)?(?:override\s*)?(?:noexcept\s*)?\{", strip_comments(read_source(f)))
        ]
        if question.test_cases and (library_files or header_impls) and not direct_all_passed:
            driver_res, driver_evals, driver_note = self._run_with_driver(
                student_id=student_id,
                question=question,
                library_files=library_files,
                # Only this question's files: the whole submission wastes the prompt budget, and
                # same-named files from other tasks would overwrite each other in the prompt
                all_source_files=mapped_files or all_source_files,
                include_flags=include_flags,
                detected_lang=detected_lang,
                bin_dir=bin_dir,
                main_files=[f for f in code_files if f not in library_files]
            )

        # Pick the run that demonstrates the most: the student's own program wins ties
        driver_used = False
        compiler_res, test_evaluations = direct_res, direct_evals
        direct_passed = sum(1 for t in direct_evals if t.passed)
        driver_passed = sum(1 for t in driver_evals if t.passed)
        # (when the student's own main was not tested at all, e.g. driver_only questions, the driver's run is the result)
        if driver_res and driver_res.success and driver_evals and (
            driver_passed > direct_passed or not direct_evals or not (direct_res and direct_res.success)
        ):
            compiler_res, test_evaluations, driver_used = driver_res, driver_evals, True
            if direct_res and direct_res.success:
                notes.append(f"Tests ran via TA driver ({driver_passed} passed) - student's own main passed {direct_passed}.")
            else:
                notes.append("Student's own program did not build; tests ran via TA driver calling their functions.")

        if compiler_res is None:
            compiler_res = CompilerResult(success=False, returncode=-1, concise_error="Compilation failed or no executable generated.")

        passed_tests = sum(1 for t in test_evaluations if t.passed)

        # --- Layer B: Deterministic Static Analysis ---
        static_results = StaticAnalyzer.analyze_files(
            source_files=mapped_files,
            rules=question.static_checks,
            expected_language=question.language
        )

        # --- Layer C: Qualitative LLM Review ---
        llm_review = self._skipped_review() if not self.llm_review_enabled else self.llm_reviewer.review_question_submission(
            question=question,
            source_code=source_code,
            compiler_result=compiler_res,
            test_evaluations=test_evaluations,
            static_results=static_results,
            marking=self.assignment.marking
        )

        # Manual review recommendation logic
        reasons: List[str] = []
        if not compiler_res.success:
            reasons.append(f"Compilation failure: {compiler_res.concise_error}")
        elif question.test_cases and not test_evaluations:
            reasons.append(f"Tests could not run: {driver_note or 'no TA driver was available for this code'}. Check this part by hand.")

        blocked = [t for t in test_evaluations if any(b in (t.diff_summary or "") for b in BLOCKED_ERROR_TYPES)]
        if blocked:
            reasons.append("Windows blocked the compiled program from running (Smart App Control / antivirus) - results are not meaningful.")

        failed_tests = [t for t in test_evaluations if not t.passed]
        if failed_tests and not blocked:
            reasons.append(f"{len(failed_tests)}/{len(test_evaluations)} tests failed.")

        for s in static_results:
            if not s.passed:
                reasons.append(s.message)

        if llm_review and llm_review.manual_review and llm_review.manual_review_reason:
            reasons.append(f"LLM: {llm_review.manual_review_reason}")
        elif llm_review and llm_review.manual_review:
            reasons.append("LLM recommended manual review.")

        return QuestionGradingResult(
            question_id=question.id,
            question_name=question.name,
            language=question.language,
            source_files=[str(f) for f in mapped_files],
            found_source=True,
            compiler_result=compiler_res,
            test_results=test_evaluations,
            static_results=static_results,
            llm_review=llm_review,
            passed_tests_count=passed_tests,
            total_tests_count=len(test_evaluations),
            needs_manual_review=bool(reasons),
            manual_review_reasons=reasons + notes if reasons else [],
            driver_used=driver_used,
            driver_note=driver_note or (notes[0] if notes else None),
            execution_backend=compiler_res.backend if compiler_res else None
        )

    def _build_and_run(
        self,
        files: List[Path],
        flags: List[str],
        language: LanguageType,
        student_id: str,
        label: str,
        exe_file: Path,
        question: QuestionConfig,
        stdin_for: Callable
    ) -> Tuple[CompilerResult, List[TestEvaluation]]:
        """Compiles and runs every test case; rebuilds in WSL if Windows refuses to launch the .exe."""
        backend = SystemConfig.resolve_exec_backend()
        compiler_res = self.compiler.compile(
            source_files=files, output_executable=exe_file, language=language,
            compiler_flags=flags, student_id=student_id, question_id=label, backend=backend
        )
        if not compiler_res.success:
            # Code that only builds with the Windows toolchain still deserves a run, and a missing
            # native compiler should not stop grading when WSL has one.
            alt = None
            if compiler_res.backend == "wsl" and SystemConfig.find_compiler(language)[0]:
                alt = "native"
            elif compiler_res.backend == "native" and "No suitable" in (compiler_res.concise_error or "") and SystemConfig.wsl_toolchain():
                alt = "wsl"
            if alt:
                alt_res = self.compiler.compile(
                    source_files=files, output_executable=exe_file, language=language,
                    compiler_flags=flags, student_id=student_id, question_id=label, backend=alt
                )
                if alt_res.success:
                    compiler_res = alt_res
            if not compiler_res.success:
                return compiler_res, []

        evaluations = self._run_tests(exe_file, question, stdin_for)

        if compiler_res.backend == "native" and SystemConfig.wsl_toolchain() and any(
            any(b in (t.diff_summary or "") for b in BLOCKED_ERROR_TYPES) for t in evaluations
        ):
            self._log(f"{student_id} · {label}: Windows blocked the .exe, rebuilding in WSL")
            wsl_res = self.compiler.compile(
                source_files=files, output_executable=exe_file, language=language,
                compiler_flags=flags, student_id=student_id, question_id=label, backend="wsl"
            )
            if wsl_res.success:
                compiler_res = wsl_res
                evaluations = self._run_tests(exe_file, question, stdin_for)

        return compiler_res, evaluations

    def _run_tests(self, exe_file: Path, question: QuestionConfig, stdin_for: Callable) -> List[TestEvaluation]:
        evaluations: List[TestEvaluation] = []
        if not question.test_cases or not exe_file.exists():
            return evaluations
        SandboxRunner.warm_up(exe_file)
        for idx, tc in enumerate(question.test_cases, 1):
            exec_res = SandboxRunner.run_executable(
                executable_path=exe_file,
                input_data=stdin_for(idx, tc),
                # The per-question timeout is a floor so raising it in the UI actually takes effect
                timeout_sec=max(tc.timeout_sec or 0, question.timeout_sec or 0, 0.5)
            )
            evaluation = Tester.evaluate(tc, exec_res)
            evaluation.input_data = tc.input_data
            evaluations.append(evaluation)
        return evaluations

    def _run_with_driver(
        self,
        student_id: str,
        question: QuestionConfig,
        library_files: List[Path],
        all_source_files: List[Path],
        include_flags: List[str],
        detected_lang: LanguageType,
        bin_dir: Path,
        main_files: Optional[List[Path]] = None
    ) -> Tuple[Optional[CompilerResult], List[TestEvaluation], Optional[str]]:
        """Builds the student's implementation files with a TA test driver that owns main()."""
        lang_ext = "cpp" if (detected_lang == LanguageType.CPP or question.language == LanguageType.CPP) else "c"
        driver_dir = self.drivers_root / self.assignment.id
        driver_dir.mkdir(parents=True, exist_ok=True)

        # Reuse a cached driver (either extension) unless this is a forced re-check
        driver_file = driver_dir / f"{student_id}_{question.id}.{lang_ext}"
        driver_code: Optional[str] = None
        from_cache = False
        if not self._is_recheck:
            for candidate in (driver_file, driver_dir / f"{student_id}_{question.id}.{'c' if lang_ext == 'cpp' else 'cpp'}"):
                if candidate.exists():
                    raw = candidate.read_text(encoding="utf-8", errors="replace")
                    driver_code = GeminiReviewer.strip_stray_language_tag(raw)
                    if driver_code != raw:
                        candidate.write_text(driver_code, encoding="utf-8")  # repair old 'pp'-prefixed drivers
                    driver_file, from_cache = candidate, True
                    break

        def generate(feedback=None) -> Optional[str]:
            if not self.llm_reviewer.api_key:
                return None
            self._log(f"{student_id} · {question.id.upper()}: {'repairing' if feedback else 'generating'} TA test driver with the LLM")
            # Headers first: they carry the class/function API, and the prompt may truncate the last files
            ordered = sorted(all_source_files, key=lambda f: (f.suffix.lower() not in HEADER_EXTS, f.name.lower()))
            files_map = {f.name: read_source(f) for f in ordered}
            code = self.llm_reviewer.synthesize_test_driver(question, files_map, student_id, lang_ext=lang_ext, feedback=feedback)
            if code:
                driver_file.write_text(code, encoding="utf-8")
            return code

        if not driver_code:
            driver_code = generate()
        if not driver_code:
            why = self.llm_reviewer.last_driver_error if self.llm_reviewer.api_key else "no Gemini API key"
            return None, [], f"TA driver not available ({why})."

        # A driver that prints the expected answers itself would pass any student: regenerate it once, else reject
        cheats = hardcoded_answers(driver_code, question)
        if cheats and self.llm_reviewer.api_key:
            self._log(f"{student_id} · {question.id.upper()}: driver hard-coded expected answers, regenerating")
            regenerated = generate(feedback=(driver_code, cheat_feedback(cheats)))
            if regenerated:
                driver_code = regenerated
                cheats = hardcoded_answers(driver_code, question)
        if cheats:
            return None, [], f"TA driver rejected: it printed expected answers itself ({cheats[0]})."

        exe_file = bin_dir / f"{question.id}_driver.exe"

        # Copies of the student's main files with main() renamed, for functions they defined in main.cpp
        main_copies: List[Path] = []
        for f in main_files or []:
            text = re.sub(r"\b(int|void)\s+main\s*\(", r"\1 __ta_student_main_unused(", read_source(f), count=1)
            copy = bin_dir / "_student_mains" / question.id / f.name
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_text(text, encoding="utf-8")
            main_copies.append(copy)
        use_main_copies = False
        open_access: List[str] = []

        def build():
            return self._build_and_run(
                files=library_files + (main_copies if use_main_copies else []) + [driver_file],
                flags=list(question.compiler_flags or []) + include_flags + open_access,
                language=LanguageType.CPP if lang_ext == "cpp" else question.language,
                student_id=student_id,
                label=f"{question.id}_driver",
                exe_file=exe_file,
                question=question,
                stdin_for=lambda idx, tc: str(idx)
            )

        res, evals = build()
        if not res.success and main_copies and "undefined reference" in (res.stderr or ""):
            # The student defined some functions next to their own main(): link those in as well
            use_main_copies = True
            res, evals = build()
        if not res.success and lang_ext == "cpp" and private_access_error(res.stderr):
            # The driver sets members the class keeps private: open access instead of asking the LLM again
            open_access = open_access_flags(bin_dir)
            res, evals = build()
        if not res.success and self.llm_reviewer.api_key:
            # One repair round: show the LLM its driver and the compiler errors
            previous = driver_file.read_text(encoding="utf-8", errors="replace") if driver_file.exists() else ""
            if generate(feedback=(previous, res.concise_error or res.stderr or "")):
                res, evals = build()
                if not res.success and main_copies and not use_main_copies and "undefined reference" in (res.stderr or ""):
                    use_main_copies = True
                    res, evals = build()
                if not res.success and lang_ext == "cpp" and not open_access and private_access_error(res.stderr):
                    open_access = open_access_flags(bin_dir)
                    res, evals = build()
        if driver_file.exists():
            cheats = hardcoded_answers(driver_file.read_text(encoding="utf-8", errors="replace"), question)
            if cheats:
                return None, [], f"TA driver rejected: it printed expected answers itself ({cheats[0]})."
        note = None if res.success else f"TA driver failed to compile: {res.concise_error}"
        return res, evals, note

    @staticmethod
    def _skipped_review():
        from grader.models import LLMReview
        return LLMReview(error="SKIPPED", issues=["LLM review was turned off for this batch."])

    def reevaluate_results(self) -> List[Tuple[str, int, int]]:
        """
        Re-checks stored program outputs against the current comparison rules (e.g. fractions that are
        correct but not simplified) without recompiling or calling an LLM. Returns
        [(student_id, tests that changed to pass, total tests)].
        """
        from grader.models import ExecutionResult
        changes = []
        tests_by_q = {q.id: {t.id: t for t in q.test_cases} for q in self.assignment.questions}
        for f in sorted(self.cache.results_dir.glob("*.json")):
            res = self.cache.load_result(f.stem)
            if res is None or (res.assignment_id and res.assignment_id != self.assignment.id):
                continue
            flipped = total = 0
            for qid, q in res.question_results.items():
                for i, t in enumerate(q.test_results):
                    total += 1
                    tc = tests_by_q.get(qid, {}).get(t.test_id)
                    if tc is None or t.status != CheckStatus.FAIL:
                        continue  # only plain output mismatches can change; crashes/timeouts stay as they are
                    ex = ExecutionResult(status=CheckStatus.PASS, returncode=t.returncode, stdout=t.actual_output,
                                         stderr=t.stderr, duration_sec=t.execution_time_sec)
                    new = Tester.evaluate(tc, ex)
                    if new.passed:
                        new.input_data = t.input_data
                        q.test_results[i] = new
                        flipped += 1
                q.passed_tests_count = sum(1 for t in q.test_results if t.passed)
                failed = len(q.test_results) - q.passed_tests_count
                q.manual_review_reasons = [r for r in q.manual_review_reasons if not r.endswith("tests failed.")]
                if failed:
                    q.manual_review_reasons.insert(0, f"{failed}/{len(q.test_results)} tests failed.")
                q.needs_manual_review = bool(q.manual_review_reasons)
            if flipped:
                res.total_tests_passed = sum(q.passed_tests_count for q in res.question_results.values())
                res.manual_review_reasons = [f"{qid.upper()}: {r}" for qid, q in res.question_results.items() for r in q.manual_review_reasons]
                res.manual_review_recommended = bool(res.manual_review_reasons)
                self.cache.save_result(res)
                self.reporter.save_student_report(res, self.assignment)
            changes.append((res.student_id, flipped, total))
        return changes

    def _read_source_files(self, files: List[Path]) -> str:
        pieces = []
        for f in files:
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as sf:
                    pieces.append(f"// File: {f.name}\n" + sf.read())
            except Exception:
                pass
        return "\n\n".join(pieces)


def main():
    parser = argparse.ArgumentParser(
        description="C/C++ TA Autograder — Safe, TA-assisted code inspection pipeline."
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # Command: grade (default if path supplied)
    grade_parser = subparsers.add_parser("grade", help="Grade submissions directory")
    grade_parser.add_argument("submissions", help="Path to submissions folder containing .zip archives")
    grade_parser.add_argument("--assignment", "-a", default=None, help="Assignment pack ID or folder (default: first pack in assignments/)")
    grade_parser.add_argument("--backend", choices=["auto", "native", "wsl"], default=None, help="Where to compile/run student code (default: EXEC_BACKEND or auto)")
    grade_parser.add_argument("--student", "-s", default=None, help="Filter by student ID")
    grade_parser.add_argument("--question", "-q", default=None, help="Filter by question ID")
    grade_parser.add_argument("--rerun", action="store_true", help="Force rerun from scratch, ignoring cache")
    grade_parser.add_argument("--rerun-llm", action="store_true", help="Rerun only Layer C LLM qualitative review using cached test results")
    grade_parser.add_argument("--output-dir", "-o", default="reports", help="Reports output directory")
    grade_parser.add_argument("--results-dir", "-r", default="results", help="Intermediate JSON results directory")
    grade_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose console output")

    # Command: list-assignments
    subparsers.add_parser("list-assignments", help="List all available weekly assignment configurations")

    # Command: new-assignment
    new_assign_parser = subparsers.add_parser("new-assignment", help="Scaffold a new assignment pack")
    new_assign_parser.add_argument("--name", "-n", required=True, help="Assignment ID/folder name (e.g. week2_linked_lists)")
    new_assign_parser.add_argument("--title", "-t", default=None, help="Human-readable assignment title")
    new_assign_parser.add_argument("--desc", "-d", default=None, help="Assignment description")
    new_assign_parser.add_argument("--format", "-f", default="json", choices=["json", "yaml"], help="Config file format")

    # Command: add-question
    add_q_parser = subparsers.add_parser("add-question", help="Add a new question to an existing assignment")
    add_q_parser.add_argument("--assignment", "-a", required=True, help="Target assignment ID")
    add_q_parser.add_argument("--id", required=True, help="Question ID (e.g. q1, q2)")
    add_q_parser.add_argument("--name", required=True, help="Question Name")
    add_q_parser.add_argument("--desc", required=True, help="Problem description")
    add_q_parser.add_argument("--type", "-t", default="c", choices=["c", "cpp"], help="Language type")

    # Command: generate-tests
    gen_test_parser = subparsers.add_parser("generate-tests", help="Generate comprehensive test cases using Gemini and freeze them in config")
    gen_test_parser.add_argument("--assignment", "-a", required=True, help="Target assignment ID")
    gen_test_parser.add_argument("--question", "-q", required=True, help="Question ID to generate tests for")
    gen_test_parser.add_argument("--problem", "-p", required=True, help="Detailed problem description / specification")
    gen_test_parser.add_argument("--count", "-c", type=int, default=4, help="Number of test cases to generate")

    # Command: gui
    subparsers.add_parser("gui", help="Launch interactive Web GUI dashboard in browser")

    # Command: reevaluate
    reeval_parser = subparsers.add_parser("reevaluate", help="Re-check stored outputs with the current comparison rules (no recompiling, no LLM)")
    reeval_parser.add_argument("--assignment", "-a", required=True, help="Assignment pack ID")
    reeval_parser.add_argument("--results-dir", "-r", required=True, help="Results folder to update")
    reeval_parser.add_argument("--output-dir", "-o", required=True, help="Reports folder to regenerate")

    # Handle direct invocation like `python grader.py submissions/`
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-") and sys.argv[1] not in ["grade", "gui", "list-assignments", "new-assignment", "add-question", "generate-tests", "reevaluate"]:
        # Insert 'grade' subcommand
        sys.argv.insert(1, "grade")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "reevaluate":
        pipeline = AutograderPipeline(assignment_id_or_path=args.assignment, output_dir=args.output_dir, results_dir=args.results_dir)
        changes = pipeline.reevaluate_results()
        for sid, flipped, total in changes:
            if flipped:
                print(f"  {sid}: {flipped} test(s) now pass (of {total})")
        print(f"Re-evaluated {len(changes)} results; {sum(1 for _, f, _ in changes if f)} changed.")
        sys.exit(0)

    if args.command == "gui":
        from gui import main as start_gui
        start_gui()
        sys.exit(0)

    if args.command == "list-assignments":
        assignments = AssignmentManager.list_assignments()
        print(f"\n{Fore.CYAN}{Style.BRIGHT}Available Weekly Assignments:{Style.RESET_ALL}")
        if not assignments:
            print("  No assignments found in 'assignments/' directory.")
        for a in assignments:
            print(f"  • {Fore.WHITE}{Style.BRIGHT}{a['id']}{Style.RESET_ALL} — {a.get('title', a['id'])}")
            if "questions" in a:
                print(f"    Questions ({a['questions_count']}): {', '.join(a['questions'])}")
            print(f"    Path: {a['path']}")
        print()

    elif args.command == "new-assignment":
        target = AssignmentManager.create_new_assignment(
            name=args.name,
            title=args.title,
            description=args.desc,
            format_type=args.format
        )
        print(f"\n{Fore.GREEN}Successfully created new assignment pack:{Style.RESET_ALL} {target}")
        print(f"Edit {target / f'assignment.{args.format}'} or use 'python grader.py add-question' to configure questions.\n")

    elif args.command == "add-question":
        new_q = AssignmentManager.add_question_to_assignment(
            assignment_id=args.assignment,
            question_id=args.id,
            name=args.name,
            description=args.desc,
            language=args.type
        )
        print(f"\n{Fore.GREEN}Successfully added {new_q.id.upper()} to assignment '{args.assignment}'.{Style.RESET_ALL}\n")

    elif args.command == "generate-tests":
        print(f"\n{Fore.CYAN}Invoking Gemini to generate test cases for '{args.question}' in assignment '{args.assignment}'...{Style.RESET_ALL}")
        gen = TestCaseGenerator()
        tests = gen.generate_and_save_tests(
            assignment_id_or_path=args.assignment,
            question_id=args.question,
            problem_description=args.problem,
            num_tests=args.count
        )
        print(f"{Fore.GREEN}Generated and saved {len(tests)} test cases to assignment configuration!{Style.RESET_ALL}")
        for t in tests:
            print(f"  - [{t.id}] {t.description} (mode: {t.comparison_mode.value}, edge: {t.is_edge_case})")
        print(f"\nThese test cases are now frozen in configuration and will evaluate all students equally.\n")

    elif args.command == "grade":
        if args.backend:
            os.environ["EXEC_BACKEND"] = args.backend
        if not args.assignment:
            available = AssignmentManager.list_assignments()
            if not available:
                print(f"{Fore.RED}No assignment packs found. Create one with 'python grader.py new-assignment'.{Style.RESET_ALL}")
                sys.exit(1)
            args.assignment = available[0]["id"]
            print(f"No --assignment given; using '{args.assignment}'.")
        pipeline = AutograderPipeline(
            assignment_id_or_path=args.assignment,
            output_dir=args.output_dir,
            results_dir=args.results_dir,
            verbose=args.verbose
        )
        pipeline.grade_batch(
            submissions_path=args.submissions,
            filter_student=args.student,
            filter_question=args.question,
            rerun_all=args.rerun,
            rerun_llm_only=args.rerun_llm
        )


if __name__ == "__main__":
    main()
