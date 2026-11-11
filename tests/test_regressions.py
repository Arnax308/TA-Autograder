"""Regression tests for bugs fixed in the sandbox / pipeline overhaul."""

import json
import sys

import pytest
import yaml

from grader.analyzer import StaticAnalyzer
from grader.compiler import Compiler, has_main, detect_language
from grader.config import AssignmentManager, AssignmentLoader, SystemConfig
from grader.generator import TestCaseGenerator
from grader.llm import GeminiReviewer
from grader.models import (CheckStatus, ComparisonMode, ExecutionResult, LanguageType, StaticCheckRule, TestCase)
from grader.runner import SandboxRunner
from grader.tester import Tester


# ---------- LLM driver extraction (every C++ driver used to start with "pp") ----------

@pytest.mark.parametrize("fence", ["```cpp", "```c++", "```c", "```C", "```", "``` cpp"])
def test_extract_code_block_strips_language_tag(fence):
    raw = f"Here you go:\n{fence}\n#include <stdio.h>\nint main() {{ return 0; }}\n```\nDone."
    code = GeminiReviewer.extract_code_block(raw)
    assert code.startswith("#include <stdio.h>")


def test_cached_driver_with_stray_pp_line_is_repaired():
    assert GeminiReviewer.strip_stray_language_tag("pp\n#include <iostream>\nint main(){}\n").startswith("#include")
    assert GeminiReviewer.strip_stray_language_tag("#include <iostream>\n").startswith("#include")


def test_llm_review_parser_tolerates_wrong_shapes():
    r = GeminiReviewer(api_key="x")
    review = r._parse_llm_json_response(json.dumps({
        "comp1_style_conventions": "GOOD",          # string instead of object
        "requirements": ["not a dict", {"requirement": "R1", "status": "pass"}],
        "issues": "single issue string",
        "algorithmic_assessment": None,
    }), 1.0, "m")
    assert review.requirements[0].status == CheckStatus.PASS
    assert review.issues == ["single issue string"]


# ---------- Runner ----------

def test_plain_nonzero_exit_is_not_a_crash():
    assert SandboxRunner._classify_crash_code(1, wsl=True) is None
    assert SandboxRunner._classify_crash_code(11, wsl=True) is None  # exit(11) is not SIGSEGV
    assert "SEGMENTATION" in SandboxRunner._classify_crash_code(139, wsl=True)
    if sys.platform == "win32":
        assert SandboxRunner._classify_crash_code(1) is None
        assert "SEGMENTATION" in SandboxRunner._classify_crash_code(0xC0000005 - (1 << 32))


def test_smart_app_control_block_is_reported_distinctly():
    err = OSError(22, "An Application Control policy has blocked this file")
    err.winerror = 4551
    res = SandboxRunner._launch_failure(err, 0.0)
    assert res.error_type == "BLOCKED_BY_SMART_APP_CONTROL"


def test_wsl_path_conversion():
    if sys.platform != "win32":
        pytest.skip("Windows-only path conversion")
    assert SystemConfig.to_wsl_path("C:\\Users\\a b\\x.c") == "/mnt/c/Users/a b/x.c"


@pytest.mark.parametrize("backend", ["native", "wsl"])
def test_compile_and_run_on_each_backend(tmp_path, backend):
    if backend == "wsl" and not SystemConfig.wsl_toolchain():
        pytest.skip("WSL toolchain not installed")
    if backend == "native" and not SystemConfig.find_compiler(LanguageType.C)[0]:
        pytest.skip("No native compiler")
    src = tmp_path / "add.c"
    src.write_text('#include <stdio.h>\nint main(void){int a,b; scanf("%d %d",&a,&b); printf("%d\\n",a+b); return 0;}\n', encoding="utf-8")
    res = Compiler(tmp_path / "logs").compile([src], tmp_path / "add.exe", backend=backend)
    assert res.success, res.concise_error
    assert res.backend == backend
    run = SandboxRunner.run_executable(tmp_path / "add.exe", "20 22", timeout_sec=5)
    if run.error_type == "BLOCKED_BY_SMART_APP_CONTROL":
        pytest.skip("Smart App Control blocked the native binary on this machine")
    assert run.status == CheckStatus.PASS
    assert run.stdout.strip() == "42"


# ---------- Compiler ----------

def test_commented_out_main_is_not_counted():
    assert not has_main("// int main() { }\n/* int main(void) */\nint add(int a, int b) { return a + b; }")
    assert has_main("int main(void) { return 0; }")


def test_cpp_code_in_c_file_is_detected(tmp_path):
    f = tmp_path / "rational.c"
    f.write_text("#include <iostream>\nclass R { int p; };\n", encoding="utf-8")
    assert detect_language([f]) == LanguageType.CPP


def test_duplicate_c_and_cpp_variants_are_deduplicated(tmp_path):
    files = [tmp_path / "Rational.c", tmp_path / "Rational.cpp", tmp_path / "main.cpp"]
    kept = Compiler._dedupe_language_variants(files, is_cpp=True)
    assert tmp_path / "Rational.cpp" in kept and tmp_path / "Rational.c" not in kept


def test_c_std_flag_is_dropped_for_cpp():
    flags = Compiler._build_flags(True, ["-std=c11", "-Wall", "-I", "x"], "wsl")
    assert "-std=c11" not in flags and "-std=c++17" in flags
    assert flags[-2:] == ["-I", "x"]


def test_missing_stdbool_is_retried(tmp_path):
    src = tmp_path / "b.c"
    src.write_text("int main(void) { bool ok = true; return ok ? 0 : 1; }\n", encoding="utf-8")
    res = Compiler(tmp_path / "logs").compile([src], tmp_path / "b.exe")
    assert res.success, res.concise_error


def test_student_defined_bool_still_compiles(tmp_path):
    src = tmp_path / "b.c"
    src.write_text("typedef enum { false, true } bool;\nint main(void) { bool ok = true; return ok ? 0 : 1; }\n", encoding="utf-8")
    res = Compiler(tmp_path / "logs").compile([src], tmp_path / "b.exe")
    assert res.success, res.concise_error


# ---------- Tester ----------

def test_prompt_stripping_does_not_eat_output():
    ok, _ = Tester._compare("This value: 9\n", "Result is: 5\n", ComparisonMode.WHITESPACE_NORMALIZED)
    assert not ok


def test_invalid_regex_test_case_does_not_crash():
    tc = TestCase(id="t", expected_output="([unclosed", comparison_mode=ComparisonMode.REGEX_MATCH)
    ev = Tester.evaluate(tc, ExecutionResult(status=CheckStatus.PASS, returncode=0, stdout="x"))
    assert not ev.passed and "Invalid regex" in ev.diff_summary


# ---------- Static analyzer ----------

def test_invalid_rule_regex_does_not_crash(tmp_path):
    f = tmp_path / "a.c"
    f.write_text("int main(){return 0;}", encoding="utf-8")
    rule = StaticCheckRule(rule_id="r", rule_type="contains_regex", description="d", pattern="(unclosed")
    res = StaticAnalyzer.analyze_files([f], [rule])
    assert res[0].status == CheckStatus.UNCERTAIN


def test_grouped_regex_snippets_are_strings(tmp_path):
    f = tmp_path / "a.c"
    f.write_text("int x = 1; int y = 2;", encoding="utf-8")
    rule = StaticCheckRule(rule_id="r", rule_type="contains_regex", description="d", pattern=r"(int)\s+(\w+)")
    res = StaticAnalyzer.analyze_files([f], [rule])
    assert all(isinstance(s, str) for s in res[0].matched_snippets)


def test_plain_assignment_is_not_function_pointer_usage(tmp_path):
    f = tmp_path / "a.c"
    f.write_text("void (*unused)(int);\nint main(){ int a; int b = 2; a = b; return 0; }", encoding="utf-8")
    rule = StaticCheckRule(rule_id="fp", rule_type="function_pointer", description="fp")
    res = StaticAnalyzer.analyze_files([f], [rule])
    assert res[0].status == CheckStatus.UNCERTAIN


# ---------- Config ----------

def test_yaml_assignment_roundtrip(tmp_path):
    target = AssignmentManager.create_new_assignment("yaml_lab", base_dir=str(tmp_path), format_type="yaml")
    AssignmentManager.update_question("yaml_lab", "q1", timeout_sec=3.0, base_dir=str(tmp_path))
    cfg = AssignmentLoader.load_assignment(target)  # used to fail: enums dumped as python objects
    assert cfg.questions[0].timeout_sec == 3.0
    assert yaml.safe_load((target / "assignment.yaml").read_text(encoding="utf-8"))["questions"][0]["language"] == "c"


def test_edits_survive_modular_tests_file(tmp_path):
    target = AssignmentManager.create_new_assignment("mod_lab", base_dir=str(tmp_path))
    (target / "tests.json").write_text(json.dumps({"q1": [{"id": "old", "input_data": "", "expected_output": ""}]}), encoding="utf-8")
    cfg = AssignmentLoader.load_assignment(target)
    cfg.questions[0].test_cases = [TestCase(id="new")]
    AssignmentManager.save_assignment(cfg, target)
    assert [t.id for t in AssignmentLoader.load_assignment(target).questions[0].test_cases] == ["new"]


def test_delete_assignment_refuses_paths_outside_base(tmp_path):
    (tmp_path / "victim").mkdir()
    base = tmp_path / "assignments"
    base.mkdir()
    with pytest.raises(FileNotFoundError):
        AssignmentManager.delete_assignment("../victim", base_dir=str(base))
    assert (tmp_path / "victim").exists()


def test_generator_parses_null_strings():
    tests = TestCaseGenerator(api_key="x")._parse_test_cases(json.dumps([
        {"id": "t1", "expected_output": "1", "sort_order": "null", "sort_type": "None", "timeout_sec": None},
        {"id": "t1", "expected_output": "2"},
    ]))
    assert tests[0].sort_order is None and tests[0].sort_type is None and tests[0].timeout_sec == 2.0
    assert tests[0].id != tests[1].id


# ---------- Student scope ----------

def test_student_scope_ranges():
    from grader.config import StudentScope
    s = StudentScope.parse("cs25b1089-cs25b1103, cs25b2001 to cs25b2008, 2010")
    assert s.includes("1089") and s.includes("1103") and s.includes("2005") and s.includes("2010")
    assert not s.includes("1088") and not s.includes("1104") and not s.includes("2009")
    assert s.includes("1095", "cs25b1095@iiitdm.ac.in")
    assert not s.includes("1095", "cs24b1095@iiitdm.ac.in")  # other batch, same number
    assert s.size() == 15 + 8 + 1
    assert StudentScope.parse("").includes("anything")
    with pytest.raises(ValueError):
        StudentScope.parse("cs25b1089-cs25b1103-cs25b1200")


def test_submission_listing_honours_scope(tmp_path, monkeypatch):
    from grader.main import list_submission_items
    for sid in ("1088", "1089", "1103", "2004", "2009"):
        (tmp_path / f"{sid}.zip").write_bytes(b"")
    monkeypatch.setenv("TA_STUDENTS", "cs25b1089-cs25b1103, cs25b2001-cs25b2008")
    assert [p.stem for _, p in list_submission_items(tmp_path)] == ["1089", "1103", "2004"]
    assert len(list_submission_items(tmp_path, only_my_students=False)) == 5


def test_split_programs_keeps_same_named_files_per_task_folder(tmp_path):
    """Two task folders with identically named headers/impls must not leak into each other's program."""
    from grader.extractor import Extractor
    for task in ("q3", "q4"):
        d = tmp_path / task
        d.mkdir()
        (d / "sh.h").write_text("class Sh { public: int a(); };\n")
        (d / "sh.cpp").write_text('#include "sh.h"\nint Sh::a() { return 1; }\n')
        (d / "main.cpp").write_text('#include "sh.h"\nint main() { Sh s; return s.a(); }\n')
    # Q1 has Student.h + StudentFunctions.cpp; Q2 has its own Student.h + Student.cpp
    (tmp_path / "Q1").mkdir()
    (tmp_path / "Q1" / "Student.h").write_text("struct Student { ~Student(); };\n")
    (tmp_path / "Q1" / "StudentFunctions.cpp").write_text('#include "Student.h"\nStudent::~Student() {}\n')
    (tmp_path / "Q1" / "main.cpp").write_text('#include "Student.h"\nint main() { Student s; }\n')
    (tmp_path / "Q2").mkdir()
    (tmp_path / "Q2" / "Student.h").write_text("struct Student { ~Student(); };\n")
    (tmp_path / "Q2" / "Student.cpp").write_text('#include "Student.h"\nStudent::~Student() {}\n')
    (tmp_path / "Q2" / "main.cpp").write_text('#include "Student.h"\nint main() { Student s; }\n')

    files = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    programs = Extractor.split_programs(files)
    assert len(programs) == 4
    for prog in programs:
        assert len({f.parent for f in prog}) == 1, [str(f) for f in prog]


def test_driver_that_prints_expected_answers_is_detected():
    from grader.main import hardcoded_answers
    from grader.models import QuestionConfig, TestCase
    q = QuestionConfig(id="q1", name="Sorts", test_cases=[
        TestCase(id="t1", input_data="Sort 64 25 12", expected_output="Selection: 12 25 64"),
        TestCase(id="t2", input_data="Matrix rows: 1 2 / 3 4", expected_output="1 2\n3 4\nArea: 0"),
    ])
    cheating = r'int main(){ std::cout << "Selection: 12 25 64\n"; }'
    honest = (r'int main(){ std::istringstream in("1 2 3 4"); std::cout << "Selection: "; s.sort(); '
              'printf("Area: 0\\n"); // Selection: 12 25 64\n }')
    assert hardcoded_answers(cheating, q) == ["Selection: 12 25 64"]
    assert hardcoded_answers(honest, q) == []


def test_lines_mode_is_strict_about_words():
    from grader.models import ComparisonMode
    from grader.tester import Tester
    exp = "OPERATOR >=\nNUMBER 10\n"
    assert Tester._compare("OPERATOR  >=\r\n\nNUMBER 10", exp, ComparisonMode.LINES)[0]
    assert not Tester._compare("OPERATOR >\nOPERATOR =\nNUMBER 10\n", exp, ComparisonMode.LINES)[0]
    assert not Tester._compare("OPERATOR >=\nNUMBER 10\nextra\n", exp, ComparisonMode.LINES)[0]
    # the lenient default would accept the wrong tokens because the numbers still appear in order
    assert Tester._compare("OPERATOR >\nOPERATOR =\nNUMBER 10\n", exp, ComparisonMode.WHITESPACE_NORMALIZED)[0]
