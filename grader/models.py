"""
Data models and schemas for the C/C++ TA Autograder.
"""

from __future__ import annotations
from enum import Enum
from typing import List, Dict, Optional, Any, Union
from pydantic import BaseModel, Field


class ComparisonMode(str, Enum):
    EXACT = "exact"
    WHITESPACE_NORMALIZED = "whitespace_normalized"
    FLOAT_TOLERANCE = "float_tolerance"
    TOKEN_MATCH = "token_match"
    SORTED_CHECK = "sorted_check"
    REGEX_MATCH = "regex_match"
    LINES = "lines"  # every non-blank line must match in order (spacing inside a line ignored); no lenient fallbacks


class LanguageType(str, Enum):
    C = "c"
    CPP = "cpp"
    AUTO = "auto"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    UNCERTAIN = "UNCERTAIN"
    NOT_TESTED = "NOT_TESTED"
    TIMEOUT = "TIMEOUT"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    COMPILATION_ERROR = "COMPILATION_ERROR"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    SKIPPED = "SKIPPED"


class QualitativeRating(str, Enum):
    GOOD = "GOOD"
    FAIR = "FAIR"
    POOR = "POOR"
    NOT_EVALUATED = "NOT_EVALUATED"


class AlgorithmicAssessment(str, Enum):
    CORRECT = "CORRECT"
    POSSIBLY_WRONG = "POSSIBLY_WRONG"
    WRONG = "WRONG"
    UNCERTAIN = "UNCERTAIN"
    NOT_EVALUATED = "NOT_EVALUATED"


class TestCase(BaseModel):
    __test__ = False  # Prevent pytest from treating this as a test class
    id: str
    description: str = ""
    input_data: str = ""
    expected_output: str = ""
    comparison_mode: ComparisonMode = ComparisonMode.WHITESPACE_NORMALIZED
    float_tolerance: float = 1e-4
    timeout_sec: float = 2.0
    is_edge_case: bool = False
    sort_order: Optional[str] = None  # "ascending" | "descending"
    sort_type: Optional[str] = None   # "int" | "float" | "char"
    expected_display: Optional[str] = None  # human-readable expected output when expected_output is a regex


class StaticCheckRule(BaseModel):
    rule_id: str
    rule_type: str  # e.g., "contains_regex", "template_declaration", "function_overloading", "function_pointer", "storage_classes", "const_pointers", "forbidden_regex"
    description: str
    pattern: Optional[str] = None
    forbidden_pattern: Optional[str] = None
    required_tokens: List[str] = Field(default_factory=list)
    min_count: int = 1
    severity: str = "warning"  # "error" | "warning" | "info"


class QuestionRequirement(BaseModel):
    id: str
    name: str
    description: str = ""


class QuestionConfig(BaseModel):
    id: str
    name: str
    description: str = ""
    language: LanguageType = LanguageType.C
    source_hints: List[str] = Field(default_factory=list)  # e.g. ["q1", "question1", "pointer"]
    # Regexes describing what this task's code contains (e.g. "class\s+Complex"); when set, each student
    # program (a main() plus the files it uses) is matched to the task whose keywords it hits most
    content_keywords: List[str] = Field(default_factory=list)
    # Skip feeding test stdin to the student's own main (it is still compiled); tests run via the TA driver
    driver_only: bool = False
    compiler_flags: List[str] = Field(default_factory=list)
    timeout_sec: float = 2.0
    rubric: List[QuestionRequirement] = Field(default_factory=list)
    static_checks: List[StaticCheckRule] = Field(default_factory=list)
    test_cases: List[TestCase] = Field(default_factory=list)


class MarkingScheme(BaseModel):
    """Max marks per component (the lab's 15-mark rubric) and how suggestions are combined."""
    comp1_max: float = 3.0   # coding conventions, naming, style, indentation
    comp2_max: float = 3.0   # basic test cases, expected output
    comp3_max: float = 6.0   # logic, algorithm, border cases, validation
    comp4_max: float = 3.0   # timely submission, proper comments
    comp3_test_weight: float = 0.5  # share of Comp 3 taken from edge-case tests (rest from the Gemini review)
    late_penalty: float = 1.0       # deducted from Comp 4 when Classroom marks the submission late
    unsimplified_penalty: float = 1.0  # deducted once from Comp 3 when answers are right but fractions are not reduced
    # Lenient marking (TA's choice): genuine attempts get soft floors and marks round UP to the next half
    lenient: bool = True
    style_floor: float = 1.5     # Comp 1 maps 0..max onto floor..max
    comments_floor: float = 1.5  # Comp 4 maps 0..max onto floor..max (late penalty applied after)
    tests_floor: float = 1.0     # Comp 2 minimum when code was submitted (even if it does not compile)
    logic_floor: float = 2.0     # Comp 3 minimum when code was submitted (fraction penalty applied after)


class AssignmentConfig(BaseModel):
    id: str
    title: str
    description: str = ""
    default_language: LanguageType = LanguageType.C
    default_timeout_sec: float = 2.0
    questions: List[QuestionConfig] = Field(default_factory=list)
    marking: MarkingScheme = Field(default_factory=MarkingScheme)


# Execution & Evaluation Models
class CompilerResult(BaseModel):
    success: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    concise_error: Optional[str] = None
    full_log_path: Optional[str] = None
    duration_sec: float = 0.0
    command: str = ""
    backend: str = "native"  # "native" (local toolchain) | "wsl"
    language_used: Optional[LanguageType] = None


class ExecutionResult(BaseModel):
    status: CheckStatus
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.0
    timed_out: bool = False
    error_type: Optional[str] = None  # "TIMEOUT", "SEGFAULT", "ABORT", etc.
    output_truncated: bool = False


class TestEvaluation(BaseModel):
    __test__ = False  # not a pytest test class
    test_id: str
    description: str
    passed: bool
    status: CheckStatus
    actual_output: str
    expected_output: str
    diff_summary: str = ""
    execution_time_sec: float = 0.0
    is_edge_case: bool = False
    input_data: str = ""
    stderr: str = ""
    returncode: Optional[int] = None
    note: str = ""  # e.g. "exited with code 1 but output matched"
    unsimplified: bool = False  # right value, fraction not in lowest terms


class StaticCheckResult(BaseModel):
    rule_id: str
    rule_type: str
    description: str
    passed: bool
    status: CheckStatus
    message: str
    matched_snippets: List[str] = Field(default_factory=list)


class ComponentDetail(BaseModel):
    rating: QualitativeRating = QualitativeRating.NOT_EVALUATED
    status: CheckStatus = CheckStatus.PASS
    feedback: str = ""
    score: Optional[float] = None      # Gemini's anchored score for this component
    max_score: Optional[float] = None  # the max it was scored out of


class EvaluationComponents(BaseModel):
    # Comp 1: Coding conventions, naming (variable, fun naming ) etc. coding style, indentation etc
    comp1_style_conventions: ComponentDetail = Field(
        default_factory=lambda: ComponentDetail(feedback="Coding conventions, naming and indentation")
    )
    # Comp 2: basic test cases, expected output
    comp2_basic_tests: ComponentDetail = Field(
        default_factory=lambda: ComponentDetail(feedback="Basic test cases and expected output matching")
    )
    # Comp 3: logic + algo + bordercases / validation etc
    comp3_logic_edge_cases: ComponentDetail = Field(
        default_factory=lambda: ComponentDetail(feedback="Core logic, algorithm efficiency, border cases and validation")
    )
    # Comp 4: timely submission / proper comments etc
    comp4_comments_submission: ComponentDetail = Field(
        default_factory=lambda: ComponentDetail(feedback="Proper comments, headers and submission structure")
    )
    # Extra comments / notes
    extra_comments: List[str] = Field(default_factory=list)


class LLMReviewItem(BaseModel):
    requirement: str
    status: CheckStatus = CheckStatus.UNCERTAIN
    reason: str = ""


class LLMReview(BaseModel):
    requirements: List[LLMReviewItem] = Field(default_factory=list)
    algorithmic_assessment: AlgorithmicAssessment = AlgorithmicAssessment.NOT_EVALUATED
    readability: QualitativeRating = QualitativeRating.NOT_EVALUATED
    variable_naming: QualitativeRating = QualitativeRating.NOT_EVALUATED
    components: EvaluationComponents = Field(default_factory=EvaluationComponents)
    issues: List[str] = Field(default_factory=list)
    manual_review: bool = False
    manual_review_reason: Optional[str] = None
    raw_response: Optional[str] = None
    duration_sec: float = 0.0
    model_used: Optional[str] = None
    error: Optional[str] = None


class QuestionGradingResult(BaseModel):
    question_id: str
    question_name: str
    language: LanguageType
    source_files: List[str] = Field(default_factory=list)
    found_source: bool = True
    compiler_result: Optional[CompilerResult] = None
    test_results: List[TestEvaluation] = Field(default_factory=list)
    static_results: List[StaticCheckResult] = Field(default_factory=list)
    llm_review: Optional[LLMReview] = None
    passed_tests_count: int = 0
    total_tests_count: int = 0
    needs_manual_review: bool = False
    manual_review_reasons: List[str] = Field(default_factory=list)
    driver_used: bool = False
    driver_note: Optional[str] = None
    execution_backend: Optional[str] = None
    grading_error: Optional[str] = None


class StudentGradingResult(BaseModel):
    student_id: str
    assignment_id: Optional[str] = None
    zip_path: Optional[str] = None
    extracted_dir: Optional[str] = None
    question_results: Dict[str, QuestionGradingResult] = Field(default_factory=dict)
    total_tests_passed: int = 0
    total_tests_run: int = 0
    compilation_failures: int = 0
    runtime_failures: int = 0
    manual_review_recommended: bool = False
    manual_review_reasons: List[str] = Field(default_factory=list)
    timestamp: str = ""
    error_message: Optional[str] = None
    late: Optional[bool] = None  # from the Classroom download manifest; None = unknown
    ta_marks: Dict[str, float] = Field(default_factory=dict)  # marks confirmed/edited by the TA (comp1..comp4)
    ta_marks_at: Optional[str] = None


class ComponentMark(BaseModel):
    key: str
    label: str
    max_marks: float
    suggested: Optional[float] = None
    final: Optional[float] = None  # TA mark if set, else the suggestion
    confirmed: bool = False
    source: str = ""
    reason: str = ""


class MarkSuggestion(BaseModel):
    components: List[ComponentMark] = Field(default_factory=list)
    total_suggested: Optional[float] = None
    total_final: Optional[float] = None
    total_max: float = 0.0
    all_confirmed: bool = False
    flags: List[str] = Field(default_factory=list)


class BatchSummary(BaseModel):
    assignment_id: str
    total_submissions: int = 0
    processed_count: int = 0
    successful_count: int = 0
    compilation_failures: int = 0
    runtime_failures: int = 0
    manual_review_count: int = 0
    llm_failures: int = 0
    student_ids: List[str] = Field(default_factory=list)
    timestamp: str = ""
