import pytest
from pathlib import Path
from grader.tester import Tester
from grader.analyzer import StaticAnalyzer
from grader.models import (
    TestCase,
    ExecutionResult,
    ComparisonMode,
    CheckStatus,
    StaticCheckRule,
    LanguageType
)

def test_tester_whitespace_and_sorted():
    tc = TestCase(
        id="t1",
        expected_output="1 2 3 4 5",
        comparison_mode=ComparisonMode.WHITESPACE_NORMALIZED
    )
    exec_res = ExecutionResult(status=CheckStatus.PASS, returncode=0, stdout="  1   2\n3 4 5  \n")
    eval_res = Tester.evaluate(tc, exec_res)
    assert eval_res.passed is True

    # Test sorting checker
    tc_sort = TestCase(
        id="t2",
        expected_output="1 2 3 4 5",
        comparison_mode=ComparisonMode.SORTED_CHECK,
        sort_order="ascending"
    )
    exec_sort = ExecutionResult(
        status=CheckStatus.PASS,
        returncode=0,
        stdout="Enter elements:\nSorted array is: 1 2 3 4 5\nDone."
    )
    eval_sort = Tester.evaluate(tc_sort, exec_sort)
    assert eval_sort.passed is True

def test_tester_float_tolerance():
    tc = TestCase(
        id="t_float",
        expected_output="3.1415 2.7182",
        comparison_mode=ComparisonMode.FLOAT_TOLERANCE,
        float_tolerance=1e-3
    )
    exec_res = ExecutionResult(status=CheckStatus.PASS, returncode=0, stdout="Result: 3.14159 2.71828\n")
    eval_res = Tester.evaluate(tc, exec_res)
    assert eval_res.passed is True

def test_static_analyzer_template_and_overloading(tmp_path):
    cpp_file = tmp_path / "template.cpp"
    cpp_file.write_text("""
    #include <iostream>
    template <typename T>
    void sort(T arr[], int n) {}
    """, encoding="utf-8")

    rule_template = StaticCheckRule(
        rule_id="r_template",
        rule_type="template_declaration",
        description="Template Check"
    )
    results = StaticAnalyzer.analyze_files([cpp_file], [rule_template], expected_language=LanguageType.CPP)
    assert len(results) == 1
    assert results[0].passed is True

def test_static_analyzer_function_pointer(tmp_path):
    c_file = tmp_path / "func_ptr.c"
    c_file.write_text("""
    #include <stdio.h>
    int cmp(int a, int b) { return a > b; }
    void sort(int arr[], int n, int (*compare)(int, int)) {
        compare(1, 2);
    }
    int main() {
        int (*fptr)(int, int) = cmp;
        sort(NULL, 0, fptr);
        return 0;
    }
    """, encoding="utf-8")

    rule_fptr = StaticCheckRule(
        rule_id="r_fptr",
        rule_type="function_pointer",
        description="Function pointer check"
    )
    results = StaticAnalyzer.analyze_files([c_file], [rule_fptr], expected_language=LanguageType.C)
    assert len(results) == 1
    assert results[0].passed is True
