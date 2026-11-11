# C/C++ TA Autograder

> **A robust, local, TA-assisted code inspection and grading pipeline for C/C++ programming labs.**

The application is built for Teaching Assistants (TAs). It acts as an intelligent, automated pair-evaluator and **never assigns final marks automatically**. The TA remains solely responsible for entering marks into the official institution spreadsheet.

## Start here

- **Setting it up on a new machine:** follow **[SETUP.md](SETUP.md)**. It is written step by step with a check after each step, so a coding agent can follow it.
- **Compiler Design labs or a large class:** read **[docs/COMPILER_DESIGN.md](docs/COMPILER_DESIGN.md)**. It covers which comparison mode to use, how to write labs that grade cleanly at scale, and the flex/bison and input-file extensions.
- **Submissions from a college LMS** (not Google Classroom): `scripts/import_lms_download.py` turns the LMS "download all submissions" zip into one `<roll>.zip` per student ([SETUP.md](SETUP.md) §5a).
- **Example packs:** `assignments/example_c_basics` and `assignments/example_compiler_design`. Real course packs stay local; they are git-ignored because they hold reference answers.
- **AI provider:** Groq (free tier) or Gemini, set in `.env` (see `.env.example`). The AI writes TA test drivers; the optional code review is controlled by `LLM_REVIEW`.

---

## 🏛️ Architecture: The 3-Layer Principle

```
┌────────────────────────────────────────────────────────┐
│                   HUMAN TA (Sole Authority)            │
│  Inspects concise reports, reviews flags & assigns marks│
└───────────────────────────▲────────────────────────────┘
                            │
┌───────────────────────────┴────────────────────────────┐
│         Layer C: Qualitative LLM Review (Gemini)       │
│  Readability, naming, potential edge-case notes,       │
│  calm/fair review, manual review recommendations        │
├────────────────────────────────────────────────────────┤
│     Layer B: Deterministic Static / Structural Checks  │
│  Templates, function overloading, function pointer vs  │
│  if/else runtime dispatch, const qualifiers, storage   │
├────────────────────────────────────────────────────────┤
│            Layer A: Objective Execution & Tests        │
│  GCC/G++ compilation, process tree timeouts (while(1)),│
│  segfault / crash capture, multi-mode test assertions  │
└────────────────────────────────────────────────────────┘
```

1. **Layer A (Objective Facts)**: GCC/G++ compilation, concise error summaries, sandboxed execution with strict timeout protection, memory safety, and multi-mode test comparisons (whitespace-normalized, floating-point tolerance, sorted sequence checks, token matching).
2. **Layer B (Deterministic Static Analysis)**: Inspects AST/regex patterns with comments & strings safely stripped. Checks templates, overloading, function pointer runtime selection vs direct branching, const qualifiers, and storage classes.
3. **Layer C (Qualitative LLM Review)**: Powered by Google Gemini (e.g. Gemini 2.5 Flash / Gemini 3.7 Flash). Evaluates readability, variable naming, and algorithmic subtleties constructively and fairly without exaggerating minor typos.

---

## 🚀 Quick Start

### 1. Prerequisites
- **Python 3.9+**
- **GCC and G++** (MinGW, Clang, or Linux GCC). Automatically discovered on your system or in portable locations.

- **Recommended on Windows 11: WSL with gcc** (`wsl --install`, then `sudo apt install build-essential` inside Ubuntu). See *Execution Sandbox* below.

### 2. Installation
Clone or navigate to the project directory and install dependencies:
```bash
pip install -r requirements.txt
```

### 3. API Key Configuration
Create or edit your `.env` file:
```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.6-flash
# Where student code is compiled/run: auto (default), wsl, or native
EXEC_BACKEND=auto
```
*(If no API key is provided, the autograder executes Layer A and Layer B with 100% fidelity and skips Layer C without error).*

---

## 💻 Usage & CLI Commands

### Import submissions from Google Classroom
The **Classroom** page downloads every turned-in submission for a classwork item and saves it as `<roll>.zip`
(roll ID taken from the student's email with a regex, default `(\d{4})@` → `cs25b1089@…` becomes `1089`; any row
can be overridden and the override is remembered). Loose files are packed into one zip; zips inside zips are unpacked.
Re-syncing only downloads new submissions and resubmissions, and those resubmissions are re-graded automatically.
Link the classwork to an assignment pack once, and the Grade page gets a **Sync & grade** button.

One-time setup (the page walks you through it): create a Google Cloud project, enable the *Google Classroom API* and
*Google Drive API*, configure the consent screen (Internal, or External with yourself as a test user), create an
OAuth client of type **Desktop app**, download its JSON and upload it on the Classroom page, then sign in.
Only read-only scopes are requested; the client file and token are stored in `.secrets/`. You must be a teacher
(or co-teacher) of the course.

### Dashboard (recommended)
```bash
python gui.py
```
Opens `http://127.0.0.1:8765`. Pick the assignment and the week's submissions folder (folders are remembered per
assignment), start grading, and click any student to see compiler errors, input / expected / actual output for every
test, static checks, the Gemini review and the source code. Several students are graded in parallel.

### 1. Grade a Batch of Submissions
To grade all student `.zip` archives in a directory:
```bash
python grader.py sample_submissions/
```

#### Handy Options & Flags:
```bash
# Grade a specific student only
python grader.py sample_submissions/ --student Student_01_Perfect

# Grade a single question only (e.g. Q3)
python grader.py sample_submissions/ --question q3

# Grade against a specific weekly assignment pack
python grader.py sample_submissions/ --assignment week_2

# Rerun only Layer C (LLM Qualitative Review) using cached test results
python grader.py sample_submissions/ --rerun-llm

# Force fresh rerun from scratch (ignore cache)
python grader.py sample_submissions/ --rerun

# Output directories
python grader.py sample_submissions/ --output-dir custom_reports/ --results-dir custom_results/
```

---

## 📅 Weekly Assignment Management (Dynamic Questions)

Every week you can evaluate a completely different set of questions with zero Python code changes!

### 1. List Available Assignments
```bash
python grader.py list-assignments
```

### 2. Scaffold a New Assignment Pack
```bash
python grader.py new-assignment --name week2_linked_lists --title "Lab 2: Dynamic Memory & Linked Lists" --desc "Linked list insertion, deletion, and reversal"
```
This generates `assignments/week2_linked_lists/assignment.json` with editable templates for rubrics, static checks, and test cases. Both **JSON** and **YAML** formats are supported!

### 3. Add Questions via CLI
```bash
python grader.py add-question --assignment week2_linked_lists --id q1 --name "Singly Linked List" --desc "Insert, delete, and print nodes" --type c
```

---

## 🤖 LLM Automated Test Case Generator & Frozen Evaluation

You can have Gemini design comprehensive test suites automatically for new questions:
```bash
python grader.py generate-tests --assignment week2_linked_lists --question q1 --problem "Write a C program to reverse a singly linked list with n elements. Input: n followed by elements. Output: reversed elements." --count 4
```

### 🔒 The Frozen Test Suite Principle:
- The generated test cases (normal cases, edge cases like empty/single element/negatives, floating point tolerances) are automatically saved and locked into `assignments/<assignment>/tests.json`.
- You can inspect or edit them if desired.
- During batch evaluation, the **exact same frozen test suite** is run deterministically across all ~22 students in the batch, guaranteeing 100% fair and consistent evaluation.

---

## 📊 Sample Output Report

For every student, a concise **8–12 line Markdown report** is generated in `reports/<student_id>.md`:

```markdown
Student: Rahul

Q1: PASS — All tests and structural requirements satisfied.
Q2A: PASS — All tests and structural requirements satisfied.
Q2B: PASS — Overloaded functions detected (3 variants).
Q2C: FAIL — No template <typename T> declaration found in source code.
Q3: PASS — Function pointer detected and utilized for runtime dispatch.
Q4: PARTIAL — Demonstrated storage classes (static, extern).

Code quality: GOOD
Naming: FAIR

Manual review: Q2C, Q4

Notes:
* Q2C: Student implemented overloading instead of function template.
* Q4: Register/auto keywords not demonstrated.
```

A detailed layer-by-layer diagnostic report and intermediate machine-readable JSON are also saved in `results/<student_id>.json`.

---

## 🧪 Synthetic Test Submissions (Edge Cases Handled)

`python scripts/create_sample_submissions.py` writes 14 synthetic submissions to `sample_submissions/`. They were made for the original 6-question demo pack, which is not included. For a quick start, use the example packs in `assignments/` (see [SETUP.md](SETUP.md) step 4); the automated tests in `tests/` cover these edge cases with their own packs.
1. `Student_01_Perfect.zip` — Completely correct implementation of all 6 questions.
2. `Student_02_CompileError.zip` — Missing semicolon / syntax error in Q2A.
3. `Student_03_RuntimeError.zip` — Null pointer dereference / Segfault in Q3.
4. `Student_04_InfiniteLoop.zip` — Hanging `while(1)` loop in Q2B (handled via timeout).
5. `Student_05_WrongSorting.zip` — Corrupted / wrong sorting logic.
6. `Student_06_VerboseFormatting.zip` — Correct logic with decorative user prompts.
7. `Student_07_NoFunctionPointer.zip` — Direct `if/else` calls instead of a function pointer.
8. `Student_08_C_Instead_Of_Cpp.zip` — Submitting C code for C++ template/overload tasks.
9. `Student_09_BrokenTemplate.zip` — Missing template in Q2C.
10. `Student_10_PoorNaming.zip` — Single-letter obfuscated variable names.
11. `Student_11_AltSortAlgo.zip` — Valid alternative algorithms (Selection sort).
12. `Student_12_MalformedZip.zip` — Corrupted ZIP archive.
13. `Student_13_MissingQuestions.zip` — Partial submission (only Q1 and Q3).
14. `Student_14_NestedMessyZip.zip` — Deeply nested folders with spaces and mixed casing.

### Running Test Suite:
```bash
python -m pytest tests/ -v
```

---

## 🛡️ Security & Sandboxing

### Execution backends (why programs used to "always error")
Windows 11 **Smart App Control** blocks unsigned, freshly compiled `.exe` files based on cloud reputation
(`WinError 4551: An Application Control policy has blocked this file`), so most student programs were
never allowed to run. The grader now supports two backends, chosen in **Settings** or with `EXEC_BACKEND`:

| Backend | What happens |
| :--- | :--- |
| `auto` (default) | Uses WSL when it has gcc/g++, otherwise the Windows toolchain. |
| `wsl` | Compiles with Linux gcc/g++ inside WSL and runs the program there with `timeout`, a 2 GiB memory cap and output-size limits. Not affected by Smart App Control or Defender exe scanning. |
| `native` | Uses MinGW / w64devkit on Windows. If Windows blocks a binary, the grader automatically rebuilds it in WSL when available. |

Programs that need Windows-only headers (`conio.h`, `windows.h`) or MSVC functions (`scanf_s`) are rebuilt natively automatically.
CLI: `python grader.py <folder> --assignment week_2 --backend wsl`.

- Untrusted student executables are run in isolated temporary working directories.
- Environment variables (`GEMINI_API_KEY`, `LLM_API_KEY`, etc.) are stripped from student child processes.
- Process trees are terminated with `/F /T` on Windows or `SIGKILL` on Unix to prevent orphaned zombie processes.
- Strict per-test timeouts prevent denial-of-service hangs.
