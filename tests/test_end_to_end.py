import json
import zipfile
from pathlib import Path

import pytest

from grader.main import AutograderPipeline
from grader.models import CheckStatus

SUM_OK = '#include <stdio.h>\nint main(void) { int a, b; scanf("%d %d", &a, &b); printf("Sum: %d\\n", a + b); return 0; }\n'
SUM_COMPILE_ERROR = '#include <stdio.h>\nint main(void) { int a, b scanf("%d %d", &a, &b); return 0; }\n'
SUM_CRASH = '#include <stdio.h>\nint main(void) { int *p = NULL; *p = 3; printf("Sum: 3\\n"); return 0; }\n'
SUM_LOOP = '#include <stdio.h>\nint main(void) { while (1) {} return 0; }\n'
SUM_EXIT_1 = '#include <stdio.h>\nint main(void) { int a, b; scanf("%d %d", &a, &b); printf("Sum: %d\\n", a + b); return 1; }\n'
SUM_SPLIT_MAIN = '#include <stdio.h>\n#include "sum.h"\nint main(void) { int a, b; scanf("%d %d", &a, &b); printf("Sum: %d\\n", add(a, b)); return 0; }\n'
SUM_SPLIT_IMPL = '#include "sum.h"\nint add(int a, int b) { return a + b; }\n'
SUM_SPLIT_HDR = '#ifndef SUM_H\n#define SUM_H\nint add(int a, int b);\n#endif\n'


@pytest.fixture()
def no_llm(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("TA_STUDENTS", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")


def _make_assignment(root: Path) -> Path:
    d = root / "assignments" / "e2e_lab"
    d.mkdir(parents=True)
    cfg = {
        "id": "e2e_lab",
        "title": "E2E Lab",
        "questions": [{
            "id": "q1",
            "name": "Sum",
            "language": "c",
            "test_cases": [
                {"id": "t1", "input_data": "2 3\n", "expected_output": "Sum: 5\n"},
                {"id": "t2", "input_data": "-4 4\n", "expected_output": "Sum: 0\n", "is_edge_case": True},
            ],
            "static_checks": [{"rule_id": "no_goto", "rule_type": "forbidden_regex", "description": "No goto", "forbidden_pattern": r"\bgoto\b"}],
        }],
    }
    (d / "assignment.json").write_text(json.dumps(cfg), encoding="utf-8")
    return d


def _zip(path: Path, files: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def test_full_pipeline_execution(tmp_path, no_llm):
    assignment_dir = _make_assignment(tmp_path)
    subs = tmp_path / "subs"
    subs.mkdir()
    _zip(subs / "s01_ok.zip", {"q1.c": SUM_OK})
    _zip(subs / "s02_compile.zip", {"q1.c": SUM_COMPILE_ERROR})
    _zip(subs / "s03_crash.zip", {"q1.c": SUM_CRASH})
    _zip(subs / "s04_loop.zip", {"q1.c": SUM_LOOP})
    _zip(subs / "s05_exit1.zip", {"q1.c": SUM_EXIT_1})
    _zip(subs / "s06_nested.zip", {"My Lab/Deep Folder/main.c": SUM_SPLIT_MAIN, "My Lab/Deep Folder/sum.c": SUM_SPLIT_IMPL, "My Lab/Deep Folder/sum.h": SUM_SPLIT_HDR})
    (subs / "s07_broken.zip").write_bytes(b"not a zip at all")

    pipeline = AutograderPipeline(
        assignment_id_or_path=str(assignment_dir),
        output_dir=str(tmp_path / "reports"),
        results_dir=str(tmp_path / "results"),
        log_dir=str(tmp_path / "logs"),
    )
    pipeline.scratch_root = tmp_path / "scratch"
    pipeline.drivers_root = tmp_path / "drivers"
    for t in pipeline.assignment.questions[0].test_cases:
        t.timeout_sec = 1.0

    results = {r.student_id: r for r in pipeline.grade_batch(subs, rerun_all=True)}

    ok = results["s01_ok"]
    assert ok.compilation_failures == 0 and ok.runtime_failures == 0
    assert ok.total_tests_passed == 2
    assert ok.manual_review_recommended is False
    assert ok.assignment_id == "e2e_lab"

    assert results["s02_compile"].compilation_failures == 1
    assert results["s02_compile"].manual_review_recommended

    crash = results["s03_crash"].question_results["q1"]
    assert all(t.status == CheckStatus.RUNTIME_ERROR for t in crash.test_results)
    assert "SEGMENTATION" in crash.test_results[0].diff_summary

    loop = results["s04_loop"].question_results["q1"]
    assert all(t.status == CheckStatus.TIMEOUT for t in loop.test_results)

    # A non-zero exit code is not a crash: correct output still passes, with a note
    exit1 = results["s05_exit1"].question_results["q1"]
    assert exit1.passed_tests_count == 2
    assert "exited with code" in exit1.test_results[0].note

    # Multi-file submission in nested folders with spaces links header + implementation
    assert results["s06_nested"].total_tests_passed == 2

    assert results["s07_broken"].error_message
    assert results["s07_broken"].manual_review_recommended

    assert (tmp_path / "reports" / "s01_ok.md").exists()
    assert (tmp_path / "results" / "s01_ok.json").exists()
    assert (tmp_path / "reports" / "BATCH_SUMMARY.md").exists()


def test_cached_result_from_other_assignment_is_not_reused(tmp_path, no_llm):
    assignment_dir = _make_assignment(tmp_path)
    subs = tmp_path / "subs"
    subs.mkdir()
    _zip(subs / "s01.zip", {"q1.c": SUM_OK})
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "s01.json").write_text(json.dumps({"student_id": "s01", "assignment_id": "some_other_week", "timestamp": "x"}), encoding="utf-8")

    pipeline = AutograderPipeline(str(assignment_dir), output_dir=str(tmp_path / "r"), results_dir=str(results_dir), log_dir=str(tmp_path / "l"))
    pipeline.scratch_root = tmp_path / "scratch"
    pipeline.drivers_root = tmp_path / "drivers"
    res = pipeline.grade_single_student(subs / "s01.zip")
    assert res.assignment_id == "e2e_lab"
    assert res.total_tests_passed == 2


def test_resubmitted_zip_is_regraded_in_default_mode(tmp_path, no_llm):
    import os, time
    assignment_dir = _make_assignment(tmp_path)
    subs = tmp_path / "subs"
    subs.mkdir()
    z = _zip(subs / "s01.zip", {"q1.c": SUM_COMPILE_ERROR})
    pipeline = AutograderPipeline(str(assignment_dir), output_dir=str(tmp_path / "r"), results_dir=str(tmp_path / "res"), log_dir=str(tmp_path / "l"))
    pipeline.scratch_root = tmp_path / "scratch"
    pipeline.drivers_root = tmp_path / "drivers"
    assert pipeline.grade_single_student(z).compilation_failures == 1
    assert pipeline.grade_single_student(z).compilation_failures == 1  # unchanged -> cached

    _zip(z, {"q1.c": SUM_OK})  # student resubmits a fixed version
    later = time.time() + 5
    os.utime(z, (later, later))
    assert pipeline.grade_single_student(z).total_tests_passed == 2
