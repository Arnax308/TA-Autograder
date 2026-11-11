# Overhaul: sandbox fix, bug fixes, new dashboard

## Why the sandbox "always threw errors"

1. **Windows Smart App Control (enforcing on this machine) blocked freshly compiled `.exe` files**
   (`WinError 4551`). Whether a binary was allowed depended on cloud reputation, so it ran only occasionally.
   The runner retried the launch 6 times (wasting ~7 s per test) and then reported a crash.
   **Fix:** a WSL execution backend (`grader/compiler.py`, `grader/runner.py`). Code is compiled with Linux gcc/g++
   and run inside WSL with `timeout`, memory and output limits. Smart App Control does not apply. `auto` (the default)
   uses WSL when it is available. If a native `.exe` is blocked, it is rebuilt in WSL automatically. Code that needs
   `conio.h`, `windows.h` or `scanf_s` is rebuilt natively.
2. **Every C++ test driver started with a stray `pp` line**, so it never compiled. The code-fence regex matched the ```` ```c ````
   in ```` ```cpp ````. Fixed, and the cached drivers in `drivers/` are repaired the next time they load.
3. **Any non-zero exit code counted as a crash** (`return 1;`, or `void main()`'s garbage value), so the output was never
   compared. Now only real crashes (segfault, divide-by-zero, abort and so on) count as crashes. For any other exit code,
   the output is still compared and the code is shown in a note.
4. **The first run of each new `.exe` was slowed down by antivirus scanning** and hit the 2 s timeout. Native binaries now get a warm-up launch first.
5. **The configured Gemini model (`gemini-3.7-flash`) hung until timeout on every call.** Grading was slow and all 21
   reviews failed. The default is now `gemini-3.6-flash` (~5 s). Models that time out or return 404 are skipped for a while,
   and errors are reported instead of being swallowed.

## Other bugs fixed

### Pipeline (`grader/main.py`)
- `shutil` was never imported. Binary cleanup failed silently, which is why `scratch/binaries` had grown to 117 MB.
- Single-question labs mapped only some of the student's files. The helper `.c` files were dropped, which caused "undefined reference" errors.
- A cached result from a different assignment in the same results folder was returned as if it belonged to this one.
- Re-grading one question (`--question`) overwrote the student's results for every other question.
- The question timeout setting was ignored, because every test had its own 2.0 s default that took priority.
- One crashing question aborted the whole student (and could abort the batch).
- The driver language came from the question setting, not from the student's code. A C question answered in C++ got a C driver.
- A broken cached driver was reused forever. It is now regenerated once.
- Archives were extracted to `%TEMP%` and never deleted. They now go to `scratch/extracted/<assignment>/<student>`.
- When both the student's own program and the TA driver can run, the result that shows more passing tests is used, and the report says which one.

### Compiler
- Header files were passed to gcc as compile units.
- Duplicate `X.c` and `X.cpp` files caused "multiple definition" errors.
- A `main()` inside a comment was counted as a real one.
- `-std=c11` from the question was passed to g++.
- `-include stdbool.h` was always forced on, which broke students who define their own `bool`. It is now only added when gcc complains that `bool` is missing.
- gcc output in WSL showed mojibake quotes.

### Tester / analyzer
- The prompt-stripping regex had no word boundaries. For example, `is` inside `this` erased the rest of the output.
- An invalid regex in a test or rule crashed grading.
- A regex rule with groups produced tuple snippets, which failed validation.
- Any `x = y;` counted as using a function pointer, and so did the pointer's own declaration.
- `cout` / `cin` C++ detection had no word boundaries.

### Config
- YAML packs were saved with Python enum tags and could not be loaded again.
- A modular `tests.json` silently overrode tests edited in the UI.
- **Deleting an assignment fell back to `Path(id)`.** Deleting `grader` would have deleted the app's own source folder.
- Adding a question with an existing ID silently replaced it, along with its tests.
- Assignment names were not sanitized.

### LLM / generator / extractor
- The API key was sent in the URL query string. It now goes in a header.
- Timeouts were 6–12 s, too short for flash models. Errors were never surfaced.
- A malformed LLM JSON shape (for example, a string instead of an object) crashed grading.
- The generator wrote `assignment.json` even for YAML packs, and turned `"null"` into a literal string.
- Duplicate test IDs were allowed.
- Zip-slip check was strengthened. cp437 re-decoding was applied to names that were already UTF-8.
- Over-eager filename matching: short stems like `op` matched `operations`.

### Server (`grader/server.py`)
- `ComparisonMode` / `TestCase` were not imported, so **saving a test case from the UI always failed**.
- Blocking endpoints were `async def`. Re-checking a student or generating tests froze the whole server, including progress polling.
- Default assignment `week1_pointers_sorting` doesn't exist.
- `HTTPException`s were re-wrapped, so 404s came back as 400s.
- The PowerShell folder dialog could be broken by quotes in a path.
- Path components in student IDs were not stripped.
- New: parallel batch grading, cancel, settings/backend/model endpoints, source viewer, full compile log.

### Reporter / cache
- Components showed `GOOD` / `PASS` even when the LLM never ran. They now show `N/A (not evaluated)`.
- Duplicate notes in reports.
- Result JSON is written atomically, so the dashboard never reads a half-written file.

### Old dashboard
- The static-analysis section never rendered (it read `static_checks` / `s.name` instead of `static_results` / `description`).
- Table ratings showed the last question's values, not the worst across all questions.
- Student output, file names and compiler errors were injected as raw HTML (XSS from student programs).
- Failed requests were silently ignored (the `detail` was never shown).
- The saved assignment selection was never restored.
- "Save default folders" didn't persist anything, and the logs-folder setting was never used.
- Tests could only be added or deleted, not edited. Tolerance, sort and edge fields couldn't be set.

### Docs / tests
- **The README contained the live Gemini API key.** It has been removed. Rotate the key if this file was ever shared.
- The end-to-end test depended on a fixture that no longer exists. It is now self-contained.
- 25 regression tests were added in `tests/test_regressions.py`.

## New dashboard
A full redesign of `grader/gui/`:
- Sidebar layout with light and dark themes, plus a system health panel (compiler, sandbox, Gemini).
- Folders are remembered per assignment.
- Parallel grading with live progress, a log and a cancel button.
- Stat tiles that filter the table, a sortable and searchable table, and CSV export.
- A student inspector showing input / expected / actual output for every test, the compiler error and full log, static checks, the Gemini components, and source code (including the TA driver).
- Full editor for assignments, questions and tests.
- Settings page for the execution backend and the Gemini model, which is picked from the models available to your key.
