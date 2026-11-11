"""
Robust ZIP and directory extractor with heuristic and LLM-assisted question mapping.
Handles nested directories, irregular filenames, arbitrary folder nesting, encoding issues,
and semantic file-to-question classification via Google Gemini.
"""

from __future__ import annotations
import os
import re
import shutil
import tempfile
import zipfile
import json
import time
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set

from grader.models import QuestionConfig, AssignmentConfig
from grader.config import SystemConfig
from grader.llm_router import call_llm


class LLMSemanticMapper:
    """Uses Google Gemini to semantically map arbitrarily named student source files to questions."""

    @staticmethod
    def map_unmapped_files(
        unmapped_files: List[Path],
        questions: List[QuestionConfig],
        current_mapping: Dict[str, List[Path]]
    ) -> Dict[str, List[Path]]:
        """
        Takes unmapped source files and sends their names and code snippets to Gemini
        along with the assignment question descriptions to determine the exact question mapping.
        """
        if not unmapped_files:
            return current_mapping

        api_key = SystemConfig.get_llm_api_key()
        model = SystemConfig.get_llm_model()

        # Questions that still need files or have 0 files
        needed_qs = [q for q in questions if not current_mapping.get(q.id)]
        if not needed_qs:
            needed_qs = questions

        # Prepare file summaries with preview snippets
        file_previews = []
        for idx, fp in enumerate(unmapped_files, 1):
            try:
                code_text = fp.read_text(encoding="utf-8", errors="replace")
                # Remove excessive whitespace
                code_snippet = code_text[:1200]
            except Exception:
                code_snippet = "Could not read file."

            file_previews.append({
                "file_index": idx,
                "file_path": str(fp),
                "filename": fp.name,
                "snippet": code_snippet
            })

        # Build prompt
        qs_desc = "\n".join([f"- ID '{q.id}': Name='{q.name}', Description='{q.description}'" for q in questions])
        files_desc = "\n\n".join([
            f"FILE #{f['file_index']}: '{f['filename']}' (Path: {f['file_path']})\nCode Snippet:\n```\n{f['snippet']}\n```"
            for f in file_previews
        ])

        prompt = f"""You are assisting an automated programming autograder.
Students have submitted source files with arbitrary names (e.g. 'fib.c', 'fact.cpp', 'sort.c', 'solution.cpp', 'task1.c').
Your task is to analyze the source code and filename of each student file, and map it to the correct Question ID.

### ASSIGNMENT QUESTIONS:
{qs_desc}

### STUDENT SOURCE FILES TO MAP:
{files_desc}

### INSTRUCTIONS:
Return ONLY a valid JSON object mapping each Question ID to a list of matching file paths.
Example format:
{{
  "q1": ["path/to/fib.c"],
  "q2": ["path/to/fact.cpp"]
}}

Every file should be mapped to the most appropriate question ID based on the logic implemented in its code.
"""

        # If API key is available, call Gemini
        if api_key:
            text, _err, _ = call_llm(prompt, model=model, json_mode=True,
                                        temperature=0.1, max_output_tokens=4096, timeout_sec=60.0)
            try:
                mapping_json = json.loads(text) if text else None
            except (TypeError, ValueError):
                mapping_json = None
            if isinstance(mapping_json, dict):
                for qid, paths in mapping_json.items():
                    if qid in current_mapping and isinstance(paths, list):
                        for p_str in paths:
                            for uf in unmapped_files:
                                if str(uf) == str(p_str) or uf.name == Path(str(p_str)).name:
                                    if uf not in current_mapping[qid]:
                                        current_mapping[qid].append(uf)
                return current_mapping

        # Local Token / Keyword Fallback if offline or API unavailable
        LLMSemanticMapper._fallback_keyword_matching(unmapped_files, questions, current_mapping)
        return current_mapping

    @staticmethod
    def _fallback_keyword_matching(
        unmapped_files: List[Path],
        questions: List[QuestionConfig],
        current_mapping: Dict[str, List[Path]]
    ):
        """Fuzzy semantic keyword match between file content/name and question name/description."""
        for fp in unmapped_files:
            # Check if already mapped
            if any(fp in files for files in current_mapping.values()):
                continue

            try:
                content = fp.read_text(encoding="utf-8", errors="replace").lower()
            except Exception:
                content = ""

            stem = fp.stem.lower()
            best_q = None
            best_score = 0

            for q in questions:
                score = 0
                q_words = set(re.findall(r"\w+", f"{q.name} {q.description}".lower()))
                stem_words = set(re.findall(r"\w+", stem))

                # Common stem words overlap
                overlap = stem_words.intersection(q_words)
                score += len(overlap) * 5

                # Check if question name words appear inside code functions/comments
                for qw in q_words:
                    if len(qw) > 3 and qw in content:
                        score += 2

                if score > best_score and score >= 4:
                    best_score = score
                    best_q = q.id

            if best_q:
                current_mapping[best_q].append(fp)


class Extractor:
    """Safely extracts student submission ZIPs and maps extracted files to assignment questions."""

    @staticmethod
    def extract_zip(zip_path: str | Path, target_dir: Optional[Path] = None, _nested: bool = False) -> Tuple[Optional[Path], Optional[str]]:
        """
        Safely extracts a ZIP archive into target_dir (or a temporary dir).
        Protects against zip-slip, handles nested folder hierarchies, and malformed archives.
        Returns (extracted_path, error_message).
        """
        zip_p = Path(zip_path)
        if not zip_p.exists():
            return None, f"Submission file does not exist: {zip_p}"

        if not zipfile.is_zipfile(zip_p):
            return None, f"File is not a valid ZIP archive: {zip_p.name}"

        if target_dir is None:
            target_dir = Path(tempfile.mkdtemp(prefix=f"extracted_{zip_p.stem}_"))
        else:
            # Start from a clean folder so files from an older submission never leak into a re-check
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            target_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_p, "r") as zf:
                for member in zf.infolist():
                    # Handle encoding quirks
                    filename = member.filename
                    if not member.flag_bits & 0x800:  # name not flagged as UTF-8: may be mis-decoded cp437
                        try:
                            filename = member.filename.encode("cp437").decode("utf-8")
                        except Exception:
                            pass

                    # Zip-slip prevention: the resolved destination must stay inside target_dir
                    norm_path = os.path.normpath(filename.replace("\\", "/"))
                    if os.path.isabs(norm_path) or re.match(r"^[A-Za-z]:", norm_path):
                        continue
                    dest_path = (target_dir / norm_path).resolve()
                    if not dest_path.is_relative_to(target_dir.resolve()):
                        continue
                    if member.is_dir():
                        dest_path.mkdir(parents=True, exist_ok=True)
                    else:
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as source, open(dest_path, "wb") as target:
                            shutil.copyfileobj(source, target)

            # A zip inside the zip (common when several files are uploaded together) is unpacked in place, one level deep
            if not _nested:
                for inner in list(target_dir.rglob("*.zip")):
                    if "__MACOSX" in inner.parts or not zipfile.is_zipfile(inner):
                        continue
                    Extractor.extract_zip(inner, target_dir=inner.parent / f"{inner.stem}_unzipped", _nested=True)

            return target_dir, None

        except zipfile.BadZipFile as e:
            return None, f"Corrupted or invalid ZIP file: {str(e)}"
        except Exception as e:
            return None, f"Extraction failed: {str(e)}"

    @staticmethod
    def fix_double_extensions(folder: Path) -> List[str]:
        """
        Windows hides known extensions, so students often submit 'RAT.h.cpp' or 'main.cpp.cpp'; the
        includes then cannot resolve. Renames them to their intended name and returns what was renamed.
        """
        renamed = []
        pattern = re.compile(r"^(.+\.(?:h|hpp|c|cpp|cc|cxx))\.(?:cpp|c|txt|h)$", re.IGNORECASE)
        for p in sorted(Path(folder).rglob("*")):
            m = pattern.match(p.name)
            if not p.is_file() or not m or "__MACOSX" in p.parts:
                continue
            target = p.with_name(m.group(1))
            if target.exists():
                continue
            p.rename(target)
            renamed.append(f"{p.name} -> {target.name}")
        return renamed

    @staticmethod
    def collect_source_files(extracted_dir: Path) -> List[Path]:
        """
        Recursively finds all C and C++ source and header files regardless of how deeply nested
        folders are inside the ZIP.
        Filters out Mac metadata, temporary files, and compiled binaries.
        """
        valid_extensions = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"}
        ignored_dir_prefixes = {"__MACOSX", ".git", ".vscode", ".idea", "__pycache__"}
        found_files = []

        if not extracted_dir.exists():
            return []

        for root, dirs, files in os.walk(extracted_dir):
            # Prune unwanted directories
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ignored_dir_prefixes]

            for file in files:
                p = Path(root) / file
                # Ignore hidden files, Mac metadata, or temporary files
                if file.startswith(".") or file.startswith("~$") or file.endswith(".tmp"):
                    continue
                if p.suffix.lower() in valid_extensions:
                    found_files.append(p)

        return sorted(found_files)

    @staticmethod
    def map_files_to_questions(
        source_files: List[Path],
        assignment: AssignmentConfig,
        use_llm_mapper: bool = True
    ) -> Dict[str, List[Path]]:
        """
        Maps source files to questions using a 3-tier pipeline:
        1. Pattern matching (regex, question numbering, and name keywords)
        2. AST/Token content heuristics (templates, overloads, function pointers, etc.)
        3. LLM Semantic Classification with Gemini (for arbitrary names like 'fib.c', 'factorial.cpp')
        """
        mapping: Dict[str, List[Path]] = {q.id: [] for q in assignment.questions}
        unmapped_files: List[Path] = []

        # A single-question lab owns every file in the submission. Mapping only a subset here used to
        # drop helper files (e.g. addmatrices.c) and caused "undefined reference" linker failures.
        if len(assignment.questions) == 1:
            mapping[assignment.questions[0].id] = list(source_files)
            return mapping

        # Multi-task OOP labs: find each program (a main() and the files it uses) and match it by content
        if any(q.content_keywords for q in assignment.questions):
            by_content = Extractor._map_by_content(source_files, assignment)
            if by_content is not None:
                return by_content

        # When every question has its own language (e.g. "Q1 in C, Q2 in C++"), the language of each
        # folder is a far more reliable signal than file names: students number folders after the lab
        # sheet (q2/, q3/), which often differs from the question IDs in the pack.
        by_language = Extractor._map_by_language(source_files, assignment)
        if by_language is not None:
            return by_language

        # Tier 1: Pattern & Name Matching
        for file_path in source_files:
            assigned_q = Extractor._match_by_name(file_path, assignment.questions)
            if assigned_q:
                mapping[assigned_q].append(file_path)
            else:
                unmapped_files.append(file_path)

        # Tier 2: Heuristic Content Inspection for remaining files
        if unmapped_files:
            still_unmapped: List[Path] = []
            for file_path in unmapped_files:
                heuristic_q = Extractor._match_by_content_heuristics(file_path, assignment.questions, mapping)
                if heuristic_q:
                    mapping[heuristic_q].append(file_path)
                else:
                    still_unmapped.append(file_path)
            unmapped_files = still_unmapped

        # Tier 3: LLM Semantic Classifier (Gemini) for any remaining unmapped files
        if unmapped_files and use_llm_mapper:
            mapping = LLMSemanticMapper.map_unmapped_files(
                unmapped_files=unmapped_files,
                questions=assignment.questions,
                current_mapping=mapping
            )

        return mapping

    @staticmethod
    def split_programs(source_files: List[Path]) -> List[List[Path]]:
        """
        Splits a submission into programs: each file with main() plus every header it includes
        (transitively) and every implementation file belonging to those headers. A submission
        without any main() is a single program.
        """
        from grader.compiler import has_main, read_source, HEADER_EXTS
        texts = {f: read_source(f) for f in source_files}
        by_name: Dict[str, List[Path]] = {}
        for f in source_files:
            by_name.setdefault(f.name.lower(), []).append(f)
        by_path = {str(f.resolve()).lower(): f for f in source_files}
        impls = [f for f in source_files if f.suffix.lower() not in HEADER_EXTS and not has_main(texts[f])]
        mains = [f for f in source_files if f.suffix.lower() not in HEADER_EXTS and has_main(texts[f])]
        if not mains:
            return [list(source_files)] if source_files else []

        include_re = re.compile(r'#\s*include\s*"([^"]+)"')

        def closeness(a: Path, b: Path) -> int:
            n = 0
            for x, y in zip(a.resolve().parts, b.resolve().parts):
                if x.lower() != y.lower():
                    break
                n += 1
            return n

        def resolve(from_file: Path, name: str) -> Optional[Path]:
            # Like the compiler: relative to the including file first (case-insensitive, as on Windows)
            hit = by_path.get(str((from_file.parent / name).resolve()).lower())
            if hit is not None:
                return hit
            # Otherwise the same-named file closest to the including file (never one from another task folder if a nearer one exists)
            options = by_name.get(Path(name).name.lower(), [])
            return max(options, key=lambda h: closeness(h.parent, from_file.parent)) if options else None

        def includes(f: Path) -> List[Path]:
            out = []
            for name in include_re.findall(texts[f]):
                h = resolve(f, name)
                if h is not None and h != f:
                    out.append(h)
            return out

        programs = []
        for main in mains:
            members = {main}
            frontier = [main]
            while frontier:
                cur = frontier.pop()
                for h in includes(cur):
                    if h not in members:
                        members.add(h)
                        frontier.append(h)
                # Implementation files that define what the included headers declare
                member_headers = [h for h in members if h.suffix.lower() in HEADER_EXTS]
                for impl in impls:
                    if impl in members:
                        continue
                    impl_headers = [h for h in includes(impl) if h.suffix.lower() in HEADER_EXTS]
                    member_names = {h.name.lower() for h in member_headers}
                    if any(h not in members and h.name.lower() in member_names for h in impl_headers):
                        continue  # it uses its own copy of a same-named header: another task's program
                    belongs = any(h in members for h in impl_headers)
                    if not belongs:
                        # Same stem as an included header, and the nearest such implementation to that header
                        for h in member_headers:
                            if h.stem.lower() != impl.stem.lower():
                                continue
                            rivals = [i for i in impls if i.stem.lower() == impl.stem.lower()]
                            if max(rivals, key=lambda i: closeness(i.parent, h.parent)) == impl:
                                belongs = True
                                break
                    if belongs:
                        members.add(impl)
                        frontier.append(impl)
            # A main that includes nothing local still gets the implementation files sitting next to it,
            # unless it defines its own classes: then it is a self-contained demo program, and sibling
            # files belong to other tasks (linking them in clashes with the main's own classes)
            if len(members) == 1 and not re.search(r"\b(?:class|struct)\s+\w+\s*(?::[^;{]*)?\{", texts[main]):
                members |= {f for f in impls if f.parent == main.parent}
            programs.append(sorted(members))
        return programs

    @staticmethod
    def _map_by_content(source_files: List[Path], assignment: AssignmentConfig) -> Optional[Dict[str, List[Path]]]:
        from grader.compiler import read_source, strip_comments
        programs = Extractor.split_programs(source_files)
        if not programs:
            return None

        def score(program: List[Path], q: QuestionConfig) -> float:
            text = "\n".join(strip_comments(read_source(f)) for f in program)
            names = " ".join(str(f).lower() for f in program)
            total = 0.0
            for kw in q.content_keywords:
                try:
                    total += min(len(re.findall(kw, text, re.IGNORECASE | re.MULTILINE)), 5)
                except re.error:
                    continue
            for hint in q.source_hints:
                if re.search(rf"\b{re.escape(hint.lower())}\b", names):
                    total += 2
            return total

        scores = [{q.id: score(p, q) for q in assignment.questions} for p in programs]
        mapping: Dict[str, List[Path]] = {q.id: [] for q in assignment.questions}
        qids = [q.id for q in assignment.questions]
        assigned = Extractor._best_assignment(scores, qids) if len(programs) * len(qids) <= 64 else None
        if assigned is not None:
            # One program per task, chosen so the total match is highest: a static-binding demo that also
            # uses `virtual` must not take the polymorphism task from the program written for it
            for qid, pi in assigned.items():
                mapping[qid] = list(programs[pi])
        else:
            best_for_q: Dict[str, float] = {}
            for p, sc in zip(programs, scores):
                top = max(sc.values())
                if top <= 0:
                    continue
                for qid, v in sc.items():
                    # Each program goes to its best-matching task; among several programs for a task, keep the strongest
                    if v == top and v > best_for_q.get(qid, 0):
                        mapping[qid], best_for_q[qid] = list(p), v
        # A task nobody's program won outright can still use a program that clearly covers it (one main for all tasks)
        for q in assignment.questions:
            if not mapping[q.id]:
                cand = max(zip(programs, scores), key=lambda ps: ps[1][q.id])
                if cand[1][q.id] >= 3:
                    mapping[q.id] = list(cand[0])
        return mapping

    @staticmethod
    def _best_assignment(scores: List[Dict[str, float]], qids: List[str]) -> Optional[Dict[str, int]]:
        """
        Matches programs to tasks one-to-one, maximising the summed score (only positive scores count).
        Ties prefer the assignment where each program sits at its own top-scoring task. Returns
        {question id: program index}; tasks without a positive match are left out.
        """
        import itertools
        n = len(scores)
        slots = list(range(n)) + [None] * len(qids)
        best, best_key = None, None
        for perm in itertools.permutations(slots, len(qids)):
            if len({x for x in perm if x is not None}) != sum(1 for x in perm if x is not None):
                continue
            total, at_top = 0.0, 0
            for qid, pi in zip(qids, perm):
                if pi is None:
                    continue
                v = scores[pi][qid]
                if v <= 0:
                    break
                total += v
                at_top += v == max(scores[pi].values())
            else:
                key = (total, at_top)
                if best_key is None or key > best_key:
                    best, best_key = perm, key
        if best is None:
            return None
        return {qid: pi for qid, pi in zip(qids, best) if pi is not None}

    @staticmethod
    def _map_by_language(source_files: List[Path], assignment: AssignmentConfig) -> Optional[Dict[str, List[Path]]]:
        """Groups files by folder and assigns each group to the question whose language it is written in."""
        from grader.compiler import detect_language
        from grader.models import LanguageType

        langs = [q.language for q in assignment.questions]
        if LanguageType.AUTO in langs or len(set(langs)) != len(langs) or not source_files:
            return None

        groups: Dict[Path, List[Path]] = {}
        for f in source_files:
            groups.setdefault(f.parent, []).append(f)

        from grader.compiler import looks_like_cpp, read_source, HEADER_EXTS

        lang_to_q = {q.language: q.id for q in assignment.questions}
        mapping: Dict[str, List[Path]] = {q.id: [] for q in assignment.questions}

        def add(lang, files):
            qid = lang_to_q.get(lang)
            if qid is None:
                return False
            mapping[qid].extend(f for f in files if f not in mapping[qid])
            return True

        for files in groups.values():
            code = [f for f in files if f.suffix.lower() not in HEADER_EXTS]
            headers = [f for f in files if f.suffix.lower() in HEADER_EXTS]
            c_code = [f for f in code if f.suffix.lower() == ".c" and not looks_like_cpp(read_source(f))]
            cpp_code = [f for f in code if f not in c_code]
            if not (c_code and cpp_code):
                if not add(detect_language(files), files):
                    return None
                continue
            # One folder holding both a C and a C++ program: split it, giving each side the headers it includes
            def included_by(side):
                text = "\n".join(read_source(f) for f in side)
                return [h for h in headers if re.search(rf'#\s*include\s*"{re.escape(h.name)}"', text)]
            c_headers, cpp_headers = included_by(c_code), included_by(cpp_code)
            unclaimed = [h for h in headers if h not in c_headers and h not in cpp_headers]
            if not (add(LanguageType.C, c_code + c_headers + unclaimed) and add(LanguageType.CPP, cpp_code + cpp_headers + unclaimed)):
                return None
        return mapping

    @staticmethod
    def _match_by_name(file_path: Path, questions: List[QuestionConfig]) -> Optional[str]:
        """Matches a file to a question based on file name, parent folder name, and question title keywords."""
        stem = file_path.stem.lower()
        full_str = str(file_path).lower()

        # Sort questions by longest ID first (e.g. q2a, q2b, q2c before q2)
        sorted_qs = sorted(questions, key=lambda q: len(q.id), reverse=True)

        # 1. Exact match on stem
        for q in sorted_qs:
            qid = q.id.lower()
            if stem == qid:
                return q.id

        # 2. Pattern and hint checks with word boundaries
        for q in sorted_qs:
            qid = q.id.lower()
            q_num = qid.lstrip("q")  # e.g., "2a", "1", "2b"
            patterns = [
                rf"\b{re.escape(qid)}\b",
                rf"\b(?:question|prob|task|q|lab)[\s\-_]*(?:q)?{re.escape(q_num)}\b",
                rf"\b(?:question|prob|task|q|lab)[\s\-_]*(?:q)?{re.escape(qid)}\b",
            ]
            for hint in q.source_hints:
                patterns.append(rf"\b{re.escape(hint.lower())}\b")

            for pat in patterns:
                if re.search(pat, stem, re.IGNORECASE):
                    return q.id

        # 3. Path directory match
        for q in sorted_qs:
            qid = q.id.lower()
            if f"/{qid}/" in full_str or f"\\{qid}\\" in full_str or f"/{qid}_" in full_str or f"\\{qid}_" in full_str:
                return q.id

        # 4. Keyword similarity to question name (e.g. stem 'fibonacci' or 'fib' vs question 'Fibonacci Sequence')
        for q in sorted_qs:
            q_title_words = [w.lower() for w in re.findall(r"\w+", q.name) if len(w) > 3]
            for w in q_title_words:
                if w in stem or (len(stem) >= 4 and stem in w):
                    return q.id

        return None

    @staticmethod
    def _match_by_content_heuristics(
        file_path: Path,
        questions: List[QuestionConfig],
        current_mapping: Dict[str, List[Path]]
    ) -> Optional[str]:
        """Inspects source code content to identify question if filename was vague."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            return None

        # Clean comments
        clean_code = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
        clean_code = re.sub(r"//.*", "", clean_code)

        scores: Dict[str, int] = {q.id: 0 for q in questions}

        for q in questions:
            qid = q.id.lower()
            # If question already has a mapped file, reduce priority
            if current_mapping.get(q.id):
                scores[q.id] -= 2

            if qid == "q2c" or "template" in q.name.lower():
                if re.search(r"template\s*<", clean_code):
                    scores[q.id] += 10
            elif qid == "q2b" or "overload" in q.name.lower():
                if re.search(r"(?:int|void|float|double)\s+(\w+)\s*\([^)]*int", clean_code) and \
                   re.search(r"(?:int|void|float|double)\s+(\w+)\s*\([^)]*char", clean_code):
                    scores[q.id] += 8
            elif qid == "q3" or ("pointer" in q.name.lower() and "func" in q.description.lower()):
                if re.search(r"\(\s*\*\s*\w+\s*\)\s*\(", clean_code):
                    scores[q.id] += 10
            elif qid == "q1" or "const" in q.description.lower():
                if re.search(r"const\s+\w+\s*\*|\w+\s*\*\s*const", clean_code):
                    scores[q.id] += 8
            elif qid == "q4" or "storage" in q.description.lower():
                matches = 0
                for kw in ["extern", "static", "register", "auto"]:
                    if re.search(rf"\b{kw}\b", clean_code):
                        matches += 1
                if matches >= 2:
                    scores[q.id] += 10

        best_q = max(scores, key=scores.get)
        if scores[best_q] >= 5:
            return best_q

        return None
