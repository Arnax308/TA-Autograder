"""Suggested marks: objective parts from tests, Gemini parts anchored, TA marks override."""

import json

from grader.llm import GeminiReviewer
from grader.marks import suggest_marks, read_late_flag
from grader.models import (
    AssignmentConfig, ComponentDetail, EvaluationComponents, LLMReview, MarkingScheme, QualitativeRating,
    QuestionConfig, QuestionGradingResult, StudentGradingResult, TestCase, TestEvaluation, CheckStatus,
)


def _assignment(edge=True, lenient=False):
    # Most tests pin the strict rubric arithmetic; test_lenient_marking covers the lenient mode (the default)
    tests = [TestCase(id="n1"), TestCase(id="n2"), TestCase(id="e1", is_edge_case=edge), TestCase(id="e2", is_edge_case=edge)]
    return AssignmentConfig(id="w", title="W", questions=[QuestionConfig(id="q1", name="Q1", test_cases=tests)],
                            marking=MarkingScheme(lenient=lenient))


def test_lenient_marking_lifts_attempts_but_keeps_penalties():
    r = _result(set(), _review(0, 0, 0), late=True)
    r.question_results["q1"].found_source = True
    s = suggest_marks(r, _assignment(lenient=True))
    c = {x.key: x.suggested for x in s.components}
    assert c["comp1"] == 1.5           # soft floor for style
    assert c["comp2"] == 1.0           # attempt floor even with 0 tests passed
    assert c["comp3"] == 2.0           # attempt floor for logic
    assert c["comp4"] == 0.5           # comments floor 1.5, then the -1 late penalty still applies
    full = suggest_marks(_result({"n1", "n2", "e1"}, _review(2.5, 4, 3)), _assignment(lenient=True))
    f = {x.key: x.suggested for x in full.components}
    assert f["comp1"] == 3.0 and f["comp4"] == 3.0   # 2.5 -> 1.5 + 1.5 * 2.5/3 = 2.75 -> rounded up to 3
    nothing = _result(set(), _review(0, 0, 0))
    nothing.question_results["q1"].found_source = False
    no_code = suggest_marks(nothing, _assignment(lenient=True))
    assert {x.key: x.suggested for x in no_code.components}["comp2"] == 0.0  # nothing submitted: no floor


def _review(c1, c3, c4, error=None):
    def comp(score, mx):
        return ComponentDetail(score=score, max_score=mx, rating=QualitativeRating.FAIR)
    return LLMReview(error=error, components=EvaluationComponents(
        comp1_style_conventions=comp(c1, 3), comp3_logic_edge_cases=comp(c3, 6), comp4_comments_submission=comp(c4, 3)))


def _result(passed_ids, review, late=None, ta=None):
    evals = [TestEvaluation(test_id=t, description="", passed=t in passed_ids, status=CheckStatus.PASS if t in passed_ids else CheckStatus.FAIL,
                            actual_output="", expected_output="") for t in ("n1", "n2", "e1", "e2")]
    q = QuestionGradingResult(question_id="q1", question_name="Q1", language="c", test_results=evals, llm_review=review)
    return StudentGradingResult(student_id="1089", question_results={"q1": q}, late=late, ta_marks=ta or {}, timestamp="x")


def test_hybrid_marks():
    s = suggest_marks(_result({"n1", "n2", "e1"}, _review(2.5, 4, 3)), _assignment())
    marks = {c.key: c.suggested for c in s.components}
    assert marks["comp1"] == 2.5                 # Gemini
    assert marks["comp2"] == 3.0                 # 2/2 normal tests
    assert marks["comp3"] == 3.5                 # 3 * (1/2 edge) + 3 * (4/6 gemini) = 1.5 + 2.0
    assert marks["comp4"] == 3.0
    assert s.total_suggested == 12.0 and s.total_max == 15


def test_late_penalty_and_ta_override():
    s = suggest_marks(_result({"n1", "n2", "e1", "e2"}, _review(3, 6, 3), late=True, ta={"comp1": 2.0}), _assignment())
    c = {x.key: x for x in s.components}
    assert c["comp4"].suggested == 2.0 and "late" in c["comp4"].reason
    assert c["comp1"].final == 2.0 and c["comp1"].confirmed and c["comp1"].suggested == 3.0
    assert s.total_final == 13.0 and not s.all_confirmed


def test_failed_review_leaves_component_for_the_ta():
    s = suggest_marks(_result({"n1"}, _review(3, 6, 3, error="HTTP 429")), _assignment())
    c = {x.key: x for x in s.components}
    assert c["comp1"].suggested is None and c["comp4"].suggested is None
    assert c["comp2"].suggested == 1.5  # tests still count
    assert s.total_suggested is None


def test_old_reviews_without_scores_use_ratings():
    review = LLMReview(components=EvaluationComponents(
        comp1_style_conventions=ComponentDetail(rating=QualitativeRating.GOOD),
        comp3_logic_edge_cases=ComponentDetail(rating=QualitativeRating.POOR),
        comp4_comments_submission=ComponentDetail(rating=QualitativeRating.FAIR)))
    s = suggest_marks(_result(set(), review), _assignment())
    c = {x.key: x.suggested for x in s.components}
    assert (c["comp1"], c["comp3"], c["comp4"]) == (3.0, 1.0, 2.0)


def test_missing_question_counts_as_zero():
    a = _assignment()
    a.questions.append(QuestionConfig(id="q2", name="Q2", test_cases=[TestCase(id="m1")]))
    s = suggest_marks(_result({"n1", "n2", "e1", "e2"}, _review(3, 6, 3)), a)
    assert {x.key: x.suggested for x in s.components}["comp2"] == 2.0  # 2 of 3 normal tests
    assert any("No source found for Q2" in f for f in s.flags)


def test_gemini_scores_are_parsed_and_clamped():
    raw = json.dumps({"comp1_style_conventions": {"score": 2.7}, "comp3_logic_edge_cases": {"score": 9},
                      "comp4_comments_submission": {"score": "x"}})
    review = GeminiReviewer(api_key="x")._parse_llm_json_response(raw, 0.0, "m", marking=MarkingScheme())
    assert review.components.comp1_style_conventions.score == 2.5
    assert review.components.comp3_logic_edge_cases.score == 6.0
    assert review.components.comp4_comments_submission.score is None


def test_prompt_contains_anchors_for_configured_maxima():
    from grader.models import CompilerResult
    prompt = GeminiReviewer(api_key="x")._build_review_prompt(
        QuestionConfig(id="q1", name="Q"), "int main(){}", CompilerResult(success=True, returncode=0), [], [],
        marking=MarkingScheme(comp3_max=10))
    assert "Comp 3 score, 0 to 10" in prompt and '"score"' in prompt


def test_late_flag_from_manifest(tmp_path):
    (tmp_path / ".classroom_sync.json").write_text(json.dumps({"students": {"1089": {"late": True}, "1090": {"late": False}}}))
    assert read_late_flag(tmp_path, "1089") is True
    assert read_late_flag(tmp_path, "1090") is False
    assert read_late_flag(tmp_path, "2001") is None


def test_unsimplified_fraction_passes_with_one_mark_off():
    from grader.tester import Tester
    from grader.models import ExecutionResult
    tc = TestCase(id="n1", expected_output="Addition: 5/6\nMultiplication: 1/6\n")
    ev = Tester.evaluate(tc, ExecutionResult(status=CheckStatus.PASS, returncode=0, stdout="Addition: 10/12\nMultiplication: 1/6\n"))
    assert ev.passed and ev.unsimplified and "not simplified" in ev.note
    wrong = Tester.evaluate(tc, ExecutionResult(status=CheckStatus.PASS, returncode=0, stdout="Addition: 7/12\nMultiplication: 1/6\n"))
    assert not wrong.passed

    res = _result({"n1", "n2", "e1", "e2"}, _review(3, 6, 3))
    res.question_results["q1"].test_results[0].unsimplified = True
    c = {x.key: x for x in suggest_marks(res, _assignment()).components}
    assert c["comp3"].suggested == 5.0 and "not simplified" in c["comp3"].reason
    assert c["comp2"].suggested == 3.0


def test_code_metrics_used_when_review_skipped(tmp_path):
    src = tmp_path / "a.cpp"
    src.write_text("// Rational package\n// add two numbers\nint add(int first, int second) {\n    return first + second;\n}\n", encoding="utf-8")
    res = _result({"n1"}, LLMReview(error="SKIPPED"))
    res.question_results["q1"].source_files = [str(src)]
    s = suggest_marks(res, _assignment())
    c = {x.key: x for x in s.components}
    assert c["comp1"].source == "Code metrics" and c["comp1"].suggested == 3.0
    assert c["comp4"].suggested is not None and "comment lines" in c["comp4"].reason
    assert not any("could not review" in f for f in s.flags)
