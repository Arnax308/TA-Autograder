# Using the autograder for Compiler Design (and large classes)

The app was built for a C/C++ OOP lab with about 25 students. This page covers what changes for a **Compiler Design** lab
graded **at a much larger volume**: what works out of the box, what to set up differently, and two small extensions a
setup agent should implement before the labs that need them.

Finish [SETUP.md](../SETUP.md) first.

---

## 1. What works out of the box

Any lab where the student writes a **C or C++ program that reads stdin and prints stdout**. That covers most of a
Compiler Design course:

| Typical lab | How to test it |
|---|---|
| Hand-written lexical analyzer (token classes, symbol table) | `input_data` = a small source program; expected = one `CLASS lexeme` per line; mode `lines` |
| Removing comments / whitespace, counting lines and tokens | mode `lines`, or `whitespace_normalized` when the output is just numbers |
| FIRST / FOLLOW sets, left recursion / left factoring | input = grammar; expected = one set per line **in a fixed order** (see §2); mode `lines` |
| LL(1) / SLR / LR(0) / LALR parse tables | input = grammar; expected = table rows in a fixed format; mode `lines` |
| Recursive-descent / predictive / shift-reduce parsers | input = N strings; expected = `Accepted` / `Rejected` per line; mode `lines` |
| Three-address code, quadruples, postfix, DAG / constant folding | input = expressions; expected = generated code lines; mode `lines` (or `regex_match` if temporaries may be named differently) |

`assignments/example_compiler_design/` is a working example with a lexer and a recursive-descent parser. Its expected
outputs were produced by running a reference solution. A deliberately buggy lexer (it splits `>=` into `>` and `=`)
fails the right tests.

**Always use `comparison_mode: "lines"` for compiler output.** The default `whitespace_normalized` mode is lenient for
menu-driven programs. Among other fallbacks, it **passes a test when the expected numbers appear in order**, so a lexer
that prints the wrong token classes but the right numbers would pass.

## 2. Make the output format part of the lab statement

At high volume, the single biggest time saver is an unambiguous I/O format. Then every student's own `main()` can be
tested directly, and no AI driver is needed (drivers cost API quota and can be wrong). Put this in every lab sheet:

- Read everything from **stdin** (no `fopen("input.txt")`, no command-line arguments), and print results to **stdout**.
- Give the exact labels and one item per line, for example `KEYWORD int`, `FIRST(E) = { (, id }`, `Accepted`.
- **Sets in a fixed order** (e.g. terminals in the order they first appear in the grammar, or sorted), with `id` / `epsilon` spelled out.
- No prompts like "Enter the grammar:" (or allow them, and use `regex_match`).
- Name the main file after the question (`q1_lexer.c`), or put each question in its own folder (`q1/`, `q2/`).
  The app maps files to questions using `source_hints` and `content_keywords`.

Once a lab is written this way, `whitespace_normalized` / `lines` tests and the student's own program are enough.
TA drivers are only generated when the student's own program fails and it has separate implementation files.

## 3. Extensions to implement (for the setup agent)

These are **not implemented yet**. Implement each one only when a lab needs it, and add a test in `tests/`.

### 3a. Lex / Yacc (flex / bison) labs

Today only `.c .cpp .cc .cxx .h .hpp` files are collected and compiled. To support `.l` and `.y`:

1. Install the tools in WSL: `sudo apt install -y flex bison`.
2. `grader/extractor.py` → `Extractor.collect_source_files`: add `.l` and `.y` to `valid_extensions`.
3. `grader/compiler.py` → `Compiler.compile`, before building the gcc command:
   - Copy the sources into the build directory.
   - For each `.y`, run `bison -d -o <stem>.tab.c <file>.y`. This creates `<stem>.tab.c` and `<stem>.tab.h`. Many lex files `#include "y.tab.h"`, so also copy or alias it as `y.tab.h`.
   - For each `.l`, run `flex -o <stem>.yy.c <file>.l`.
   - Add the generated `.c` files to the compile units and link with `-lfl`, or define `int yywrap(void){return 1;}` when the student didn't.
   - Run these steps through the same WSL wrapper that is used for gcc (`_build_command` / `_run`), and add their output to the compile log so build errors show in the student drawer.
4. `grader/compiler.py` → `has_main`: a `.y` / `.l` file may contain `main()` in its user-code section. Treat those files as "has main" so they're built as the student's program.
5. Tests stay stdin/stdout as in §1.

**Acceptance:** a test zip with a `.l` that counts lines, words and chars (and a `.y` + `.l` calculator) compiles in WSL and passes its pack.

### 3b. Tests that need input files or command-line arguments

Only needed if a lab can't be changed to read stdin (§2). Today `TestCase` only has `input_data` (stdin).

1. `grader/models.py` → `TestCase`: add `input_files: Dict[str, str] = {}` (file name → content) and `args: List[str] = []`.
2. `grader/runner.py` → `SandboxRunner.run_executable`: accept `files` and `args`, write the files into `temp_cwd` before
   starting the process, and pass the args to both `_run_native` and `_run_in_wsl`.
   For WSL, the args go after the executable in the `timeout` command.
3. `grader/main.py` → `_run_tests`: pass `tc.input_files` and `tc.args` through.
4. Add the two fields to the test editor in `grader/gui/app.js` (`testForm`) if TAs edit tests in the dashboard.

## 4. Grading a large class

| Topic | What to do |
|---|---|
| **Who gets graded** | `TA_STUDENTS` empty = everyone in the folder. Several TAs can split the class with ranges (`cs23b1001-cs23b1060`), each on their own machine. |
| **Parallel grading** | Dashboard → Grade → *Output folders & performance* → workers (default 3). The server caps it at 6 (`grader/server.py`, `run_batch_grading_task`). Raise the cap to about the number of CPU cores if WSL keeps up. Compiling is the slow part. |
| **AI quota** | The free Groq tier allows about 8k tokens per minute per model, plus a daily cap. One TA driver costs roughly 3–6k tokens. At hundreds of students, design labs per §2 so drivers are rarely needed. Keep `LLM_REVIEW=off`, or use a paid Groq / Gemini key and set `GROQ_TPM_LIMIT` to the plan's limit. The app spreads load across the configured Groq models and waits when it reaches the per-minute limit. |
| **Re-runs are cheap** | Results are cached per student. Mode *Only new / ungraded* skips finished students. A resubmission (newer zip) is re-graded automatically. Saved TA drivers are reused; *Re-check* forces a new one. |
| **Getting submissions (LMS)** | LMS bulk download → `python scripts/import_lms_download.py "<download.zip>" "<Lab> Submissions"` → one `<roll>.zip` per student (see [SETUP.md](../SETUP.md) §5a). With hundreds of students, run `--dry-run` first. Fix the **Needs attention** list (use `--names` for students without a roll number in their file names), then import. Re-importing a later download overwrites zips with newer ones, and the grader re-grades only those. |
| **Folder layout** | Use `<Lab> Submissions` for the zips. The app finds a sibling `<Lab> Check/Results` and `<Lab> Check/Reports` automatically, and remembers each lab's folders in `assignment_folders.json`. |
| **Late submissions** | LMS imports carry no submission times, so no automatic late penalty. Note late students from the LMS and adjust Comp 4 when confirming marks. |
| **Disk** | `scratch/` holds extracted code and binaries, about 1 MB per student per lab. Between labs it is safe to delete `scratch/extracted` and `scratch/binaries`. Keep `drivers/` if you might re-check. |
| **Timeouts** | `default_timeout_sec` per pack (2 s is plenty for most labs). Infinite loops are killed; memory is capped at 2 GiB per run in WSL. |
| **Suspicious results** | After a batch, open **Needs review** first. Before trusting a pass, check that a deliberately wrong solution fails your tests, as the example pack shows. |
| **Marks** | Every mark is a suggestion. Confirm in the drawer, then **Export CSV**. Change the rubric maxima and lenient floors under `marking` in each pack. |

## 5. Checklist for each new lab

1. Write the lab statement with an exact I/O format (§2).
2. Copy `assignments/example_compiler_design` and edit the questions, keywords and tests (mode `lines`).
3. Produce the expected outputs by running a reference solution.
4. Grade the reference solution (it should get full marks) and one deliberately buggy version (it should fail the right tests).
5. Download all submissions from the LMS and run `scripts/import_lms_download.py` into the lab's submissions folder.
6. Grade, look at **Needs review**, confirm marks, export.
