"""
Suggested marks for the lab rubric (Comp 1-4), always for the TA to confirm.

Objective evidence wins wherever it exists:
- Comp 2 (basic tests) comes only from the automated non-edge tests.
- Comp 3 (logic & border cases) blends edge-case test results with Gemini's logic score.
- Comp 4 (timely submission & comments) uses Gemini's comment score minus a late penalty
  taken from the Classroom download manifest.
- Comp 1 (style) is Gemini's anchored score.
Missing or failed Gemini reviews leave a component empty and flagged instead of guessing.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from grader.models import (
    AssignmentConfig, ComponentMark, MarkSuggestion, MarkingScheme, QualitativeRating, StudentGradingResult,
)

# Older reviews only carry a rating; map it onto the score scale the same way for every student
RATING_FRACTION = {QualitativeRating.GOOD: 1.0, QualitativeRating.FAIR: 2 / 3, QualitativeRating.POOR: 1 / 3}

LABELS = {
    "comp1": "Comp 1 · Style & naming",
    "comp2": "Comp 2 · Basic tests",
    "comp3": "Comp 3 · Logic & border cases",
    "comp4": "Comp 4 · Timely & comments",
}


def _half(x: float) -> float:
    return round(x * 2) / 2


def _half_up(x: float) -> float:
    """Round UP to the next half mark (tiny float noise ignored)."""
    return math.ceil(x * 2 - 1e-9) / 2


def _fmt(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:g}"


def _llm_fraction(result: StudentGradingResult, assignment: AssignmentConfig, attr: str) -> Tuple[Optional[float], List[str]]:
    """Average Gemini score (as a fraction of max) across questions; missing questions count as 0."""
    fractions, notes = [], []
    for q in assignment.questions:
        qres = result.question_results.get(q.id)
        if qres is None or not qres.found_source:
            fractions.append(0.0)
            continue
        review = qres.llm_review
        if review is None or review.error or not review.components:
            if not (review and review.error in ("SKIPPED", "NO_API_KEY")):
                notes.append(q.id.upper())
            continue
        comp = getattr(review.components, attr)
        if comp.score is not None and comp.max_score:
            fractions.append(max(0.0, min(1.0, comp.score / comp.max_score)))
        elif comp.rating in RATING_FRACTION:
            fractions.append(RATING_FRACTION[comp.rating])
        else:
            notes.append(q.id.upper())
    # Questions Gemini could not review are left out of the average (and flagged) rather than blanking the mark
    if not any(f > 0 for f in fractions) and notes:
        return None, notes
    if not fractions:
        return None, notes
    return sum(fractions) / len(fractions), notes


def _tests_not_run(q, qres) -> bool:
    return bool(q.test_cases and qres and qres.found_source and qres.compiler_result
                and qres.compiler_result.success and not qres.test_results)


def _test_fraction(result: StudentGradingResult, assignment: AssignmentConfig, edge: bool) -> Tuple[Optional[float], int, int]:
    """
    Share of the configured tests of one kind (normal or edge) the student passed, over every
    question. Tests that never ran (missing file, build failure) count as failed.
    Falls back to all tests when the pack has none of that kind.
    """
    def collect(kind: Optional[bool]) -> Tuple[int, int]:
        total = passed = 0
        for q in assignment.questions:
            configured = [t for t in q.test_cases if kind is None or t.is_edge_case == kind]
            qres = result.question_results.get(q.id)
            if _tests_not_run(q, qres):
                continue  # the student's code built but our TA driver did not: not the student's fault
            total += len(configured)
            if qres:
                ids = {t.id for t in configured}
                passed += sum(1 for t in qres.test_results if t.passed and t.test_id in ids)
        return passed, total

    passed, total = collect(edge)
    if total == 0:
        passed, total = collect(None)
    if total == 0:
        return None, 0, 0
    return passed / total, passed, total


def _student_metrics(result: StudentGradingResult):
    from grader.code_metrics import measure
    files = sorted({Path(p) for q in result.question_results.values() for p in q.source_files})
    return measure(files) if files else None


def _static_fraction(result: StudentGradingResult, assignment: AssignmentConfig) -> Tuple[Optional[float], float, int]:
    """Share of the assignment's concept checks (constructors, overloading, inheritance, ...) satisfied."""
    credit = {"PASS": 1.0, "PARTIAL": 0.5, "UNCERTAIN": 0.5}
    total, got = 0, 0.0
    for q in assignment.questions:
        if not q.static_checks:
            continue
        ids = {r.rule_id for r in q.static_checks}
        total += len(ids)
        qres = result.question_results.get(q.id)
        if qres and qres.found_source:
            got += sum(credit.get(r.status.value, 0.0) for r in qres.static_results if r.rule_id in ids)
    if total == 0:
        return None, 0.0, 0
    return got / total, got, total


def read_late_flag(submissions_dir: Optional[str | Path], student_id: str) -> Optional[bool]:
    """Late flag recorded by the Classroom download for this student, or None if unknown."""
    if not submissions_dir:
        return None
    manifest = Path(submissions_dir) / ".classroom_sync.json"
    try:
        info = json.loads(manifest.read_text(encoding="utf-8")).get("students", {}).get(student_id)
    except (OSError, ValueError):
        return None
    return bool(info.get("late")) if info else None


def suggest_marks(result: StudentGradingResult, assignment: AssignmentConfig, late: Optional[bool] = None) -> MarkSuggestion:
    m: MarkingScheme = assignment.marking or MarkingScheme()
    rnd = _half_up if m.lenient else _half
    pen3 = pen4 = 0.0
    raw3 = raw4 = None  # marks before the fraction / late penalties
    late = result.late if late is None else late
    flags: List[str] = []
    comps: List[ComponentMark] = []

    if result.error_message:
        flags.append(f"Submission could not be opened ({result.error_message}); all marks need a manual check.")

    failed_builds = [qid.upper() for qid, q in result.question_results.items() if q.found_source and q.compiler_result and not q.compiler_result.success]
    missing = [q.id.upper() for q in assignment.questions if not (result.question_results.get(q.id) and result.question_results[q.id].found_source)]
    if missing:
        flags.append(f"No source found for {', '.join(missing)}: counted as 0 everywhere.")
    if failed_builds:
        flags.append(f"{', '.join(failed_builds)} did not compile, so its tests count as failed. Check whether it is a small fixable error.")
    not_run = [q.id.upper() for q in assignment.questions if _tests_not_run(q, result.question_results.get(q.id))]
    if not_run:
        flags.append(f"Tests for {', '.join(not_run)} could not run (the TA driver could not be built for this student's code); they are left out of Comp 2/3. Check that part by hand.")
    if any(q.driver_used for q in result.question_results.values()):
        flags.append("Some tests ran through the TA driver (student's own main did not match the test format).")

    metrics = _student_metrics(result)

    # Comp 1: style (LLM review when available, otherwise deterministic code metrics)
    frac1, gaps1 = _llm_fraction(result, assignment, "comp1_style_conventions")
    if frac1 is not None:
        s1, src1 = rnd(frac1 * m.comp1_max), "LLM review"
        reason1 = "LLM style score" + (" (averaged over questions)" if len(assignment.questions) > 1 else "") + (f"; {', '.join(gaps1)} not reviewed" if gaps1 else "")
    elif metrics:
        s1, src1, reason1 = rnd(metrics.style_fraction() * m.comp1_max), "Code metrics", metrics.style_reason()
    else:
        s1, src1, reason1 = None, "", "No review and no readable source; mark it yourself."
    comps.append(ComponentMark(key="comp1", label=LABELS["comp1"], max_marks=m.comp1_max, suggested=s1, source=src1, reason=reason1))

    # Comp 2: basic tests (objective only)
    frac2, p2, t2 = _test_fraction(result, assignment, edge=False)
    comps.append(ComponentMark(
        key="comp2", label=LABELS["comp2"], max_marks=m.comp2_max,
        suggested=None if frac2 is None else rnd(frac2 * m.comp2_max), source="Tests",
        reason=f"{p2}/{t2} normal tests passed" if frac2 is not None else "This assignment has no test cases; mark it yourself.",
    ))

    # Comp 3: edge-case tests + (LLM logic review and/or the assignment's concept checks)
    frac3_tests, p3, t3 = _test_fraction(result, assignment, edge=True)
    frac3_llm, gaps3 = _llm_fraction(result, assignment, "comp3_logic_edge_cases")
    frac3_static, ok3, n3 = _static_fraction(result, assignment)
    judged = [f for f in (frac3_llm, frac3_static) if f is not None]
    frac3_judged = sum(judged) / len(judged) if judged else None
    judged_label = " + ".join(x for x, f in (("LLM logic review", frac3_llm), (f"{_fmt(ok3)}/{n3} concept checks", frac3_static)) if f is not None)
    w = max(0.0, min(1.0, m.comp3_test_weight))
    if frac3_tests is not None and frac3_judged is not None:
        s3 = rnd(m.comp3_max * (w * frac3_tests + (1 - w) * frac3_judged))
        reason3 = f"{p3}/{t3} edge-case tests ({_fmt(rnd(m.comp3_max * w * frac3_tests))}) + {judged_label} ({_fmt(rnd(m.comp3_max * (1 - w) * frac3_judged))})"
    elif frac3_judged is not None:
        s3, reason3 = rnd(m.comp3_max * frac3_judged), f"{judged_label} (no tests for this part)"
    elif frac3_tests is not None:
        s3, reason3 = rnd(m.comp3_max * frac3_tests), f"{p3}/{t3} edge-case tests only (no review or concept checks)"
    else:
        s3, reason3 = None, "No tests, review or concept checks; mark it yourself."
    unsimplified = [qid.upper() for qid, q in result.question_results.items() if any(t.unsimplified for t in q.test_results)]
    if s3 is not None and unsimplified and m.unsimplified_penalty:
        raw3, pen3 = s3, m.unsimplified_penalty
        s3 = max(0.0, s3 - m.unsimplified_penalty)
        reason3 += f", −{_fmt(m.unsimplified_penalty)} fractions not simplified ({', '.join(unsimplified)})"
    comps.append(ComponentMark(key="comp3", label=LABELS["comp3"], max_marks=m.comp3_max, suggested=s3, source="Tests + checks", reason=reason3))

    # Comp 4: comments (LLM review or code metrics) minus late penalty (Classroom)
    frac4, gaps4 = _llm_fraction(result, assignment, "comp4_comments_submission")
    if frac4 is not None:
        s4, reason4 = rnd(frac4 * m.comp4_max), None
        reason4 = f"LLM comment score {_fmt(s4)}"
    elif metrics:
        s4 = rnd(metrics.comment_fraction() * m.comp4_max)
        reason4 = f"{_fmt(s4)} from {metrics.comment_reason()}"
    else:
        s4, reason4 = None, "No review and no readable source; mark it yourself."
    if s4 is not None:
        if late:
            raw4, pen4 = s4, m.late_penalty
            s4 = max(0.0, s4 - m.late_penalty)
            reason4 += f", −{_fmt(m.late_penalty)} late submission"
        elif late is False:
            reason4 += ", submitted on time"
        else:
            reason4 += "; on-time status unknown (not downloaded via Classroom)"
    comps.append(ComponentMark(key="comp4", label=LABELS["comp4"], max_marks=m.comp4_max, suggested=s4,
                               source="Comments + Classroom", reason=reason4))
    if late:
        flags.append("Submitted late (from Classroom).")
    if unsimplified:
        flags.append(f"Correct answers but fractions not simplified in {', '.join(unsimplified)} (−{_fmt(m.unsimplified_penalty)} in Comp 3).")

    unreviewed = sorted(set(gaps1) | set(gaps3) | set(gaps4))
    reviewed_any = frac1 is not None or frac3_llm is not None or frac4 is not None
    if unreviewed and reviewed_any:
        flags.append(f"The LLM could not review {', '.join(unreviewed)}; Comp 1/3/4 use the other questions only. Re-check to retry.")

    # Lenient marking: anyone who submitted code keeps a base for the attempt; penalties still bite afterwards
    attempted = any(q.found_source for q in result.question_results.values())
    if m.lenient and attempted:
        rules = {"comp1": ("soft", m.style_floor, 0.0, None), "comp2": ("min", m.tests_floor, 0.0, None),
                 "comp3": ("min", m.logic_floor, pen3, raw3), "comp4": ("soft", m.comments_floor, pen4, raw4)}
        for c in comps:
            if c.suggested is None or c.key not in rules:
                continue
            kind, floor, pen, before = rules[c.key]
            raw = c.suggested if before is None else before  # lift the pre-penalty mark, then take the penalty again
            lifted = floor + (c.max_marks - floor) * raw / c.max_marks if kind == "soft" else max(raw, floor)
            new = max(0.0, min(c.max_marks, _half_up(lifted)) - pen)
            if new > c.suggested:
                c.reason = (c.reason or "") + f"; lenient marking {_fmt(c.suggested)} → {_fmt(new)}"
                c.suggested = new

    # TA-confirmed marks override suggestions
    for c in comps:
        if c.key in result.ta_marks:
            c.final, c.confirmed = result.ta_marks[c.key], True
        else:
            c.final = c.suggested

    def total(values):
        return None if any(v is None for v in values) else _half(sum(values))

    return MarkSuggestion(
        components=comps,
        total_suggested=total([c.suggested for c in comps]),
        total_final=total([c.final for c in comps]),
        total_max=sum(c.max_marks for c in comps),
        all_confirmed=all(c.confirmed for c in comps),
        flags=flags,
    )


def format_marks_block(s: MarkSuggestion) -> List[str]:
    """Lines for the Markdown report."""
    lines = ["Suggested marks (TA to confirm):"]
    for c in s.components:
        mark = _fmt(c.final)
        tag = " (TA)" if c.confirmed else ""
        lines.append(f"  {c.label}: {mark}/{_fmt(c.max_marks)}{tag} - {c.reason}")
    lines.append(f"  Total: {_fmt(s.total_final)}/{_fmt(s.total_max)}")
    return lines
