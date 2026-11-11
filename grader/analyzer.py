"""
Deterministic Layer B Static Analyzer for C and C++ source code.
Inspects templates, function overloading, function pointers vs direct if/else branching,
storage classes, scopes, const pointer qualifiers, and forbidden constructs.
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set

from grader.models import (
    StaticCheckRule,
    StaticCheckResult,
    CheckStatus,
    LanguageType,
)


def _snippets(matches: list, limit: int = 3) -> List[str]:
    """re.findall returns tuples when a pattern has several groups; snippets must be strings."""
    out = []
    for m in matches[:limit]:
        out.append(m if isinstance(m, str) else " ".join(x for x in m if x))
    return out


class StaticAnalyzer:
    """Performs deterministic static and structural analysis on C/C++ source code."""

    @staticmethod
    def analyze_files(
        source_files: List[Path],
        rules: List[StaticCheckRule],
        expected_language: LanguageType = LanguageType.C
    ) -> List[StaticCheckResult]:
        """Runs all specified static check rules across the given source files."""
        if not source_files:
            return [
                StaticCheckResult(
                    rule_id="missing_source",
                    rule_type="file_check",
                    description="Source code availability",
                    passed=False,
                    status=CheckStatus.FAIL,
                    message="No source files provided for static analysis."
                )
            ]

        # Read and merge source content with comments stripped
        combined_raw = ""
        combined_clean = ""
        for sf in source_files:
            try:
                with open(sf, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                    combined_raw += f"\n/* File: {sf.name} */\n" + content
                    combined_clean += f"\n/* File: {sf.name} */\n" + StaticAnalyzer.strip_comments_and_strings(content)
            except Exception:
                pass

        results: List[StaticCheckResult] = []

        # Run configured rules
        for rule in rules:
            if rule.rule_type.lower() == "separate_interface":
                results.append(StaticAnalyzer._check_separate_interface(rule, source_files))
                continue
            try:
                res = StaticAnalyzer._evaluate_rule(rule, combined_clean, combined_raw)
            except re.error as e:
                res = StaticCheckResult(
                    rule_id=rule.rule_id, rule_type=rule.rule_type, description=rule.description,
                    passed=False, status=CheckStatus.UNCERTAIN,
                    message=f"Rule has an invalid regular expression ({e}); fix it in the assignment config."
                )
            results.append(res)

        # Run language sanity check if applicable
        lang_check = StaticAnalyzer._check_language_appropriateness(combined_clean, expected_language)
        if lang_check:
            results.append(lang_check)

        return results

    @staticmethod
    def _check_separate_interface(rule: StaticCheckRule, source_files: List[Path]) -> StaticCheckResult:
        """Interface in a header (class declared there), implementation in a .cpp (Class::method defined there)."""
        headers, impls = [], []
        for f in source_files:
            try:
                text = StaticAnalyzer.strip_comments_and_strings(Path(f).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if Path(f).suffix.lower() in (".h", ".hpp") and re.search(r"\b(?:class|struct)\s+\w+", text):
                headers.append(Path(f).name)
            elif Path(f).suffix.lower() in (".cpp", ".cc", ".cxx", ".c") and re.search(r"\b\w+\s*::\s*~?\w+\s*\(", text):
                impls.append(Path(f).name)
        passed = bool(headers and impls)
        status = CheckStatus.PASS if passed else (CheckStatus.PARTIAL if headers else CheckStatus.FAIL)
        msg = (f"Interface in {', '.join(headers[:3])}; implementation in {', '.join(impls[:3])}." if passed
               else "Class declared in a header, but methods are not defined in a separate .cpp file." if headers
               else "No header declaring the class: interface and implementation are not separated.")
        return StaticCheckResult(rule_id=rule.rule_id, rule_type=rule.rule_type, description=rule.description,
                                 passed=passed, status=status, message=msg, matched_snippets=headers[:2] + impls[:2])

    @staticmethod
    def strip_comments_and_strings(source: str) -> str:
        """Removes C/C++ comments (/* */ and //) and string/char literals while preserving line breaks."""
        def replacer(match):
            s = match.group(0)
            if s.startswith("/"):
                # Return newlines to preserve line numbering
                return "\n" * s.count("\n")
            else:
                # String or char literal: replace with empty string literal
                return '""'

        pattern = re.compile(
            r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
            re.DOTALL | re.MULTILINE
        )
        return re.sub(pattern, replacer, source)

    @staticmethod
    def _evaluate_rule(rule: StaticCheckRule, clean_code: str, raw_code: str) -> StaticCheckResult:
        rtype = rule.rule_type.lower()

        if rtype == "template_declaration":
            return StaticAnalyzer._check_template_declaration(rule, clean_code)
        elif rtype == "function_overloading":
            return StaticAnalyzer._check_function_overloading(rule, clean_code)
        elif rtype == "function_pointer":
            return StaticAnalyzer._check_function_pointer(rule, clean_code)
        elif rtype == "storage_classes":
            return StaticAnalyzer._check_storage_classes(rule, clean_code)
        elif rtype == "const_pointers":
            return StaticAnalyzer._check_const_pointers(rule, clean_code)
        elif rtype == "forbidden_regex":
            return StaticAnalyzer._check_forbidden_regex(rule, clean_code)
        elif rtype == "contains_regex":
            return StaticAnalyzer._check_contains_regex(rule, clean_code)
        else:
            # Generic token / pattern check
            if rule.pattern:
                matches = re.findall(rule.pattern, clean_code)
                passed = len(matches) >= rule.min_count
                return StaticCheckResult(
                    rule_id=rule.rule_id,
                    rule_type=rule.rule_type,
                    description=rule.description,
                    passed=passed,
                    status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                    message=f"Found {len(matches)} occurrences of pattern." if passed else "Required pattern not found.",
                    matched_snippets=_snippets(matches)
                )

        return StaticCheckResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            description=rule.description,
            passed=True,
            status=CheckStatus.PASS,
            message="Rule passed."
        )

    @staticmethod
    def _check_template_declaration(rule: StaticCheckRule, code: str) -> StaticCheckResult:
        """Checks for template <typename T> or template <class T> declarations."""
        template_pattern = r"template\s*<\s*(?:typename|class)\s+(\w+)\s*>"
        matches = re.findall(template_pattern, code)

        if matches:
            return StaticCheckResult(
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                description=rule.description,
                passed=True,
                status=CheckStatus.PASS,
                message=f"Function template declaration detected with type parameter(s): {', '.join(matches)}",
                matched_snippets=[f"template <typename {m}>" for m in matches]
            )

        return StaticCheckResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            description=rule.description,
            passed=False,
            status=CheckStatus.FAIL,
            message="No template <typename T> or template <class T> declaration found in source code."
        )

    @staticmethod
    def _check_function_overloading(rule: StaticCheckRule, code: str) -> StaticCheckResult:
        """Checks for multiple overloaded functions with the same identifier in C++."""
        # Find function definitions: return_type func_name ( params )
        fn_pattern = r"\b(?:void|int|float|double|char|bool)\s+(\w+)\s*\(([^)]*)\)\s*\{"
        matches = re.findall(fn_pattern, code)

        fn_groups: Dict[str, List[str]] = {}
        for name, params in matches:
            if name in ("main", "sizeof"):
                continue
            fn_groups.setdefault(name, []).append(params.strip())

        # Find any function name with >= 2 distinct signatures
        overloaded = {k: v for k, v in fn_groups.items() if len(v) >= 2}

        if overloaded:
            snippets = [f"{k}({', '.join(v)})" for k, v in overloaded.items()]
            return StaticCheckResult(
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                description=rule.description,
                passed=True,
                status=CheckStatus.PASS,
                message=f"Detected overloaded function(s): {', '.join(overloaded.keys())} ({len(snippets)} variants)",
                matched_snippets=snippets[:3]
            )

        return StaticCheckResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            description=rule.description,
            passed=False,
            status=CheckStatus.FAIL,
            message="No overloaded functions detected with identical names and distinct signatures."
        )

    @staticmethod
    def _check_function_pointer(rule: StaticCheckRule, code: str) -> StaticCheckResult:
        """
        Distinguishes between:
        1. Actually declaring and using a function pointer to select sort / comparator at runtime.
        2. Merely using if/else to call two separate functions directly.
        """
        # Pattern 1: Function pointer declaration (e.g., void (*fptr)(...), int (*cmp)(int, int))
        decl_pattern = r"\b(?:void|int|bool|float|double|\w+)\s*\(\s*\*\s*(\w+)\s*\)\s*\([^)]*\)"
        decl_matches = re.findall(decl_pattern, code)

        # Pattern 2: Typedef function pointer (e.g., typedef void (*SortFunc)(...))
        typedef_pattern = r"typedef\s+\w+\s*\(\s*\*\s*(\w+)\s*\)\s*\([^)]*\)"
        typedef_matches = re.findall(typedef_pattern, code)

        # Pattern 3/4: the declared pointer is assigned (fptr = sortAsc / = &sortAsc, or initialised),
        # passed along, or called (fptr(...) / (*fptr)(...)). Any unrelated `x = y;` does not count.
        pointer_names = set(decl_matches + typedef_matches)
        # Look for usage outside the declarations themselves (a bare declaration looks like a call)
        usage_code = re.sub(typedef_pattern, " ", re.sub(r"\b(?:void|int|bool|float|double|char|long|short|unsigned)\s*\*?\s*\(\s*\*\s*\w+\s*\)\s*\([^)]*\)\s*;", " ", code))
        has_usage = False
        for name in pointer_names:
            n = re.escape(name)
            if (re.search(rf"\b{n}\s*=\s*&?\s*[A-Za-z_]\w*\s*[;,)]", code)
                    or re.search(rf"(?:\(\s*\*\s*{n}\s*\)|\b{n})\s*\([^;]*\)\s*;", usage_code)):
                has_usage = True
                break

        has_decl = bool(decl_matches or typedef_matches)

        if has_decl and has_usage:
            snippets = [f"(*{m})()" for m in decl_matches + typedef_matches]
            return StaticCheckResult(
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                description=rule.description,
                passed=True,
                status=CheckStatus.PASS,
                message=f"Function pointer detected and utilized for runtime dispatch: {', '.join(decl_matches or typedef_matches)}",
                matched_snippets=snippets[:3]
            )
        elif has_decl:
            return StaticCheckResult(
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                description=rule.description,
                passed=True,
                status=CheckStatus.UNCERTAIN,
                message="Function pointer declaration found, but dynamic assignment/invocation is uncertain.",
                matched_snippets=[f"(*{m})()" for m in decl_matches + typedef_matches]
            )

        # Check if code uses plain if/else direct call
        if_else_call = re.search(r"if\s*\([^)]+\)\s*\{?\s*(?:sort|bubble|asc)[^;]+;\s*\}?\s*else", code, re.IGNORECASE)
        if if_else_call:
            return StaticCheckResult(
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                description=rule.description,
                passed=False,
                status=CheckStatus.FAIL,
                message="Code appears to use direct if/else branching to call sort functions rather than a function pointer."
            )

        return StaticCheckResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            description=rule.description,
            passed=False,
            status=CheckStatus.FAIL,
            message="No function pointer declaration or usage detected."
        )

    @staticmethod
    def _check_storage_classes(rule: StaticCheckRule, code: str) -> StaticCheckResult:
        """Inspects static, extern, register, and auto keywords and scope blocks."""
        keywords = ["static", "extern", "register", "auto"]
        found: Dict[str, List[str]] = {}

        for kw in keywords:
            matches = re.findall(rf"\b{kw}\b\s+[^;{{]+", code)
            if matches:
                found[kw] = matches

        score = len(found)
        snippets = [f"{k}: {v[0].strip()}" for k, v in found.items()]

        if score >= 2:
            status = CheckStatus.PASS if score >= 3 else CheckStatus.PARTIAL
            return StaticCheckResult(
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                description=rule.description,
                passed=(score >= 2),
                status=status,
                message=f"Demonstrated storage classes ({', '.join(found.keys())})",
                matched_snippets=snippets
            )

        return StaticCheckResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            description=rule.description,
            passed=False,
            status=CheckStatus.FAIL,
            message=f"Insufficient storage class demonstration (found only: {', '.join(found.keys()) if found else 'none'})."
        )

    @staticmethod
    def _check_const_pointers(rule: StaticCheckRule, code: str) -> StaticCheckResult:
        """Inspects pointer-to-const, const-pointer, and const-pointer-to-const variations."""
        # 1. Pointer to const (e.g. const int *ptr or int const *ptr)
        ptr_to_const = re.findall(r"(?:const\s+\w+\s*\*|\w+\s+const\s*\*)\s*(?!const\b)(\w+)", code)

        # 2. Const pointer (e.g. int * const ptr)
        const_ptr = re.findall(r"(?<!const\s)\b\w+\s*\*\s*const\s+(\w+)", code)

        # 3. Const pointer to const (e.g. const int * const ptr)
        const_ptr_to_const = re.findall(r"const\s+\w+\s*\*\s*const\s+(\w+)", code)

        details = []
        if ptr_to_const:
            details.append(f"pointer-to-const ({len(ptr_to_const)})")
        if const_ptr:
            details.append(f"const-pointer ({len(const_ptr)})")
        if const_ptr_to_const:
            details.append(f"const-pointer-to-const ({len(const_ptr_to_const)})")

        passed = len(details) >= 2
        return StaticCheckResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            description=rule.description,
            passed=passed,
            status=CheckStatus.PASS if passed else CheckStatus.PARTIAL,
            message=f"Detected const pointer variations: {', '.join(details) if details else 'None'}",
            matched_snippets=details
        )

    @staticmethod
    def _check_forbidden_regex(rule: StaticCheckRule, code: str) -> StaticCheckResult:
        if not rule.forbidden_pattern:
            return StaticCheckResult(rule_id=rule.rule_id, rule_type=rule.rule_type, description=rule.description, passed=True, status=CheckStatus.PASS, message="OK")

        matches = re.findall(rule.forbidden_pattern, code)
        if matches:
            return StaticCheckResult(
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                description=rule.description,
                passed=False,
                status=CheckStatus.FAIL,
                message=f"Forbidden pattern found ({len(matches)} occurrences).",
                matched_snippets=_snippets(matches)
            )

        return StaticCheckResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            description=rule.description,
            passed=True,
            status=CheckStatus.PASS,
            message="No forbidden patterns found."
        )

    @staticmethod
    def _check_contains_regex(rule: StaticCheckRule, code: str) -> StaticCheckResult:
        if not rule.pattern:
            return StaticCheckResult(rule_id=rule.rule_id, rule_type=rule.rule_type, description=rule.description, passed=True, status=CheckStatus.PASS, message="OK")

        matches = re.findall(rule.pattern, code, re.IGNORECASE)
        passed = len(matches) >= rule.min_count
        return StaticCheckResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            description=rule.description,
            passed=passed,
            status=CheckStatus.PASS if passed else CheckStatus.FAIL,
            message=f"Found {len(matches)} match(es) for '{rule.description}'" if passed else f"Required pattern '{rule.description}' not found in source.",
            matched_snippets=_snippets(matches)
        )

    @staticmethod
    def _check_language_appropriateness(code: str, expected: LanguageType) -> Optional[StaticCheckResult]:
        """Checks for language mismatch (e.g. C++ constructs in a pure C assignment)."""
        cpp_features = {
            "<iostream>": r"<iostream>", "cout": r"\bcout\b", "cin": r"\bcin\b", "class": r"\bclass\s+\w+",
            "namespace": r"\bnamespace\b", "template": r"\btemplate\s*<",
        }

        if expected == LanguageType.C:
            found_cpp = [feat for feat, pat in cpp_features.items() if re.search(pat, code)]
            if found_cpp:
                return StaticCheckResult(
                    rule_id="language_mismatch_check",
                    rule_type="language_check",
                    description="Language Check (Expected C)",
                    passed=False,
                    status=CheckStatus.PARTIAL,
                    message=f"Note: File uses C++ features ({', '.join(found_cpp)}) in a C question.",
                    matched_snippets=found_cpp
                )
        return None
