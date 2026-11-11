# Setup guide

This guide sets up the TA Autograder on a new Windows 11 machine. It is written so that a coding agent
(e.g. Antigravity) can follow it step by step: every step ends with a **Check**. Do not go on until the check passes.

If the course is **Compiler Design** or the class is large, also read [docs/COMPILER_DESIGN.md](docs/COMPILER_DESIGN.md)
after finishing this guide.

---

## 0. Ground rules (for the agent)

- **Never commit or share:**
  - `.env`, `.secrets/`, `classroom.json`, `assignment_folders.json`
  - anything in `scratch/`, `results/`, `reports/`, `logs/`, `drivers/`
  - student submissions

  These hold API keys, OAuth tokens, student e-mails and student code. `.gitignore` already excludes them; keep it that way.
- Course assignment packs (`assignments/<lab>/`) contain the reference answers. They are git-ignored on purpose; keep them local.
- Run commands from the repository root.

## 1. Prerequisites

| Tool | Why | Install |
|---|---|---|
| Python **3.10+** | the app | <https://www.python.org/downloads/>; tick "Add python.exe to PATH" |
| Git | clone the repo | <https://git-scm.com/download/win> |
| WSL 2 + Ubuntu with gcc/g++ | compiles and runs student code safely (Windows Smart App Control blocks freshly compiled `.exe` files) | see below |

Install WSL and the compilers. Open PowerShell **as Administrator**:

```powershell
wsl --install -d Ubuntu
```

Reboot if asked, open Ubuntu once to create a Linux user, then inside Ubuntu run:

```bash
sudo apt update && sudo apt install -y build-essential
```

**Check** (in normal PowerShell):

```powershell
python --version          # 3.10 or newer
wsl -e gcc --version      # prints a gcc version
wsl -e g++ --version
```

> Without WSL the app falls back to a Windows gcc (MinGW / w64devkit, set `GCC_PATH`/`GXX_PATH` in `.env`).
> Windows may block those binaries, so WSL is strongly recommended.

## 2. Get the code and install dependencies

```powershell
git clone https://github.com/Arnax308/TA-Autograder.git
cd TA-Autograder
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

(If activation is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.)

**Check:**

```powershell
python -m pytest -q
```

Every test should pass. The end-to-end tests compile small C programs, so they also confirm that WSL gcc works.

## 3. Configure `.env`

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

- **AI key (needed for TA test drivers; optional otherwise).**
  - Free option: create a key at <https://console.groq.com/keys> and set `GROQ_API_KEY=...` (leave `LLM_PROVIDER=auto`).
  - Gemini works too: set `GEMINI_API_KEY`.
- `LLM_REVIEW=off` keeps token use low. Style and comments are then marked from code metrics. Set `on` for an AI code review.
- `TA_STUDENTS`: the roll numbers this TA grades, e.g. `cs23b1001-cs23b1060, cs23b2001-cs23b2030`. Leave it empty to grade everyone.

**Check:** `python gui.py` opens <http://127.0.0.1:8765>. The sidebar shows **Compilers ready** and **WSL (gcc …)**, plus the AI provider if a key was set.
If port 8765 is busy, set `TA_GUI_PORT=8770` in `.env`.

## 4. Try it on the example packs

The repo ships two example packs:

| Pack | What it shows |
|---|---|
| `assignments/example_c_basics` | two simple stdin/stdout C programs |
| `assignments/example_compiler_design` | a lexical analyzer and a recursive-descent parser, tested with the strict `lines` comparison |

Make a test submission to grade:

1. Create a folder, e.g. `C:\TA\Example Submissions`.
2. Put one zip per student in it, named by roll number (`cs23b1001.zip`), containing their `.c` files in any folder layout.
   Step 5 shows how to make these from an LMS download.
3. In the dashboard, go to **Grade**.
4. Pick the pack and choose the folder. Results go to a sibling `… Check\Results` / `… Check\Reports` folder if one exists; otherwise set them under "Output folders".
5. Click **Start grading**, then click a student to see compile errors, input / expected / actual output for each test, static checks and suggested marks.

**Check:** a correct solution passes every test of the pack it was graded against.

## 5. Getting the submissions in

The grader needs **one folder per lab** (e.g. `CD Lab 3 Submissions`) holding **one `.zip` (or one folder) per
student, named by roll number**: `cs23b1001.zip`, `cs23b1002.zip`, and so on. The zip or folder name becomes the student ID
everywhere: in results, marks and the CSV export. Inside a student's zip, any folder layout works, and zips inside zips are unpacked.

### 5a. From the college LMS (Moodle-style "download all submissions")

1. In the LMS, open the lab's assignment → **Download all submissions**. This gives one big zip.
2. Convert it into per-student zips:

   ```powershell
   python scripts/import_lms_download.py "C:\Downloads\CD-Lab3-12345.zip" "C:\TA\CD Lab 3 Submissions" --dry-run
   python scripts/import_lms_download.py "C:\Downloads\CD-Lab3-12345.zip" "C:\TA\CD Lab 3 Submissions"
   ```

   - Each top-level folder or file in the download is one student. Wrapper folders and `__MACOSX` junk are skipped.
   - The **roll number** is searched for in the folder name, then in the file names inside it.
     The default pattern matches IDs like `cs23b1001`. For another format, pass `--id-regex`, e.g. `--id-regex "(\d{4}CSE\d{4})"`.
   - If a student's files carry no roll number, add them to a CSV with lines like `Asha Rao,cs23b1001`.
     The name is the LMS display name, i.e. the part of the folder name before `_123_assignsubmission…`. Pass it with `--names names.csv`.
   - The importer prints what it wrote and lists **Needs attention** entries (no roll number found, empty, duplicate).
     It also writes `import_report.csv` into the output folder. Fix those entries and run it again.
   - Inner zips are kept as they are; the grader unpacks them when grading.
3. In the dashboard: **Grade** → pick the pack → choose that folder → **Start grading**.
   Late or resubmitted work: download again and re-import into the same folder. A newer zip is re-graded automatically.

> If the LMS has no bulk download, put each student's files into a zip (or a folder) named by roll number by hand.

**Check:** the output folder has one `<roll>.zip` per student, and the Grade page shows them under "Not graded".

### 5b. From Google Classroom (only if the course uses Classroom)

The **Classroom** page downloads every turned-in submission and saves it as `<roll>.zip`. The roll number is taken from the
student's e-mail with a regex, default `(\d{4})@`; change it on the page if the institute's e-mails differ.
Re-syncing only fetches new submissions and resubmissions. **Skip this section if submissions come from an LMS.**

One-time setup. Every TA uses **their own** Google Cloud project; never reuse someone else's client file.

1. <https://console.cloud.google.com/> → create a project. A personal Gmail account is fine if the institute blocks project creation.
2. **APIs & Services → Library:** enable **Google Classroom API** and **Google Drive API**.
3. **OAuth consent screen:**
   - Choose External (or Internal if allowed) and fill in the app name and support e-mail.
   - Under **Test users**, add the Google account that is a teacher/TA in the Classroom course.
4. **Credentials → Create credentials → OAuth client ID → Desktop app.** Download the JSON.
5. In the dashboard: **Classroom → upload the JSON → Sign in** with the course account.

Only read-only scopes are requested. The client file and token live in `.secrets/`, which is git-ignored.
If sign-in later says the scope has changed, sign out and in again.

**Check:** the Classroom page lists the course and its classwork. Syncing one classwork item creates `<roll>.zip` files in the chosen folder.

Tip: link each classwork item to its assignment pack once. The Grade page then shows **Sync & grade**.

## 6. Make a pack for a real lab

1. Copy an example folder, e.g. `assignments/example_compiler_design` → `assignments/cd_lab3`.
2. Edit `assignment.json`. Change `id` to the folder name and update the title and questions.
   You can also use **Assignments** and **Test generator** in the dashboard.
3. For each question:
   - **`description`:** the exact I/O format students must follow.
   - **`test_cases`:** `input_data` (stdin), `expected_output`, `comparison_mode`, and `is_edge_case`. Edge cases feed Comp 3.
   - **`content_keywords`:** regexes used to work out which student files belong to which question.
   - **`static_checks`:** optional concept checks. They run on code with comments **and string literals removed**.
4. Generate expected outputs by running a reference solution, never by hand. Then grade the reference solution and check it gets full marks.

Comparison modes:

| Mode | Use it for |
|---|---|
| `lines` | **Strict.** Every non-blank line must match in order; spacing inside a line is ignored. Use for tokens, parse results, tables, FIRST/FOLLOW sets. |
| `whitespace_normalized` | **Lenient** (default). Accepts prompts and extra text, and **passes if the expected numbers appear in order**. Fine for numeric answers, wrong for word outputs. |
| `exact` | Byte-for-byte, including trailing spaces and newlines. Rarely what you want. |
| `float_tolerance` | Numbers within `float_tolerance`. |
| `token_match` | Expected tokens appear in order. Also falls back to numbers only. |
| `regex_match` | `expected_output` is a regex. Add `expected_display` for a readable version. |
| `sorted_check` | Output must be a sorted sequence. |

Marking (the 15-mark rubric, per pack under `marking`):

- Comp 1 style /3, Comp 2 basic tests /3, Comp 3 logic and edge cases /6, Comp 4 comments and on-time /3.
- Lenient marking is on by default: genuine attempts get floors, and marks round up. Set `"lenient": false` or change the floors under `marking`.
- Late submissions lose `late_penalty` in Comp 4. Only Classroom downloads record lateness; for LMS imports, apply late penalties by hand when confirming marks.
- All marks are **suggestions**. The TA confirms them in the student drawer, and confirmed marks survive re-grading.

## 7. Daily use

- **Start:** `.\.venv\Scripts\Activate.ps1`, then `python gui.py`.
- **Get submissions:**
  - LMS: run `scripts/import_lms_download.py` into the lab's submissions folder (step 5a).
  - Classroom: **Sync & grade**.
- **Grade:** pick the lab, choose the folder, **Start grading**. "Only new / ungraded" skips students who are already graded.
- **Review:**
  - **Needs review** lists build failures, crashes and tests that could not run.
  - **Re-check** on a student re-runs them with a fresh TA driver.
- **Export:** **Export CSV** on the Grade page.
- **CLI alternative:** `python grader.py "<submissions folder>" --assignment <pack> --results-dir "<…>" --output-dir "<…>"`.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `WinError 4551` / programs never run | Smart App Control. Install WSL gcc (step 1) and keep `EXEC_BACKEND=auto` or `wsl`. |
| A lab shows no students in the app | Pick its submissions folder once in **Grade**. Folders are remembered on the server in `assignment_folders.json`. |
| Importer: "no roll number found" for many students | Roll numbers aren't in the file names. Use `--names names.csv` (export the class list from the LMS as `name,roll`), or adjust `--id-regex`. |
| Student IDs show up as long LMS folder names | The download was copied in without the importer. Run step 5a so every submission is `<roll>.zip`. |
| "TA driver not available (… daily limit reached)" | The AI provider's daily quota ran out. Wait for it to reset (Groq resets over the day), or use a paid key. |
| Many "TA driver failed to compile" | The student's code doesn't expose callable functions. Check by hand, or tighten the I/O format in the lab sheet so their own `main` can be tested directly. |
| Tests pass for a clearly wrong program | The test uses the lenient `whitespace_normalized` mode. Switch that test to `lines`. |
