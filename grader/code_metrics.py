"""
Deterministic style and comment metrics for C/C++ submissions.

Used for Comp 1 (naming, indentation) and Comp 4 (comments) when no LLM review is available.
The same code always gets the same score, and every score comes with the numbers behind it.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_TYPE_WORDS = r"(?:int|float|double|char|long|short|bool|unsigned|signed|string|auto|size_t|void|[A-Z]\w*)"
_DECL_RE = re.compile(rf"\b{_TYPE_WORDS}\s*[*&]*\s+([A-Za-z_]\w*)\s*(?=[=;,\[)(])")
# A function/constructor definition head: optional return type words, a name, a parameter list, then '{' or end of line
_FUNC_LINE_RE = re.compile(r"^(?:[\w:<>,*&]+\s+)*[*&]*([A-Za-z_~][\w:~]*)\s*\([^;]*\)\s*(?:const)?\s*(?::.*)?(?:\{.*)?$")
_KEYWORDS = {"if", "for", "while", "switch", "return", "else", "do", "sizeof", "main", "case", "new", "delete", "operator"}
# Short names that are idiomatic in student code (loop counters, maths, numerator/denominator, coordinates)
_OK_SHORT = set("ijknmxyzrhabcpq") | {"id", "x1", "y1", "x2", "y2", "r1", "r2", "c1", "c2", "os", "is", "in", "pi", "op"}


@dataclass
class CodeMetrics:
    code_lines: int
    comment_lines: int
    comment_ratio: float
    has_header_comment: bool
    documented_functions: int
    functions: int
    identifiers: int
    poor_names: List[str]
    indented_fraction: float
    mixed_indentation: bool

    @property
    def naming_fraction(self) -> float:
        return 1.0 if not self.identifiers else 1 - len(self.poor_names) / self.identifiers

    def style_fraction(self) -> float:
        indent = self.indented_fraction - (0.1 if self.mixed_indentation else 0)
        return max(0.0, min(1.0, 0.5 * self.naming_fraction + 0.5 * indent))

    def comment_fraction(self) -> float:
        ratio_part = min(self.comment_ratio / 0.15, 1.0)  # ~1 comment line per 7 code lines earns full credit
        func_part = self.documented_functions / self.functions if self.functions else ratio_part
        return max(0.0, min(1.0, 0.5 * ratio_part + 0.25 * float(self.has_header_comment) + 0.25 * func_part))

    def style_reason(self) -> str:
        names = f"{len(self.poor_names)}/{self.identifiers} unclear names" + (f" ({', '.join(self.poor_names[:4])})" if self.poor_names else "")
        return f"code metrics: {names}, {round(self.indented_fraction * 100)}% of nested lines indented" + (", mixed tabs/spaces" if self.mixed_indentation else "")

    def comment_reason(self) -> str:
        return (f"code metrics: {self.comment_lines} comment lines for {self.code_lines} code lines, "
                f"{self.documented_functions}/{self.functions} functions commented"
                + (", file header present" if self.has_header_comment else ", no file header comment"))


def measure(files: List[Path]) -> Optional[CodeMetrics]:
    texts = []
    for f in files:
        try:
            if Path(f).suffix.lower() in (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"):
                texts.append(Path(f).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    if not texts:
        return None

    code_lines = comment_lines = 0
    header = False
    nested = nested_indented = 0
    tabs = spaces = False
    names: List[str] = []
    functions = documented = 0

    for text in texts:
        lines = text.splitlines()
        first = [l.strip() for l in lines if l.strip()][:3]
        if any(l.startswith(("//", "/*", "*")) for l in first):
            header = True

        in_block = False
        depth = 0
        prev_comment = 999  # code lines since the last comment line
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            is_comment = in_block or stripped.startswith(("//", "/*"))
            if "/*" in stripped and "*/" not in stripped.split("/*", 1)[1]:
                in_block = True
            if in_block and "*/" in stripped:
                in_block = False
            if is_comment or "//" in stripped:
                comment_lines += 1
                prev_comment = 0
            if is_comment and not re.sub(r"/\*.*?\*/|//.*", "", stripped).strip():
                continue
            code_lines += 1
            prev_comment += 1

            code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//.*', "", stripped)
            if depth > 0 and not code.startswith("}") and not stripped.startswith("#"):
                nested += 1
                lead = line[: len(line) - len(line.lstrip())]
                if lead:
                    nested_indented += 1
                tabs |= "\t" in lead
                spaces |= " " in lead
            fm = _FUNC_LINE_RE.match(code)
            if fm and not code.rstrip().endswith(";") and fm.group(1).split("::")[-1] not in _KEYWORDS:
                functions += 1
                documented += prev_comment <= 2  # a comment right above (or one line above, e.g. template<>)
            depth += code.count("{") - code.count("}")
            depth = max(depth, 0)

        clean = re.sub(r"/\*.*?\*/|//[^\n]*", " ", text, flags=re.DOTALL)
        clean = re.sub(r'"(?:\\.|[^"\\])*"', '""', clean)
        names.extend(n for n in _DECL_RE.findall(clean) if n not in _KEYWORDS)

    unique = sorted(set(names))
    poor = [n for n in unique
            if (len(n) <= 2 and n.lower() not in _OK_SHORT and not re.fullmatch(r"[a-z]\d", n.lower()))
            or re.fullmatch(r"(?:var|abc|xyz|aa|bb|qq)\d*", n.lower())]
    return CodeMetrics(
        code_lines=code_lines,
        comment_lines=comment_lines,
        comment_ratio=comment_lines / code_lines if code_lines else 0.0,
        has_header_comment=header,
        documented_functions=documented,
        functions=functions,
        identifiers=len(unique),
        poor_names=poor,
        indented_fraction=nested_indented / nested if nested else 1.0,
        mixed_indentation=tabs and spaces,
    )
