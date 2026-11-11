"""
Test evaluation engine supporting multiple comparison modes:
exact, whitespace-normalized, floating-point tolerance, token matching, and sorted sequence checks.
"""

from __future__ import annotations
import re
from typing import List, Optional, Tuple, Any

from grader.models import (
    TestCase,
    ExecutionResult,
    TestEvaluation,
    ComparisonMode,
    CheckStatus,
)


class Tester:
    """Evaluates execution outputs against test cases using configurable comparison modes."""

    @staticmethod
    def _make_eval(test_case: TestCase, execution: ExecutionResult, passed: bool, status: CheckStatus,
                   diff: str = "", note: str = "") -> TestEvaluation:
        return TestEvaluation(
            test_id=test_case.id,
            description=test_case.description,
            passed=passed,
            status=status,
            actual_output=execution.stdout,
            expected_output=test_case.expected_display or test_case.expected_output,
            diff_summary=diff,
            execution_time_sec=execution.duration_sec,
            is_edge_case=test_case.is_edge_case,
            input_data=test_case.input_data,
            stderr=(execution.stderr or "")[:4000],
            returncode=execution.returncode,
            note=note
        )

    @staticmethod
    def evaluate(test_case: TestCase, execution: ExecutionResult) -> TestEvaluation:
        """Evaluates an ExecutionResult against a TestCase."""
        def compare():
            try:
                return Tester._compare(
                    actual=execution.stdout,
                    expected=test_case.expected_output,
                    mode=test_case.comparison_mode,
                    float_tolerance=test_case.float_tolerance,
                    sort_order=test_case.sort_order,
                    sort_type=test_case.sort_type
                )
            except re.error as e:
                return False, f"Invalid regex in test case: {e}"

        if execution.status == CheckStatus.TIMEOUT:
            # Check if student code printed the correct answer before hanging on an interactive menu or EOF loop
            if execution.stdout and execution.stdout.strip():
                passed, _ = compare()
                if passed:
                    return Tester._make_eval(test_case, execution, True, CheckStatus.PASS,
                                             note="Correct output, but the program did not exit before the timeout.")
            return Tester._make_eval(
                test_case, execution, False, CheckStatus.TIMEOUT,
                diff=f"TIMEOUT: Program exceeded {test_case.timeout_sec}s limit (infinite loop, or waiting for more input than the test provides)."
            )

        if execution.status == CheckStatus.RUNTIME_ERROR:
            stderr_hint = (execution.stderr or "").strip()[:160]
            return Tester._make_eval(
                test_case, execution, False, CheckStatus.RUNTIME_ERROR,
                diff=f"CRASH: {execution.error_type or 'Runtime Error'}." + (f" {stderr_hint}" if stderr_hint else "")
            )

        passed, diff = compare()
        note = ""
        unsimplified: List[str] = []
        if not passed and test_case.comparison_mode not in (ComparisonMode.EXACT, ComparisonMode.LINES):
            # Right values, different form: 10/12 for 5/6 is not a wrong answer (the marks take a small deduction)
            same_value, unsimplified = Tester.compare_rational_values(execution.stdout, test_case.expected_output)
            if same_value:
                passed, diff = True, ""
                note = (f"Correct values, but not simplified: {', '.join(unsimplified[:3])}." if unsimplified
                        else "Correct values in an equivalent form (e.g. 2 for 2/1).")
        if execution.error_type and execution.error_type.startswith("NON_ZERO_EXIT_CODE"):
            exit_note = f"Program {execution.error_type.lower().replace('non_zero_exit_code', 'exited with code')}."
            note = f"{note} {exit_note}".strip()
            if not passed:
                diff = f"{diff} ({exit_note})"
        if execution.output_truncated:
            note = (note + " Output was truncated (very large).").strip()

        evaluation = Tester._make_eval(test_case, execution, passed, CheckStatus.PASS if passed else CheckStatus.FAIL,
                                       diff="" if passed else diff, note=note)
        evaluation.unsimplified = bool(unsimplified)
        return evaluation

    _RATIONAL_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:\s*/\s*[-+]?\d+(?:\.\d+)?)?")

    @staticmethod
    def _rational_tokens(text: str) -> List[Tuple[Any, str]]:
        """Numbers and a/b fractions in reading order, as (value, original text)."""
        from fractions import Fraction
        out = []
        for m in Tester._RATIONAL_RE.finditer(text or ""):
            raw = re.sub(r"\s+", "", m.group(0))
            try:
                if "/" in raw:
                    num, den = raw.split("/")
                    value = Fraction(num) / Fraction(den) if Fraction(den) != 0 else ("div0", raw)
                else:
                    value = Fraction(raw)
            except (ValueError, ZeroDivisionError):
                value = ("raw", raw)
            out.append((value, raw))
        return out

    @staticmethod
    def compare_rational_values(actual: str, expected: str) -> Tuple[bool, List[str]]:
        """
        True when every number/fraction of the expected output appears, in order and with the same
        value, as a contiguous run in the actual output. Also returns the matched fractions that were
        not in lowest terms (e.g. '10/12 (expected 5/6)').
        """
        from math import gcd
        exp = Tester._rational_tokens(expected)
        act = Tester._rational_tokens(actual)
        if not exp or not any("/" in raw for _, raw in exp + act) or len(act) < len(exp):
            return False, []
        for start in range(len(act) - len(exp) + 1):
            window = act[start:start + len(exp)]
            if all(a[0] == e[0] for a, e in zip(window, exp)):
                unsimplified = []
                for (val, raw), (_, eraw) in zip(window, exp):
                    if "/" in raw and "." not in raw:
                        num, den = (int(x) for x in raw.split("/"))
                        if den != 0 and gcd(abs(num), abs(den)) > 1:
                            unsimplified.append(f"{raw} (expected {eraw})")
                return True, unsimplified
        return False, []

    @staticmethod
    def _compare(
        actual: str,
        expected: str,
        mode: ComparisonMode,
        float_tolerance: float = 1e-4,
        sort_order: Optional[str] = None,
        sort_type: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Compares actual stdout with expected according to comparison mode."""
        if not expected.strip() and mode not in (ComparisonMode.EXACT, ComparisonMode.SORTED_CHECK):
            # If expected is blank and program finished without error, it passes execution check
            return True, ""

        if mode == ComparisonMode.EXACT:
            if actual == expected:
                return True, ""
            return False, f"Expected '{expected.strip()}', got '{actual.strip()}'"

        elif mode == ComparisonMode.WHITESPACE_NORMALIZED:
            norm_actual = Tester._normalize_whitespace(actual)
            norm_expected = Tester._normalize_whitespace(expected)
            
            # 1. Exact normalized match
            if norm_actual == norm_expected:
                return True, ""

            # 2. Substring inclusion (handles cases where scanf/cin prompt precedes result)
            if norm_expected and norm_expected in norm_actual:
                return True, ""

            # 3. In-order token subsequence match
            exp_tokens = norm_expected.split()
            act_tokens = norm_actual.split()
            if exp_tokens and Tester._is_subsequence(exp_tokens, act_tokens):
                return True, ""

            # 4. Numeric subsequence match (extracts all numbers from expected and verifies in actual)
            num_matched, _ = Tester._compare_numbers_subsequence(actual, expected, float_tolerance)
            if num_matched:
                return True, ""

            # 5. Prompt-stripped comparison
            clean_act = Tester._strip_cli_prompts(norm_actual)
            clean_exp = Tester._strip_cli_prompts(norm_expected)
            if clean_act and clean_exp and (clean_act == clean_exp or clean_exp in clean_act):
                return True, ""

            return False, f"Expected '{norm_expected}', got '{norm_actual}'"

        elif mode == ComparisonMode.FLOAT_TOLERANCE:
            passed, diff = Tester._compare_floats(actual, expected, float_tolerance)
            if passed:
                return True, ""
            # Fallback to numeric subsequence
            num_matched, _ = Tester._compare_numbers_subsequence(actual, expected, float_tolerance)
            if num_matched:
                return True, ""
            return False, diff

        elif mode == ComparisonMode.TOKEN_MATCH:
            exp_tokens = expected.strip().split()
            act_tokens = actual.strip().split()
            if Tester._is_subsequence(exp_tokens, act_tokens):
                return True, ""
            # Fallback to numeric subsequence
            num_matched, _ = Tester._compare_numbers_subsequence(actual, expected, float_tolerance)
            if num_matched:
                return True, ""
            return False, f"Expected tokens '{' '.join(exp_tokens)}' not found in order in output."

        elif mode == ComparisonMode.LINES:
            # Strict: for outputs whose words matter (tokens, parse results, tables), not just their numbers
            exp_lines = [" ".join(ln.split()) for ln in expected.splitlines() if ln.strip()]
            act_lines = [" ".join(ln.split()) for ln in actual.splitlines() if ln.strip()]
            if exp_lines == act_lines:
                return True, ""
            for i, (e, a) in enumerate(zip(exp_lines, act_lines), 1):
                if e != a:
                    return False, f"Line {i}: expected '{e}', got '{a}'"
            return False, f"Expected {len(exp_lines)} non-blank lines, got {len(act_lines)}"

        elif mode == ComparisonMode.REGEX_MATCH:
            if re.search(expected.strip(), actual, re.MULTILINE):
                return True, ""
            return False, f"Output did not match expected regex: {expected.strip()}"

        elif mode == ComparisonMode.SORTED_CHECK:
            return Tester._evaluate_sorted_output(actual, expected, sort_order, sort_type, float_tolerance)

        return False, "Unknown comparison mode."

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """Normalizes spaces and newlines."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return " ".join(" ".join(line.split()) for line in lines)

    @staticmethod
    def _strip_cli_prompts(text: str) -> str:
        """Strips standard interactive CLI prompts like 'Enter matrix A:', 'The result is:'."""
        # Only drop prompt labels that end in ':' ("Enter the elements:", "Result is:"), matching whole words.
        # The old pattern had no word boundaries and an optional colon, so "is" inside "this" erased the rest of the output.
        cleaned = re.sub(
            r"(?i)\b(?:enter|input|please|the|result|resultant|matrix|array|elements|dimension|rows?|cols?|choice|output|is|are)\b[^:\n]{0,40}:",
            " ",
            text
        )
        return Tester._normalize_whitespace(cleaned)

    @staticmethod
    def _compare_numbers_subsequence(actual: str, expected: str, tolerance: float = 1e-4) -> Tuple[bool, str]:
        """Extracts all numbers from expected and verifies they appear in actual in the same sequence."""
        float_regex = r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?"
        exp_nums = [float(x) for x in re.findall(float_regex, expected)]
        act_nums = [float(x) for x in re.findall(float_regex, actual)]

        if not exp_nums:
            return False, "No numbers to compare"

        if len(act_nums) < len(exp_nums):
            return False, f"Expected {len(exp_nums)} numbers, but found only {len(act_nums)} in output."

        for start_idx in range(len(act_nums) - len(exp_nums) + 1):
            match = True
            for i, ef in enumerate(exp_nums):
                af = act_nums[start_idx + i]
                if abs(af - ef) > tolerance:
                    match = False
                    break
            if match:
                return True, ""

        return False, f"Expected number sequence not found in output."

    @staticmethod
    def _compare_floats(actual: str, expected: str, tolerance: float) -> Tuple[bool, str]:
        """Extracts floats from expected and checks if corresponding floats in actual match within tolerance."""
        float_regex = r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?"
        exp_floats = [float(x) for x in re.findall(float_regex, expected)]
        act_floats = [float(x) for x in re.findall(float_regex, actual)]

        if not exp_floats:
            return True, ""

        if len(act_floats) < len(exp_floats):
            return False, f"Expected {len(exp_floats)} numbers, but found only {len(act_floats)} in output."

        # Search for exp_floats matching subsequence in act_floats
        for start_idx in range(len(act_floats) - len(exp_floats) + 1):
            match = True
            for i, ef in enumerate(exp_floats):
                af = act_floats[start_idx + i]
                if abs(af - ef) > tolerance:
                    match = False
                    break
            if match:
                return True, ""

        return False, f"Expected float values {[round(x, 3) for x in exp_floats]} not matched within {tolerance} tolerance."

    @staticmethod
    def _evaluate_sorted_output(
        actual: str,
        expected: str,
        sort_order: Optional[str],
        sort_type: Optional[str],
        tolerance: float = 1e-4
    ) -> Tuple[bool, str]:
        """
        Smart sorting checker:
        1. Checks whitespace-normalized substring match.
        2. Evaluates line-by-line (each line matching its sorted expected tokens/numbers/chars).
        3. Extracts numbers and floats with tolerance.
        """
        norm_actual = Tester._normalize_whitespace(actual)
        norm_expected = Tester._normalize_whitespace(expected)
        if norm_expected and norm_expected in norm_actual:
            return True, ""

        # Step 2: Line-by-line comparison with float and token normalization
        act_lines = [l.strip() for l in actual.splitlines() if l.strip()]
        exp_lines = [l.strip() for l in expected.splitlines() if l.strip()]

        if exp_lines:
            matched_lines_count = 0
            act_idx = 0
            for exp_l in exp_lines:
                exp_tokens = exp_l.split()
                # Find matching line in actual output from current act_idx forward
                found_match = False
                while act_idx < len(act_lines):
                    act_l = act_lines[act_idx]
                    act_idx += 1
                    act_tokens = act_l.split()

                    # Check if tokens match with float tolerance
                    if len(act_tokens) >= len(exp_tokens):
                        # Try to match exp_tokens as sub-sequence of act_tokens
                        for s_pos in range(len(act_tokens) - len(exp_tokens) + 1):
                            sub = act_tokens[s_pos : s_pos + len(exp_tokens)]
                            tokens_match = True
                            for a_tok, e_tok in zip(sub, exp_tokens):
                                if a_tok == e_tok:
                                    continue
                                try:
                                    # Compare as floats
                                    if abs(float(a_tok) - float(e_tok)) <= tolerance:
                                        continue
                                except ValueError:
                                    pass
                                tokens_match = False
                                break
                            if tokens_match:
                                found_match = True
                                break
                    if found_match:
                        matched_lines_count += 1
                        break

            if matched_lines_count == len(exp_lines):
                return True, ""

        return False, f"Sorted output did not match expected: '{norm_expected}'"

    @staticmethod
    def _is_subsequence(sub: List[str], full: List[str]) -> bool:
        """Returns True if sub list is a contiguous or in-order subsequence of full."""
        if not sub:
            return True
        it = iter(full)
        return all(item in it for item in sub)
