"""Program splitting for multi-task labs, doubled extensions, re-evaluation, and the Groq limiter."""

import json
import time

from grader.config import AssignmentLoader
from grader.extractor import Extractor
from grader.models import AssignmentConfig, QuestionConfig, TestCase


def _write(root, files):
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_programs_are_split_by_main_and_includes(tmp_path):
    _write(tmp_path, {
        "lab/rational.h": "class Rational { public: Rational(); };",
        "lab/rational.cpp": '#include "rational.h"\nRational::Rational() {}',
        "lab/main1.cpp": '#include "rational.h"\nint main() { Rational r; }',
        "lab/number.h": "class Number { public: bool isPrime(int); };",
        "lab/prime.cpp": '#include "number.h"\nbool Number::isPrime(int n) { return n > 1; }',
        "lab/main2.cpp": '#include "number.h"\nint main() { Number n; n.isPrime(7); }',
    })
    files = Extractor.collect_source_files(tmp_path)
    programs = sorted(sorted(f.name for f in p) for p in Extractor.split_programs(files))
    assert programs == [["main1.cpp", "rational.cpp", "rational.h"], ["main2.cpp", "number.h", "prime.cpp"]]

    cfg = AssignmentConfig(id="x", title="x", questions=[
        QuestionConfig(id="q1", name="Rational", content_keywords=[r"\bRational\b"]),
        QuestionConfig(id="q2", name="Numbers", content_keywords=[r"prime"]),
    ])
    m = Extractor.map_files_to_questions(files, cfg, use_llm_mapper=False)
    assert sorted(f.name for f in m["q1"]) == ["main1.cpp", "rational.cpp", "rational.h"]
    assert sorted(f.name for f in m["q2"]) == ["main2.cpp", "number.h", "prime.cpp"]


def test_doubled_extensions_are_fixed(tmp_path):
    _write(tmp_path, {"a/RAT.h.cpp": "x", "a/main.cpp.cpp": "y", "a/ok.cpp": "z", "a/keep.h": "k", "a/keep.h.cpp": "dup"})
    renamed = Extractor.fix_double_extensions(tmp_path)
    assert sorted(renamed) == ["RAT.h.cpp -> RAT.h", "main.cpp.cpp -> main.cpp"]
    assert (tmp_path / "a/RAT.h").exists() and (tmp_path / "a/keep.h.cpp").exists()


def test_reevaluate_flips_unsimplified_fraction_failures(tmp_path, monkeypatch):
    from grader.main import AutograderPipeline
    from grader.models import CheckStatus, QuestionGradingResult, StudentGradingResult, TestEvaluation
    from grader.cache import ResultsCache
    monkeypatch.setenv("TA_STUDENTS", "")
    d = tmp_path / "assignments" / "frac"
    d.mkdir(parents=True)
    (d / "assignment.json").write_text(json.dumps({"id": "frac", "title": "F", "questions": [
        {"id": "q1", "name": "Q", "test_cases": [{"id": "t1", "expected_output": "Addition: 5/6"}, {"id": "t2", "expected_output": "Addition: 1/2"}]}]}))
    results = tmp_path / "results"
    evals = [TestEvaluation(test_id="t1", description="", passed=False, status=CheckStatus.FAIL, actual_output="Addition: 10/12", expected_output="Addition: 5/6"),
             TestEvaluation(test_id="t2", description="", passed=False, status=CheckStatus.FAIL, actual_output="Addition: 3/4", expected_output="Addition: 1/2")]
    q = QuestionGradingResult(question_id="q1", question_name="Q", language="c", test_results=evals, total_tests_count=2,
                              needs_manual_review=True, manual_review_reasons=["2/2 tests failed."])
    ResultsCache(results).save_result(StudentGradingResult(student_id="s1", assignment_id="frac", question_results={"q1": q}, total_tests_run=2, timestamp="x"))

    p = AutograderPipeline(str(d), output_dir=str(tmp_path / "reports"), results_dir=str(results), log_dir=str(tmp_path / "logs"))
    assert p.reevaluate_results() == [("s1", 1, 2)]
    res = ResultsCache(results).load_result("s1")
    assert res.total_tests_passed == 1
    assert res.question_results["q1"].test_results[0].unsimplified
    assert res.question_results["q1"].manual_review_reasons[0] == "1/2 tests failed."


def test_groq_limiter_waits_instead_of_overspending(monkeypatch):
    from grader import groq_client
    monkeypatch.setattr(groq_client, "TPM_LIMIT", 1000)
    groq_client._usage.clear()
    sleeps = []
    real_time = time.time
    clock = [real_time()]
    monkeypatch.setattr(groq_client.time, "time", lambda: clock[0])
    monkeypatch.setattr(groq_client.time, "sleep", lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)))
    groq_client._reserve("m", 700)
    groq_client._reserve("m", 700)  # must wait for the first reservation to age out of the minute window
    assert sleeps and sum(sleeps) >= 59


def test_lab_packs_load():
    for w in range(5, 11):
        cfg = AssignmentLoader.load_assignment(f"week_{w}")
        assert all(q.language.value == "cpp" for q in cfg.questions)
        assert any(q.test_cases or q.static_checks for q in cfg.questions)
