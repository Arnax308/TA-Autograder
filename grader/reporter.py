"""
Reporter module for concise student reports and batch summaries.
Clearly distinguishes Automated Execution Facts, Static Analysis, and LLM Observations.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import colorama
from colorama import Fore, Style

from grader.marks import suggest_marks, format_marks_block
from grader.models import (
    AssignmentConfig,
    StudentGradingResult,
    QuestionGradingResult,
    BatchSummary,
    CheckStatus,
    QualitativeRating,
)

colorama.init(autoreset=True)


class Reporter:
    """Formats and writes concise 8-12 line student reports and batch grading summaries."""

    def __init__(self, reports_dir: str | Path = "reports"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_student_report(self, student_res: StudentGradingResult, assignment: Optional[AssignmentConfig] = None) -> str:
        """
        Generates the concise 8-12 line report for a student following the build specification.
        """
        lines = []
        lines.append(f"Student: {student_res.student_id}\n")

        # Question-by-question summary lines
        manual_review_qs = []
        notes = []
        readability_ratings = []
        naming_ratings = []

        if student_res.error_message:
            lines.append(f"Submission Error: {student_res.error_message}")
            lines.append("Manual review: YES (Archive/Extraction Error)")
            return "\n".join(lines)

        comp1_ratings = []
        comp2_statuses = []
        comp3_ratings = []
        comp4_ratings = []
        extra_comments_list = []

        for qid, qres in student_res.question_results.items():
            q_label = qid.upper()
            status_tag, short_reason = self._summarize_question_status(qres)
            
            # Format per-question line
            comp_summary_parts = []
            if qres.llm_review and qres.llm_review.components and not qres.llm_review.error:
                c = qres.llm_review.components
                comp1_ratings.append(c.comp1_style_conventions.rating.value)
                comp2_statuses.append(c.comp2_basic_tests.status.value)
                comp3_ratings.append(c.comp3_logic_edge_cases.rating.value)
                comp4_ratings.append(c.comp4_comments_submission.rating.value)
                
                comp_summary_parts.append(f"Comp 1: {c.comp1_style_conventions.rating.value}")
                comp_summary_parts.append(f"Comp 2: {c.comp2_basic_tests.status.value}")
                comp_summary_parts.append(f"Comp 3: {c.comp3_logic_edge_cases.rating.value}")
                comp_summary_parts.append(f"Comp 4: {c.comp4_comments_submission.rating.value}")

                for ec in c.extra_comments:
                    entry = f"{q_label}: {ec}"
                    if ec and entry not in extra_comments_list:
                        extra_comments_list.append(entry)

            if comp_summary_parts:
                lines.append(f"{q_label}: {status_tag} — ({', '.join(comp_summary_parts)})")
            else:
                lines.append(f"{q_label}: {status_tag} — {short_reason}")

            if qres.needs_manual_review:
                manual_review_qs.append(q_label)
                for r in qres.manual_review_reasons:
                    entry = f"{q_label}: {r}"
                    if entry not in notes:
                        notes.append(entry)

            if qres.llm_review and not qres.llm_review.error:
                if qres.llm_review.readability != QualitativeRating.NOT_EVALUATED:
                    readability_ratings.append(qres.llm_review.readability.value)
                if qres.llm_review.variable_naming != QualitativeRating.NOT_EVALUATED:
                    naming_ratings.append(qres.llm_review.variable_naming.value)
                for issue in qres.llm_review.issues:
                    entry = f"{q_label}: {issue}"
                    if issue and entry not in notes:
                        notes.append(entry)

        lines.append("")

        # 4-Component High-Level Summary for TA Excel entry
        lines.append(f"Comp 1 (Style/Naming): {self._aggregate_rating(comp1_ratings or naming_ratings)}")
        lines.append(f"Comp 2 (Basic Tests):  {self._aggregate_status(comp2_statuses)}")
        lines.append(f"Comp 3 (Logic/Edges):  {self._aggregate_rating(comp3_ratings or readability_ratings)}")
        lines.append(f"Comp 4 (Submission):   {self._aggregate_rating(comp4_ratings)}")
        lines.append("")

        if assignment is not None:
            lines.extend(format_marks_block(suggest_marks(student_res, assignment)))
            lines.append("")

        # Manual review line
        if manual_review_qs or student_res.manual_review_recommended:
            all_flags = sorted(set(manual_review_qs + [r.split(":")[0] for r in student_res.manual_review_reasons if ":" in r]))
            lines.append(f"Manual review: {', '.join(all_flags) if all_flags else 'YES'}")
        else:
            lines.append("Manual review: NONE")

        # Concise bullet points and Extra Comments
        all_notes = notes + extra_comments_list
        if all_notes:
            lines.append("\nExtra Comments & Notes:")
            for note in all_notes[:5]:
                lines.append(f"* {note}")

        return "\n".join(lines)

    def _aggregate_status(self, statuses: List[str]) -> str:
        if not statuses:
            return "N/A (not evaluated)"
        if any("FAIL" in s.upper() for s in statuses):
            return "FAIL"
        if any("UNCERTAIN" in s.upper() for s in statuses):
            return "UNCERTAIN"
        return "PASS"

    def save_student_report(self, student_res: StudentGradingResult, assignment: Optional[AssignmentConfig] = None) -> Path:
        report_text = self.generate_student_report(student_res, assignment)
        report_file = self.reports_dir / f"{student_res.student_id}.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report_text)
        return report_file

    def generate_detailed_breakdown(self, student_res: StudentGradingResult) -> str:
        """Generates full layer-by-layer breakdown separating Automated Facts vs LLM Observations."""
        lines = []
        lines.append(f"# Detailed Inspection Report: {student_res.student_id}\n")

        for qid, qres in student_res.question_results.items():
            lines.append(f"## {qres.question_name} ({qid.upper()})")

            # Layer A: Objective Automated Results
            lines.append("### Automated Execution (Layer A)")
            if not qres.found_source:
                lines.append("- Source Files: **MISSING / NOT SUBMITTED**")
                continue

            lines.append(f"- Source Files: {', '.join(qres.source_files)}")
            if qres.compiler_result:
                c_status = "PASS" if qres.compiler_result.success else "FAIL"
                lines.append(f"- Compilation: **{c_status}**")
                if qres.compiler_result.concise_error:
                    lines.append(f"  - Compiler Log: `{qres.compiler_result.concise_error}`")

            if qres.test_results:
                passed = sum(1 for t in qres.test_results if t.passed)
                lines.append(f"- Tests: **{passed}/{len(qres.test_results)} PASS**")
                for t in qres.test_results:
                    t_stat = "PASS" if t.passed else f"FAIL ({t.status.value})"
                    lines.append(f"  - [{t_stat}] {t.description}")
                    if not t.passed and t.diff_summary:
                        lines.append(f"    - Diff: {t.diff_summary}")

            # Layer B: Deterministic Static Analysis
            lines.append("### Static Structure Analysis (Layer B)")
            if qres.static_results:
                for s in qres.static_results:
                    s_stat = "PASS" if s.passed else s.status.value
                    lines.append(f"- [{s_stat}] {s.description}: {s.message}")
            else:
                lines.append("- No static rules evaluated.")

            # Layer C: Qualitative LLM Review & 4 Components
            lines.append("### LLM Qualitative Review (Layer C)")
            if qres.llm_review and not qres.llm_review.error:
                c = qres.llm_review.components
                if c:
                    lines.append(f"- **Comp 1 (Style & Naming)**: {c.comp1_style_conventions.rating.value} — {c.comp1_style_conventions.feedback}")
                    lines.append(f"- **Comp 2 (Basic Tests)**: {c.comp2_basic_tests.status.value} — {c.comp2_basic_tests.feedback}")
                    lines.append(f"- **Comp 3 (Logic & Border Cases)**: {c.comp3_logic_edge_cases.rating.value} — {c.comp3_logic_edge_cases.feedback}")
                    lines.append(f"- **Comp 4 (Comments & Submission)**: {c.comp4_comments_submission.rating.value} — {c.comp4_comments_submission.feedback}")
                    if c.extra_comments:
                        lines.append("- Extra Comments:")
                        for ec in c.extra_comments:
                            lines.append(f"  * {ec}")

                if qres.llm_review.issues:
                    lines.append("- Specific Observations:")
                    for iss in qres.llm_review.issues:
                        lines.append(f"  * {iss}")
                if qres.llm_review.manual_review:
                    lines.append(f"- Review Recommended: **YES** ({qres.llm_review.manual_review_reason})")
            else:
                err = qres.llm_review.error if qres.llm_review else "Not evaluated"
                lines.append(f"- LLM Review: *{err}*")

            lines.append("")

        return "\n".join(lines)

    def generate_and_save_batch_summary(self, results: List[StudentGradingResult], assignment_id: str) -> BatchSummary:
        """Saves batch markdown summary and returns BatchSummary model."""
        total = len(results)
        compile_fails = sum(1 for r in results if r.compilation_failures > 0)
        runtime_fails = sum(1 for r in results if r.runtime_failures > 0)
        manual_reviews = sum(1 for r in results if r.manual_review_recommended)
        successful = sum(1 for r in results if not r.error_message and r.compilation_failures == 0 and r.runtime_failures == 0)
        llm_fails = 0
        for r in results:
            for q in r.question_results.values():
                if q.llm_review and q.llm_review.error:
                    llm_fails += 1
                    break

        summary = BatchSummary(
            assignment_id=assignment_id,
            total_submissions=total,
            processed_count=total,
            successful_count=successful,
            compilation_failures=compile_fails,
            runtime_failures=runtime_fails,
            manual_review_count=manual_reviews,
            llm_failures=llm_fails,
            student_ids=[r.student_id for r in results]
        )

        # Write markdown summary table
        md_lines = []
        md_lines.append(f"# Batch Grading Summary: {assignment_id.upper()}\n")
        md_lines.append(f"| Metric | Count |")
        md_lines.append(f"| :--- | :--- |")
        md_lines.append(f"| Total Submissions | {total} |")
        md_lines.append(f"| Completely Clean Runs | {successful} |")
        md_lines.append(f"| Compilation Failures | {compile_fails} |")
        md_lines.append(f"| Runtime Crashes / Timeouts | {runtime_fails} |")
        md_lines.append(f"| Manual Review Recommended | {manual_reviews} |")
        md_lines.append(f"| LLM API Failures | {llm_fails} |\n")

        md_lines.append("## Student Results Overview\n")
        md_lines.append("| Student | Tests Passed | Compilation | Crashes | Manual Review |")
        md_lines.append("| :--- | :--- | :--- | :--- | :--- |")

        for r in results:
            t_str = f"{r.total_tests_passed}/{r.total_tests_run}"
            c_str = "FAIL" if r.compilation_failures > 0 else "OK"
            rt_str = "CRASH" if r.runtime_failures > 0 else "OK"
            mr_str = "**YES**" if r.manual_review_recommended else "None"
            md_lines.append(f"| {r.student_id} | {t_str} | {c_str} | {rt_str} | {mr_str} |")

        summary_file = self.reports_dir / "BATCH_SUMMARY.md"
        with open(summary_file, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

        return summary

    def print_batch_console_summary(self, summary: BatchSummary) -> None:
        """Prints a clean colored summary table to terminal."""
        print("\n" + "=" * 55)
        print(f"{Fore.CYAN}{Style.BRIGHT}   C/C++ TA AUTOGRADER — BATCH EVALUATION SUMMARY{Style.RESET_ALL}")
        print("=" * 55)
        print(f"  Assignment:                {Fore.WHITE}{summary.assignment_id}")
        print(f"  Processed Submissions:     {Fore.WHITE}{summary.processed_count}")
        print(f"  Fully Clean Runs:          {Fore.GREEN}{summary.successful_count}")
        print(f"  Compilation Failures:      {Fore.RED if summary.compilation_failures > 0 else Fore.GREEN}{summary.compilation_failures}")
        print(f"  Runtime Failures/Timeouts: {Fore.RED if summary.runtime_failures > 0 else Fore.GREEN}{summary.runtime_failures}")
        print(f"  Manual Review Recommended: {Fore.YELLOW if summary.manual_review_count > 0 else Fore.GREEN}{summary.manual_review_count}")
        print(f"  LLM Failures:              {Fore.RED if summary.llm_failures > 0 else Fore.GREEN}{summary.llm_failures}")
        print("-" * 55)
        print(f"  {Fore.GREEN}Reports saved to:          {self.reports_dir}")
        print("=" * 55 + "\n")

    def _summarize_question_status(self, qres: QuestionGradingResult) -> Tuple[str, str]:
        """Produces a short status label and 1-line reason for student report."""
        if not qres.found_source:
            return "MISSING", "No source code submitted."

        if qres.compiler_result and not qres.compiler_result.success:
            err = qres.compiler_result.concise_error or "Compilation failed."
            return "FAIL", f"Compilation error: {err[:60]}"

        # Check test results
        if qres.test_results:
            failed_tests = [t for t in qres.test_results if not t.passed]
            if failed_tests:
                t = failed_tests[0]
                passed = len(qres.test_results) - len(failed_tests)
                tag = "PARTIAL" if passed else "FAIL"
                if t.status == CheckStatus.TIMEOUT:
                    return tag, f"{passed}/{len(qres.test_results)} tests; timeout (possible infinite loop)."
                elif t.status == CheckStatus.RUNTIME_ERROR:
                    return tag, f"{passed}/{len(qres.test_results)} tests; runtime crash ({t.diff_summary[:50]})."
                else:
                    return tag, f"{passed}/{len(qres.test_results)} tests; output mismatch on {t.test_id}."

        # Check static check results
        failed_static = [s for s in qres.static_results if not s.passed]
        if failed_static:
            s = failed_static[0]
            if s.status == CheckStatus.PARTIAL:
                return "PARTIAL", s.message
            return "FAIL", s.message

        # Check LLM review notes if available
        if qres.llm_review and qres.llm_review.requirements:
            failed_reqs = [r for r in qres.llm_review.requirements if r.status == CheckStatus.FAIL]
            if failed_reqs:
                return "PARTIAL", failed_reqs[0].reason or "Requirement not fully demonstrated."

        return "PASS", "All tests and structural requirements satisfied."

    def _aggregate_rating(self, ratings: List[str]) -> str:
        ratings = [r for r in ratings if r and r != "NOT_EVALUATED"]
        if not ratings:
            return "N/A (not evaluated)"
        if "POOR" in ratings:
            return "POOR"
        if "FAIR" in ratings:
            return "FAIR"
        return "GOOD"
