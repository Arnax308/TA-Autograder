"""
LLM Integration Module for Gemini.
Conducts fair, balanced qualitative reviews of C/C++ student code without ever assigning final marks.
"""

from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from grader.config import SystemConfig
from grader.llm_router import call_llm, context_budget_chars
from grader.models import (
    LLMReview,
    LLMReviewItem,
    QualitativeRating,
    AlgorithmicAssessment,
    CheckStatus,
    QuestionConfig,
    CompilerResult,
    TestEvaluation,
    StaticCheckResult,
    ComponentDetail,
    EvaluationComponents,
    MarkingScheme,
)


class GeminiReviewer:
    """Invokes Google Gemini API to produce structured, balanced qualitative reviews."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or SystemConfig.get_llm_api_key()
        self.model = model or SystemConfig.get_llm_model()
        self.last_model_used: Optional[str] = None
        self.last_driver_error: Optional[str] = None

    def review_question_submission(
        self,
        question: QuestionConfig,
        source_code: str,
        compiler_result: CompilerResult,
        test_evaluations: List[TestEvaluation],
        static_results: List[StaticCheckResult],
        marking: Optional[MarkingScheme] = None
    ) -> LLMReview:
        """
        Submits code + objective execution facts to Gemini for a balanced qualitative review.
        """
        start_time = time.time()

        if not self.api_key:
            return LLMReview(
                requirements=[],
                algorithmic_assessment=AlgorithmicAssessment.NOT_EVALUATED,
                readability=QualitativeRating.NOT_EVALUATED,
                variable_naming=QualitativeRating.NOT_EVALUATED,
                issues=["LLM review skipped: No Gemini API key provided."],
                manual_review=False,
                raw_response=None,
                duration_sec=0.0,
                error="NO_API_KEY"
            )

        prompt = self._build_review_prompt(
            question=question,
            source_code=source_code,
            compiler_result=compiler_result,
            test_evaluations=test_evaluations,
            static_results=static_results,
            marking=marking or MarkingScheme()
        )

        raw_json, error_msg = self._call_gemini_api_with_retry(prompt)
        duration = time.time() - start_time

        if error_msg or not raw_json:
            return LLMReview(
                requirements=[],
                algorithmic_assessment=AlgorithmicAssessment.NOT_EVALUATED,
                readability=QualitativeRating.NOT_EVALUATED,
                variable_naming=QualitativeRating.NOT_EVALUATED,
                issues=[f"LLM API failure: {error_msg}"],
                manual_review=False,
                raw_response=raw_json,
                duration_sec=duration,
                error=error_msg or "EMPTY_RESPONSE"
            )

        try:
            return self._parse_llm_json_response(raw_json, duration, self.last_model_used or self.model,
                                                 marking=marking or MarkingScheme())
        except Exception as e:
            return LLMReview(
                issues=[f"Could not interpret LLM review: {e}"],
                raw_response=raw_json,
                duration_sec=duration,
                model_used=self.last_model_used or self.model,
                error="PARSE_ERROR"
            )

    def _build_review_prompt(
        self,
        question: QuestionConfig,
        source_code: str,
        compiler_result: CompilerResult,
        test_evaluations: List[TestEvaluation],
        static_results: List[StaticCheckResult],
        marking: Optional[MarkingScheme] = None
    ) -> str:
        """Constructs the prompt adhering strictly to TA assistant guidelines."""
        m = marking or MarkingScheme()

        def fmt(x: float) -> str:
            return f"{round(x * 2) / 2:g}"

        # Format tests summary
        passed_tests = sum(1 for t in test_evaluations if t.passed)
        total_tests = len(test_evaluations)
        tests_summary = f"{passed_tests}/{total_tests} tests passed."
        if test_evaluations:
            test_details = "\n".join([f"  - [{t.status.value}] {t.description}: {t.diff_summary or 'OK'}" for t in test_evaluations])
        else:
            test_details = "  - No tests run."

        # Format static analysis summary
        static_summary = "\n".join([f"  - [{s.status.value}] {s.description}: {s.message}" for s in static_results])

        # Format requirements
        rubric_summary = "\n".join([f"  - {r.name}: {r.description}" for r in question.rubric])

        system_instructions = (
            "You are assisting a human Teaching Assistant (TA) in grading student lab assignments for a beginner/intermediate OOPs / C / C++ course.\n"
            "CRITICAL RULES & EVALUATION COMPONENTS:\n"
            "You must strictly evaluate the submission across the following 4 Components:\n"
            "  • Comp 1 (Coding conventions & style): Coding conventions, variable naming, function naming, coding style, indentation.\n"
            "  • Comp 2 (Basic test cases & expected output): Basic test execution, expected output accuracy.\n"
            "  • Comp 3 (Logic & Border cases): Core logic, algorithm correctness, border/edge cases handling, input validation.\n"
            "  • Comp 4 (Comments & Submission): Proper comments, function/header docstrings, submission structure.\n"
            "  • Extra Comments: Additional TA observations or flaw hypotheses.\n"
            "\n"
            "GRADING PHILOSOPHY:\n"
            "1. Your scores are SUGGESTIONS only; the TA reviews them and makes all final grading decisions.\n"
            "2. Be fair, balanced, constructive, and realistic for beginner students. Do NOT exaggerate minor typos, small formatting quirks, or harmless imperfections.\n"
            "3. Do not require a specific algorithm (bubble sort, selection sort, insertion sort, etc. are all algorithmically valid unless the rubric explicitly mandates one).\n"
            "4. Distinguish objective facts (compilation, automated tests, static analysis) from your qualitative observations.\n"
            "5. If you are uncertain about something, explicitly say UNCERTAIN.\n"
            "6. Keep reasons extremely concise (1-2 short sentences max). Do NOT produce essays or generic praise.\n"
            "7. You MUST return ONLY valid JSON matching the exact schema specified below.\n"
            "\n"
            "SCORING ANCHORS (use multiples of 0.5; apply exactly the same standard to every student):\n"
            f"  Comp 1 score, 0 to {fmt(m.comp1_max)}:\n"
            f"    {fmt(m.comp1_max)} = consistent indentation, meaningful variable and function names, readable layout.\n"
            f"    {fmt(m.comp1_max * 2 / 3)} = mostly clean with a few inconsistencies or vague names.\n"
            f"    {fmt(m.comp1_max / 3)} = hard to follow: poor names (a, b, x1), messy or missing indentation.\n"
            "    0 = unreadable, or no meaningful code submitted.\n"
            f"  Comp 3 score, 0 to {fmt(m.comp3_max)} (logic, algorithm, border cases, input validation; use the test results as evidence):\n"
            f"    {fmt(m.comp3_max)} = correct logic, handles edge cases (zero, negatives, single element, invalid input) and validates input.\n"
            f"    {fmt(m.comp3_max * 2 / 3)} = core logic correct but misses some edge cases or validation.\n"
            f"    {fmt(m.comp3_max / 3)} = partially correct logic or significant bugs.\n"
            "    0 = logic missing or fundamentally wrong.\n"
            "    If the code does not compile, judge the logic as written but give at most two thirds of the maximum.\n"
            f"  Comp 4 score, 0 to {fmt(m.comp4_max)} (comments only; timeliness is handled separately, ignore it):\n"
            f"    {fmt(m.comp4_max)} = file/function header comments plus comments explaining non-obvious logic.\n"
            f"    {fmt(m.comp4_max * 2 / 3)} = some useful comments.\n"
            f"    {fmt(m.comp4_max / 3)} = very few or trivial comments.\n"
            "    0 = no comments.\n"
            "  Comp 2 is computed from the automated tests; do not score it.\n"
        )

        json_schema_spec = (
            "{\n"
            '  "comp1_style_conventions": {\n'
            '    "rating": "GOOD | FAIR | POOR",\n'
            f'    "score": <number 0 to {fmt(m.comp1_max)}>,\n'
            '    "feedback": "Concise notes on coding conventions, variable/function naming, coding style, and indentation"\n'
            '  },\n'
            '  "comp2_basic_tests": {\n'
            '    "status": "PASS | FAIL | UNCERTAIN",\n'
            '    "feedback": "Summary of basic test cases and expected output matching"\n'
            '  },\n'
            '  "comp3_logic_edge_cases": {\n'
            '    "rating": "GOOD | FAIR | POOR",\n'
            f'    "score": <number 0 to {fmt(m.comp3_max)}>,\n'
            '    "feedback": "Assessment of core logic, algorithm efficiency, border/edge cases, and validation"\n'
            '  },\n'
            '  "comp4_comments_submission": {\n'
            '    "rating": "GOOD | FAIR | POOR",\n'
            f'    "score": <number 0 to {fmt(m.comp4_max)}>,\n'
            '    "feedback": "Assessment of proper comments, header documentation, and submission cleanliness"\n'
            '  },\n'
            '  "extra_comments": ["Concise TA observation 1", "Concise TA observation 2"],\n'
            '  "requirements": [\n'
            '    {\n'
            '      "requirement": "Requirement name from rubric",\n'
            '      "status": "PASS | FAIL | UNCERTAIN",\n'
            '      "reason": "1-line concise reason"\n'
            '    }\n'
            '  ],\n'
            '  "algorithmic_assessment": "CORRECT | POSSIBLY_WRONG | WRONG | UNCERTAIN",\n'
            '  "readability": "GOOD | FAIR | POOR",\n'
            '  "variable_naming": "GOOD | FAIR | POOR",\n'
            '  "issues": ["short issue 1", "short issue 2"],\n'
            '  "manual_review": true_or_false,\n'
            '  "manual_review_reason": "short explanation if manual review is recommended, else null"\n'
            "}\n"
        )

        prompt = f"""{system_instructions}

### ASSIGNMENT CONTEXT:
Question: {question.name} ({question.id})
Language: {question.language.value.upper()}
Description: {question.description}

### RUBRIC REQUIREMENTS:
{rubric_summary}

### OBJECTIVE AUTOMATED RESULTS:
- Compilation: {"SUCCESS" if compiler_result.success else "FAILED"}
- Concise Compiler Log: {compiler_result.concise_error or "Clean compilation"}
- Automated Tests: {tests_summary}
{test_details}
- Static Analysis Findings:
{static_summary}

### STUDENT SOURCE CODE:
```cpp
{source_code[:min(12000, context_budget_chars())]}
```

### REQUIRED JSON OUTPUT SCHEMA:
{json_schema_spec}

Respond ONLY with the JSON object.
"""
        return prompt

    def synthesize_test_driver(
        self,
        question: QuestionConfig,
        source_files_map: Dict[str, str],
        student_id: str = "student",
        lang_ext: Optional[str] = None,
        feedback: Optional[Tuple[str, str]] = None
    ) -> Optional[str]:
        """
        Analyzes ALL of the student's C/C++ files and synthesizes a complete replacement
        main() file that hardcodes test inputs as direct function calls.

        The driver reads a single integer from stdin (the test case number: 1, 2, 3...)
        and executes the corresponding test by calling the student's functions directly
        with hardcoded arguments. This completely bypasses stdin/scanf issues and
        hardcoded values in the student's original main.

        Args:
            question: The question config with test cases.
            source_files_map: Dict mapping filename -> file contents for every student file.
            student_id: Student identifier for naming.

        Returns:
            The generated driver source code string, or None on failure.
        """
        if not self.api_key or not question.test_cases:
            return None

        # Build test cases description with explicit numbering
        tc_descriptions = []
        for idx, tc in enumerate(question.test_cases[:10], 1):
            tc_descriptions.append(
                f"Test #{idx} (ID: {tc.id}, {tc.description or 'Case'}):\n"
                f"  INPUTS / WHAT TO CALL:\n{tc.input_data}\n"
                f"  EXPECTED OUTPUT (stdout):\n{tc.expected_display or tc.expected_output}"
            )
        tc_text = "\n\n".join(tc_descriptions)
        num_tests = min(len(question.test_cases), 10)

        # Build per-file source listing so the LLM sees each filename
        file_listings = []
        total_chars = 0
        for filename, content in source_files_map.items():
            # A repair request also carries the previous driver and its errors, so it gets less student source
            budget = context_budget_chars() - (3500 if feedback else 0) - total_chars
            if budget <= 200:
                file_listings.append(f"// === File: {filename} === (truncated due to length)")
                break
            # Comments and blank lines cost tokens without helping the driver call the right functions
            content = re.sub(r"\n\s*\n+", "\n", re.sub(r"/\*.*?\*/|//[^\n]*", "", content, flags=re.DOTALL))
            snippet = content[:budget]
            file_listings.append(f"// === File: {filename} ===\n{snippet}")
            total_chars += len(snippet)
        files_block = "\n\n".join(file_listings)

        # The driver must be written in the language the student actually used (a "C" question
        # answered with C++ classes needs a C++ driver), so the caller passes the detected language.
        lang_ext = lang_ext or ("cpp" if question.language.value.lower() == "cpp" else "c")

        # Detect if student code defines functions that conflict with stdlib (e.g. div, abs)
        has_stdlib_conflict = False
        stdlib_conflict_names = []
        for fname, fcontent in source_files_map.items():
            for conflict_name in ["div", "abs", "exit", "free", "malloc"]:
                if re.search(rf"\b(?:int|void|float|double|struct\s+\w+|\w+)\s+{conflict_name}\s*\(", fcontent):
                    has_stdlib_conflict = True
                    if conflict_name not in stdlib_conflict_names:
                        stdlib_conflict_names.append(conflict_name)

        stdlib_warning = ""
        if has_stdlib_conflict:
            stdlib_warning = f"""
IMPORTANT WARNING: The student's code defines function(s) named: {', '.join(stdlib_conflict_names)}
These names CONFLICT with C standard library functions in <stdlib.h>.
You MUST NOT #include <stdlib.h> in your driver. If you need malloc/free, use <stdlib.h> BUT
only if the student does NOT redefine those. For `div`, `abs` etc. — do NOT include <stdlib.h>.
"""

        prompt = f"""You are an expert C/C++ testing engineer. You must write a REPLACEMENT main() file for an automated grading system.

CRITICAL CONTEXT:
- The student submitted multiple source files (.c/.cpp and .h files) for a lab assignment.
- Their own main() file will be COMPLETELY EXCLUDED from compilation.
- Only their library/implementation files (non-main .c/.cpp files) and header files will be compiled alongside YOUR driver.
- YOUR file is the ONLY file that will contain main().
- ALL of the student's .c/.cpp files (except their main) and .h files will be linked together with your driver.
{stdlib_warning}
YOUR TASK: Generate a complete, compilable `ta_test_driver.{lang_ext}` file that:

1. **Includes headers carefully**:
   - Include <stdio.h> (always needed for printf).
   - Include <stdbool.h>, <string.h>, <math.h> ONLY if needed.
   - ONLY include <stdlib.h> if the student does NOT define conflicting functions like div() or abs().
   - Include the student's own .h header files using quotes: e.g. #include "rational.h"
   - The student's headers WILL be found by the compiler (include paths are configured).

2. **Declares/extern-declares the student's functions**:
   - Read ALL the student's .c/.cpp files below to find every function they implemented.
   - Forward-declare or extern-declare each function with its EXACT signature from their code.
   - If structs/typedefs are defined in their .h headers, just include those headers — do NOT redeclare them.

3. **Implements int main() using a TEST CASE INDEX approach**:
   - The driver reads a SINGLE integer from stdin: the test case number (1 to {num_tests}).
   - Based on this number, it runs the corresponding test case using a switch or if-else chain.
   - For EACH test case, the inputs are HARDCODED as local variables or literals — DO NOT read them from stdin.
   - The driver directly calls the student's functions with the hardcoded input values.
   - The driver prints the output to stdout in the EXACT expected format shown in the test cases.
   - After printing, call fflush(stdout) and return 0.

   EXAMPLE STRUCTURE (adapt to actual student functions):
   ```
   int main() {{
       int test_num;
       scanf("%d", &test_num);

       if (test_num == 1) {{
           // Test #1: hardcoded inputs
           int a = 5, b = 3;
           int result = student_add(a, b);
           printf("%d\\n", result);
       }} else if (test_num == 2) {{
           // Test #2: hardcoded inputs
           int a = -2, b = 10;
           int result = student_add(a, b);
           printf("%d\\n", result);
       }}
       fflush(stdout);
       return 0;
   }}
   ```

4. **HARD RULES**:
   - NEVER use while(1), for(;;), or any infinite loop.
   - NEVER use while(scanf(...)) or while(cin >>).
   - The ONLY scanf/cin call should be reading the single test case number.
   - All test data (numbers, strings, arrays) must be HARDCODED as constants/literals.
   - The program MUST terminate after printing.
   - Do NOT redefine any struct/function/typedef that already exists in the student's header files.
   - Parse the "ORIGINAL INPUT" for each test case to understand what values the student's functions expect as arguments.
   - Match the EXPECTED OUTPUT format EXACTLY (same labels, same spacing, same newlines). The labels and layout
     (e.g. "Prime: YES", "Sum: ") are YOURS to print; only the values/decisions come from the student's code.
     So for a yes/no check print `Prime: ` followed by YES or NO from the student's function result.
   - NEVER special-case inputs, ranges or values to make the output match (no "if range is 1-30 use 31",
     no hard-coded answers). Pass exactly the inputs given; a wrong student answer must stay wrong.
   - NEVER implement the student's logic yourself. If the student has no function/class/operator for something a
     test needs, print the line as `MISSING: <what is missing>` instead. Only call what the student wrote.
   - If a student's function prints its result instead of returning it, call it and let it print; do not re-print.
   - Print fractions exactly as the student's code produces them (do NOT simplify them yourself).
   - If the student's code only takes data through an input function (reading a file, cin, or scanf), feed it:
     write the values to a file in the current directory before calling it, or replace cin's buffer with a
     std::istringstream holding the values. Keep using the student's own functions.
   - If the test data has to go into private/protected members and the class offers no constructor, setter or
     input function for it (e.g. it only fills itself with rand()), put `#define private public` and
     `#define protected public` BEFORE including the student's headers and set the members directly.
   - Functions defined in the student's main file are available too (their main() is renamed and linked in).
   - Write compact code with no explanatory comments or reasoning inside the file.

### QUESTION CONTEXT:
Question: {question.name}
Description: {question.description}

### TEST CASES (your main reads a test number 1-{num_tests} and runs the corresponding case):
{tc_text}

### ALL STUDENT FILES (read carefully to understand their function signatures and struct definitions):
{files_block}

Output ONLY valid compilable {lang_ext.upper()} code inside a single ```{lang_ext} ... ``` block. No explanations.
Generate the test driver now:
"""
        if feedback:
            previous, errors = feedback
            prompt += f"""
### YOUR PREVIOUS DRIVER WAS REJECTED (see the errors below). Fix it (same rules as above).
Previous driver:
```{lang_ext}
{previous[:2500]}
```
Compiler/linker errors:
{errors[:1000]}
"""

        self.last_driver_error = None
        raw_text, err = self._call_gemini_api_with_retry(prompt, response_mime="text/plain")
        if not raw_text or err:
            self.last_driver_error = err or "Empty response"
            return None

        driver_code = self.extract_code_block(raw_text)
        if re.search(r"\bmain\s*\(", driver_code):
            return driver_code
        self.last_driver_error = "LLM response did not contain a main() function."
        return None

    @staticmethod
    def extract_code_block(raw_text: str) -> str:
        """
        Pulls source code out of a fenced LLM response.
        The language tag is matched as a whole word up to the end of the fence line; the old
        pattern matched ```c and left 'pp' from ```cpp as the first line of every C++ driver.
        """
        cleaned = raw_text.strip()
        match = re.search(r"```[ \t]*([A-Za-z0-9+#_-]*)[ \t]*\r?\n(.*?)```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(2)
        elif cleaned.startswith("```"):
            cleaned = re.sub(r"^```[^\n]*\n?", "", cleaned)
            cleaned = re.sub(r"```\s*$", "", cleaned)
        return GeminiReviewer.strip_stray_language_tag(cleaned.strip())

    @staticmethod
    def strip_stray_language_tag(code: str) -> str:
        """Removes a leftover language tag line (e.g. 'pp', 'cpp', 'c++') from the top of driver code."""
        lines = code.splitlines()
        if lines and lines[0].strip().lower() in ("pp", "cpp", "c", "c++", "cc", "cxx"):
            lines = lines[1:]
        return "\n".join(lines).strip() + "\n"

    def _call_gemini_api_with_retry(
        self,
        prompt: str,
        response_mime: str = "application/json"
    ) -> Tuple[Optional[str], Optional[str]]:
        """Calls Gemini via the shared client. Returns (text, error_message)."""
        is_driver = response_mime == "text/plain"
        text, err, model_used = call_llm(
            prompt,
            model=self.model,
            json_mode=not is_driver,
            # Reviews feed suggested marks: temperature 0 keeps the same code scoring the same way
            temperature=0.2 if is_driver else 0.0,
            max_output_tokens=16384 if is_driver else 8192,
            timeout_sec=120.0 if is_driver else 60.0,
        )
        if model_used:
            self.last_model_used = model_used
        return text, err

    def _parse_llm_json_response(self, raw_text: str, duration: float, model: str,
                                 marking: Optional[MarkingScheme] = None) -> LLMReview:
        """Cleans and parses raw JSON output from LLM."""
        cleaned = raw_text.strip()
        # Remove markdown fences if present
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Fallback: extract first {...} block
            match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                except Exception:
                    return LLMReview(
                        issues=["Failed to parse LLM structured JSON response."],
                        manual_review=True,
                        manual_review_reason="Malformed LLM JSON response",
                        raw_response=raw_text,
                        duration_sec=duration,
                        model_used=model,
                        error="JSON_PARSE_ERROR"
                    )
            else:
                return LLMReview(
                    issues=["No valid JSON object found in LLM response."],
                    manual_review=True,
                    manual_review_reason="No JSON found in LLM response",
                    raw_response=raw_text,
                    duration_sec=duration,
                    model_used=model,
                    error="NO_JSON_FOUND"
                )

        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0]
        if not isinstance(data, dict):
            raise ValueError("LLM returned JSON that is not an object")

        def as_dict(v: Any) -> Dict[str, Any]:
            return v if isinstance(v, dict) else {}

        def as_str_list(v: Any) -> List[str]:
            if isinstance(v, str):
                return [v] if v.strip() else []
            if isinstance(v, list):
                return [str(x) for x in v if x not in (None, "")]
            return []

        # Build LLMReview
        req_items = []
        for r in data.get("requirements") or []:
            if not isinstance(r, dict):
                continue
            st = str(r.get("status") or "UNCERTAIN").upper()
            status_enum = CheckStatus.UNCERTAIN
            if "PASS" in st:
                status_enum = CheckStatus.PASS
            elif "FAIL" in st:
                status_enum = CheckStatus.FAIL

            req_items.append(LLMReviewItem(
                requirement=str(r.get("requirement") or ""),
                status=status_enum,
                reason=str(r.get("reason") or "")
            ))

        algo_raw = str(data.get("algorithmic_assessment") or "UNCERTAIN").upper()
        algo_assessment = AlgorithmicAssessment.UNCERTAIN
        for opt in AlgorithmicAssessment:
            if opt.value == algo_raw:
                algo_assessment = opt
                break

        readability_raw = str(data.get("readability") or "FAIR").upper()
        readability = QualitativeRating.FAIR
        for opt in QualitativeRating:
            if opt.value == readability_raw:
                readability = opt
                break

        naming_raw = str(data.get("variable_naming") or "FAIR").upper()
        naming = QualitativeRating.FAIR
        for opt in QualitativeRating:
            if opt.value == naming_raw:
                naming = opt
                break

        def parse_rating(raw_val: Any) -> QualitativeRating:
            if not isinstance(raw_val, str):
                return QualitativeRating.FAIR
            val = raw_val.upper()
            for opt in QualitativeRating:
                if opt.value == val:
                    return opt
            return QualitativeRating.FAIR

        def parse_status(raw_val: Any) -> CheckStatus:
            if not isinstance(raw_val, str):
                return CheckStatus.PASS
            val = raw_val.upper()
            if "PASS" in val:
                return CheckStatus.PASS
            if "FAIL" in val:
                return CheckStatus.FAIL
            return CheckStatus.UNCERTAIN

        # Parse 4 Components
        comp1_raw = as_dict(data.get("comp1_style_conventions"))
        m = marking or MarkingScheme()

        def parse_score(raw: Dict[str, Any], max_score: float) -> Optional[float]:
            try:
                value = float(raw.get("score"))
            except (TypeError, ValueError):
                return None
            return round(min(max(value, 0.0), max_score) * 2) / 2

        comp1 = ComponentDetail(
            score=parse_score(comp1_raw, m.comp1_max), max_score=m.comp1_max,
            rating=parse_rating(comp1_raw.get("rating", naming_raw)),
            status=parse_status(comp1_raw.get("status", "PASS")),
            feedback=str(comp1_raw.get("feedback") or "Coding conventions, naming and indentation")
        )

        comp2_raw = as_dict(data.get("comp2_basic_tests"))
        comp2 = ComponentDetail(
            rating=parse_rating(comp2_raw.get("rating", "GOOD")),
            status=parse_status(comp2_raw.get("status", "PASS")),
            feedback=str(comp2_raw.get("feedback") or "Basic test cases and output matching")
        )

        comp3_raw = as_dict(data.get("comp3_logic_edge_cases"))
        comp3 = ComponentDetail(
            score=parse_score(comp3_raw, m.comp3_max), max_score=m.comp3_max,
            rating=parse_rating(comp3_raw.get("rating", "GOOD")),
            status=parse_status(comp3_raw.get("status", "PASS")),
            feedback=str(comp3_raw.get("feedback") or "Core logic, algorithm, and border cases")
        )

        comp4_raw = as_dict(data.get("comp4_comments_submission"))
        comp4 = ComponentDetail(
            score=parse_score(comp4_raw, m.comp4_max), max_score=m.comp4_max,
            rating=parse_rating(comp4_raw.get("rating", "GOOD")),
            status=parse_status(comp4_raw.get("status", "PASS")),
            feedback=str(comp4_raw.get("feedback") or "Comments, documentation and submission cleanliness")
        )

        extra_comments = as_str_list(data.get("extra_comments"))

        components = EvaluationComponents(
            comp1_style_conventions=comp1,
            comp2_basic_tests=comp2,
            comp3_logic_edge_cases=comp3,
            comp4_comments_submission=comp4,
            extra_comments=extra_comments
        )

        return LLMReview(
            requirements=req_items,
            algorithmic_assessment=algo_assessment,
            readability=readability,
            variable_naming=naming,
            components=components,
            issues=as_str_list(data.get("issues")),
            manual_review=bool(data.get("manual_review", False)),
            manual_review_reason=(str(data["manual_review_reason"]) if data.get("manual_review_reason") else None),
            raw_response=raw_text,
            duration_sec=duration,
            model_used=model
        )
