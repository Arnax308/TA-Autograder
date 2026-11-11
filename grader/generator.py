"""
LLM Test Case Generator Module.
Generates comprehensive test suites (normal cases, edge cases, corner cases, float tolerances)
for new questions and saves them to the assignment configuration to ensure frozen, fair batch evaluation.
"""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from grader.config import SystemConfig, AssignmentLoader, AssignmentManager
from grader.llm_router import call_llm
from grader.models import TestCase, ComparisonMode, QuestionConfig, LanguageType


class TestCaseGenerator:
    """Generates structured test cases using Gemini and locks them into assignment configuration."""

    __test__ = False  # not a pytest test class

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or SystemConfig.get_llm_api_key()
        self.model = model or SystemConfig.get_llm_model()

    def generate_tests_for_question(
        self,
        question_title: str,
        problem_description: str,
        language: str = "c",
        num_tests: int = 4
    ) -> List[TestCase]:
        """Generates a list of test cases for a specific problem."""
        if not self.api_key:
            raise ValueError("Gemini API key is required to generate test cases.")

        prompt = self._build_generator_prompt(question_title, problem_description, language, num_tests)
        raw_json, error_msg = self._call_gemini(prompt)

        if error_msg or not raw_json:
            raise RuntimeError(f"Failed to generate test cases: {error_msg or 'Empty response'}")

        test_cases = self._parse_test_cases(raw_json)
        return test_cases

    def generate_and_save_tests(
        self,
        assignment_id_or_path: str | Path,
        question_id: str,
        problem_description: str,
        num_tests: int = 4
    ) -> List[TestCase]:
        """Generates test cases and saves them directly to the assignment configuration."""
        cfg = AssignmentLoader.load_assignment(assignment_id_or_path)

        target_q: Optional[QuestionConfig] = None
        for q in cfg.questions:
            if q.id.lower() == question_id.lower():
                target_q = q
                break

        q_name = target_q.name if target_q else f"Question {question_id}"
        q_lang = target_q.language.value if target_q else "c"

        test_cases = self.generate_tests_for_question(
            question_title=q_name,
            problem_description=problem_description,
            language=q_lang,
            num_tests=num_tests
        )

        # Update assignment in memory
        if target_q:
            target_q.test_cases = test_cases
        else:
            # Create question if it didn't exist
            new_q = QuestionConfig(
                id=question_id.lower(),
                name=q_name,
                description=problem_description,
                language=LanguageType(q_lang),
                test_cases=test_cases
            )
            cfg.questions.append(new_q)

        # Save back through the manager so YAML packs and modular tests.json files stay consistent
        p = Path(assignment_id_or_path)
        target_dir = p if p.is_dir() else AssignmentManager.resolve_assignment_dir(str(assignment_id_or_path))
        AssignmentManager.save_assignment(cfg, target_dir)

        return test_cases

    def _build_generator_prompt(
        self,
        title: str,
        description: str,
        language: str,
        num_tests: int
    ) -> str:
        return f"""You are an expert Computer Science Professor and TA test case designer for C/C++ programming labs.

Design a comprehensive test suite of {num_tests} test cases for the following programming problem.

### PROBLEM DETAILS:
Title: {title}
Language: {language.upper()}
Description: {description}

### REQUIREMENTS:
1. Include at least 1 normal representative case.
2. Include at least 2 distinct edge cases (e.g. single element, reverse ordered, duplicates, negative numbers, boundary values).
3. The test cases must be appropriate for automated testing where stdin is fed directly via pipe.
4. If the program requires interactive input, provide the standard newline-separated input string.
5. Provide the expected output string.
6. Select the best comparison mode:
   - "sorted_check" (if verifying sorting algorithms)
   - "whitespace_normalized" (general text/number output)
   - "float_tolerance" (floating-point numerical calculations)
   - "token_match" (checks for presence of essential values in order)
   - "exact" (strict exact string)

### REQUIRED JSON SCHEMA:
Respond ONLY with a JSON array of objects matching this exact structure:
[
  {{
    "id": "t1_normal",
    "description": "Short explanation of this test scenario",
    "input_data": "formatted stdin with newlines\\n",
    "expected_output": "expected stdout\\n",
    "comparison_mode": "sorted_check | whitespace_normalized | float_tolerance | token_match | exact | lines",
    "float_tolerance": 0.0001,
    "timeout_sec": 2.0,
    "is_edge_case": false,
    "sort_order": "ascending | descending | null",
    "sort_type": "int | float | char | null"
  }}
]

Output ONLY the JSON array.
"""

    def _call_gemini(self, prompt: str) -> Tuple[Optional[str], Optional[str]]:
        text, err, _ = call_llm(
            prompt, model=self.model, json_mode=True,
            temperature=0.1, max_output_tokens=16384, timeout_sec=150.0
        )
        return text, err

    def _parse_test_cases(self, raw_json: str) -> List[TestCase]:
        cleaned = raw_json.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", cleaned, re.DOTALL)
            if not match:
                raise ValueError("Gemini did not return a JSON array of test cases.")
            data = json.loads(match.group(0))
        if isinstance(data, dict):
            data = data.get("test_cases") or data.get("tests") or [data]
        if not isinstance(data, list):
            raise ValueError("Gemini returned an unexpected test case format.")

        def nullable(v: Any) -> Optional[str]:
            if v is None or str(v).strip().lower() in ("", "null", "none", "n/a"):
                return None
            return str(v).strip().lower()

        test_cases = []
        seen_ids = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            # Map comparison mode safely
            cm_str = str(item.get("comparison_mode") or "whitespace_normalized").lower()
            try:
                cm = ComparisonMode(cm_str)
            except Exception:
                cm = ComparisonMode.WHITESPACE_NORMALIZED

            tc_id = str(item.get("id") or f"t{len(test_cases)+1}")
            while tc_id in seen_ids:
                tc_id += "_b"
            seen_ids.add(tc_id)
            try:
                tolerance = float(item.get("float_tolerance") or 1e-4)
                timeout = float(item.get("timeout_sec") or 2.0)
            except (TypeError, ValueError):
                tolerance, timeout = 1e-4, 2.0
            tc = TestCase(
                id=tc_id,
                description=str(item.get("description") or ""),
                input_data=str(item.get("input_data") or ""),
                expected_output=str(item.get("expected_output") or ""),
                comparison_mode=cm,
                float_tolerance=tolerance,
                timeout_sec=timeout,
                is_edge_case=bool(item.get("is_edge_case", False)),
                sort_order=nullable(item.get("sort_order")),
                sort_type=nullable(item.get("sort_type"))
            )
            test_cases.append(tc)

        if not test_cases:
            raise ValueError("Gemini returned no usable test cases.")

        return test_cases
