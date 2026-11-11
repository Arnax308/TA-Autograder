// C/C++ TA Autograder dashboard client.
// Everything rendered from server data goes through esc(): student programs control their own
// output, file names and compiler messages, so nothing from them may be injected as HTML.

"use strict";

// ---------------------------------------------------------------- helpers

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const store = {
  get(key, fallback = null) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage unavailable */ }
  },
};

async function api(path, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error("Cannot reach the autograder server. Is it still running?");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON */ }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map(d => d.msg || JSON.stringify(d)).join("; ");
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data;
}

function qs(params) {
  return Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
}

const ICONS = {
  play: '<path d="M7 4.5v15l12-7.5-12-7.5z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  layers: '<path d="M12 3 2 8l10 5 10-5-10-5z"/><path d="m2 13 10 5 10-5"/><path d="m2 18 10 5 10-5" opacity=".5"/>',
  sparkles: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.9-3.9L3 9.5"/><path d="M3 4v5.5h5.5"/><path d="M4 13a8 8 0 0 0 14.9 3.9L21 14.5"/><path d="M21 20v-5.5h-5.5"/>',
  download: '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  code: '<path d="m8 8-5 4 5 4M16 8l5 4-5 4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  beaker: '<path d="M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3"/><path d="M7 15h10"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
};

function icon(name, size = 16) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

function hydrateIcons(root = document) {
  $$("[data-icon]", root).forEach(el => { el.outerHTML = icon(el.dataset.icon); });
}

function toast(message, type = "info", ms = 4200) {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `${icon(type === "error" ? "alert" : type === "success" ? "check" : "info")}<span>${esc(message)}</span>`;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .2s"; setTimeout(() => el.remove(), 220); }, ms);
}

async function withBusy(btn, label, fn) {
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span>${esc(label)}`;
  try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = original; }
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const ta = document.createElement("textarea");
  ta.value = text; document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); } finally { ta.remove(); }
  return Promise.resolve();
}

// ---------------------------------------------------------------- state

const S = {
  view: "grade",
  assignments: [],
  status: null,
  results: [],
  filter: "all",
  search: "",
  sort: store.get("ta.sort", { key: "student", dir: 1 }),
  mode: "default",
  polling: null,
  drawer: { sid: null, data: null, report: "", tab: "summary" },
};

function currentAssignment() { return $("#assignmentSelect").value; }

function paths() {
  return {
    submissions: $("#submissionsPath").value.trim(),
    reports: $("#reportsDir").value.trim() || "reports",
    results: $("#resultsDir").value.trim() || "results",
  };
}

function savePaths() {
  const a = currentAssignment();
  if (!a) return;
  const p = paths();
  store.set(`ta.paths.${a}`, p);
  store.set("ta.lastAssignment", a);
  // Also remember them on the server, so results stay findable from any browser or after a cache clear
  if (p.submissions) {
    api(`/api/assignments/${encodeURIComponent(a)}/folders`, {
      method: "POST", body: { submissions: p.submissions, reports: p.reports, results: p.results },
    }).catch(() => {});
  }
}

async function loadPaths(assignmentId) {
  let saved = store.get(`ta.paths.${assignmentId}`) || {};
  if (assignmentId) {
    // The server's record wins: every folder change in this app is posted there too, and grading scripts
    // record their folders there, so it is the newest. This browser's copy is only a fallback.
    try {
      const known = await api(`/api/assignments/${encodeURIComponent(assignmentId)}/folders`);
      if (known && known.submissions) {
        saved = { submissions: known.submissions, reports: known.reports || "reports", results: known.results || "results" };
        store.set(`ta.paths.${assignmentId}`, saved);
      }
    } catch (e) { /* server registry unavailable: keep this browser's copy */ }
  }
  if (assignmentId !== currentAssignment()) return;  // the user already switched to another lab
  $("#submissionsPath").value = saved.submissions || "";
  $("#reportsDir").value = saved.reports || "reports";
  $("#resultsDir").value = saved.results || "results";
  updatePathHint();
}

function updatePathHint() {
  const p = paths();
  const who = S.status && S.status.students ? `Only ${S.status.students_summary} · ` : "";
  $("#pathHint").textContent = p.submissions ? `${who}Reports → ${p.reports} · Results → ${p.results}` : "Choose the folder that holds this week's .zip submissions.";
}

// ---------------------------------------------------------------- navigation

function showView(view) {
  S.view = view;
  $$(".view").forEach(v => { v.hidden = v.id !== `view-${view}`; });
  $$(".nav-item[data-view]").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  if (view === "classroom") crInit();
  if (view === "assignments") renderAssignments();
  if (view === "generator") refreshGenerator();
  if (view === "settings") renderSettings();
  store.set("ta.view", view);
}

// ---------------------------------------------------------------- system status

async function loadStatus() {
  try {
    S.status = await api("/api/system/status");
  } catch (e) {
    S.status = null;
    $("#hCompilerText").textContent = "Server offline";
    $("#hCompiler").className = "dot bad";
    return;
  }
  const st = S.status;
  const backend = st.exec_backend === "wsl" ? `WSL (gcc ${st.wsl_gcc_version || ""})` : "Native Windows";
  const compilerOk = st.exec_backend === "wsl" ? st.wsl_available : st.gcc_available;
  $("#hCompiler").className = `dot ${compilerOk ? "ok" : "bad"}`;
  $("#hCompilerText").textContent = compilerOk ? "Compilers ready" : "No compiler found";

  const sacRisk = st.smart_app_control === "on" && st.exec_backend === "native";
  $("#hSandbox").className = `dot ${sacRisk ? "warn" : "ok"}`;
  $("#hSandboxText").textContent = sacRisk ? "Native · SAC may block" : backend;

  $("#hLlm").className = `dot ${st.llm_configured ? "ok" : "warn"}`;
  $("#hLlmText").textContent = st.llm_configured ? `${st.llm_provider === "groq" ? "Groq" : "Gemini"} · ${st.llm_model.split("/").pop()}` : "AI key missing";
}

// ---------------------------------------------------------------- assignments (shared)

async function loadAssignments() {
  try {
    const data = await api("/api/assignments");
    S.assignments = data.assignments || [];
  } catch (e) {
    toast(e.message, "error");
    S.assignments = [];
  }
  const sel = $("#assignmentSelect");
  const prev = sel.value || store.get("ta.lastAssignment");
  sel.innerHTML = S.assignments.length
    ? S.assignments.map(a => `<option value="${esc(a.id)}">${esc(a.title || a.id)} · ${a.questions_count} question${a.questions_count === 1 ? "" : "s"}</option>`).join("")
    : `<option value="">No assignments yet</option>`;
  if (prev && S.assignments.some(a => a.id === prev)) sel.value = prev;

  const gsel = $("#genAssignment");
  const gprev = gsel.value || sel.value;
  gsel.innerHTML = sel.innerHTML;
  if (gprev && S.assignments.some(a => a.id === gprev)) gsel.value = gprev;
}

function assignmentById(id) { return S.assignments.find(a => a.id === id); }

// ---------------------------------------------------------------- results table

function fmtMark(v) { return v == null ? "—" : String(Math.round(v * 2) / 2); }

function markCell(value, max, confirmed, title) {
  if (value == null) return `<td class="num" title="${esc(title || "Needs your mark")}"><span class="rating fair">?</span></td>`;
  return `<td class="num mark-cell ${confirmed ? "confirmed" : ""}" title="${esc(title || "")}">${fmtMark(value)}<span class="mark-max">/${fmtMark(max)}</span></td>`;
}

function markCells(marks) {
  if (!marks) return `<td></td><td></td><td></td><td></td><td></td>`;
  const comps = marks.components.map(c => markCell(c.final, c.max_marks, c.confirmed, `${c.label}: ${c.reason}`)).join("");
  const flagged = marks.flags && marks.flags.length;
  return comps + markCell(marks.total_final, marks.total_max, marks.all_confirmed,
    marks.all_confirmed ? "All marks confirmed by you" : (flagged ? `Suggested. Check: ${marks.flags.join(" ")}` : "Suggested, not yet confirmed"))
    .replace('class="num mark-cell', `class="num mark-cell mark-total${flagged && !marks.all_confirmed ? " flagged" : ""}`);
}

function ratingChip(value) {
  if (!value) return `<span class="rating na">—</span>`;
  return `<span class="rating ${esc(value.toLowerCase())}">${esc(value)}</span>`;
}

function isPending(r) { return r.status === "pending" || !r.timestamp; }
function isClean(r) { return !isPending(r) && !r.manual_review_recommended && !r.compilation_failures && !r.runtime_failures && !r.error_message; }

function matchesFilter(r, filter) {
  switch (filter) {
    case "pending": return isPending(r);
    case "review": return !isPending(r) && r.manual_review_recommended;
    case "clean": return isClean(r);
    case "compile": return !isPending(r) && r.compilation_failures > 0;
    case "runtime": return !isPending(r) && r.runtime_failures > 0;
    case "errors": return !isPending(r) && (r.compilation_failures > 0 || r.runtime_failures > 0 || !!r.error_message);
    default: return true;
  }
}

function sortValue(r, key) {
  if (key === "tests") return isPending(r) ? -1 : (r.total_tests_run ? r.total_tests_passed / r.total_tests_run : 0);
  if (key === "review") return isPending(r) ? -1 : (r.manual_review_recommended ? 1 : 0);
  if (key === "total") return isPending(r) || !r.marks || r.marks.total_final == null ? -1 : r.marks.total_final;
  return r.student_id.toLowerCase();
}

function renderStats() {
  const all = S.results;
  const graded = all.filter(r => !isPending(r));
  $("#statTotal").textContent = all.length;
  $("#statPending").textContent = all.length - graded.length;
  $("#statClean").textContent = graded.filter(isClean).length;
  $("#statReview").textContent = graded.filter(r => r.manual_review_recommended).length;
  $("#statCompile").textContent = graded.filter(r => r.compilation_failures > 0).length;
  $("#statRuntime").textContent = graded.filter(r => r.runtime_failures > 0).length;
  $$("#statsRow .stat").forEach(s => s.classList.toggle("active", s.dataset.filter === S.filter && S.filter !== "all"));
}

function renderTable() {
  renderStats();
  const body = $("#resultsBody");
  const term = S.search.toLowerCase();
  const rows = S.results
    .filter(r => r.student_id.toLowerCase().includes(term) && matchesFilter(r, S.filter))
    .sort((a, b) => {
      const va = sortValue(a, S.sort.key), vb = sortValue(b, S.sort.key);
      return (va < vb ? -1 : va > vb ? 1 : 0) * S.sort.dir || a.student_id.localeCompare(b.student_id);
    });

  $$("th.sortable").forEach(th => {
    const active = th.dataset.sort === S.sort.key;
    th.innerHTML = th.innerHTML.replace(/<span class="sort-ind">.*?<\/span>/, "") + (active ? `<span class="sort-ind">${S.sort.dir > 0 ? "▲" : "▼"}</span>` : "");
  });

  if (!S.results.length) {
    const p = paths();
    body.innerHTML = `<tr><td colspan="11"><div class="empty">
      <div class="empty-title">${p.submissions ? "No submissions found" : "Pick a submissions folder"}</div>
      <div>${p.submissions ? `No .zip files or student folders in <span class="mono">${esc(p.submissions)}</span>.` : "Choose the folder with this week's .zip files, then start grading."}</div>
    </div></td></tr>`;
    return;
  }
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="11"><div class="empty">No students match this filter.</div></td></tr>`;
    return;
  }

  body.innerHTML = rows.map(r => {
    const sid = esc(r.student_id);
    if (isPending(r)) {
      return `<tr data-sid="${sid}" data-pending="1">
        <td><div class="student-id">${sid}</div></td>
        <td colspan="8"><span class="pill outline">Not graded yet</span></td>
        <td></td>
        <td style="text-align:right"><button class="btn btn-sm" data-grade="${sid}">${icon("play", 14)}Grade</button></td>
      </tr>`;
    }
    const total = r.total_tests_run || 0, passed = r.total_tests_passed || 0;
    const pct = total ? Math.round((passed / total) * 100) : 0;
    const fillCls = total === 0 ? "" : passed === total ? "" : passed === 0 ? "zero" : "partial";
    const build = r.error_message ? `<span class="pill fail">Archive error</span>`
      : r.compilation_failures ? `<span class="pill fail">${r.compilation_failures} failed</span>` : `<span class="pill pass">OK</span>`;
    const run = r.runtime_failures ? `<span class="pill fail">${r.runtime_failures} crash/timeout</span>` : (total ? `<span class="pill pass">OK</span>` : `<span class="rating na">—</span>`);
    const review = r.manual_review_recommended ? `<span class="pill warn">${icon("alert", 12)}Review</span>` : `<span class="pill pass">${icon("check", 12)}Clean</span>`;
    return `<tr data-sid="${sid}">
      <td><div class="student-id">${sid}</div><div class="row-sub">${esc(fmtTime(r.timestamp))}</div></td>
      <td><div class="testbar">
        <div class="testbar-track"><div class="testbar-fill ${fillCls}" style="width:${pct}%"></div></div>
        <span class="testbar-text">${total ? `${passed}/${total}` : "—"}</span>
      </div></td>
      <td>${build}</td>
      <td>${run}</td>
      ${markCells(r.marks)}
      <td>${review}</td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn btn-sm btn-ghost" data-recheck="${sid}" title="Re-grade this student">${icon("refresh", 14)}</button>
      </td>
    </tr>`;
  }).join("");
}

async function loadResults({ quiet = true } = {}) {
  const p = paths();
  const a = currentAssignment();
  try {
    const data = await api(`/api/results?${qs({ results_dir: p.results, reports_dir: p.reports, submissions_path: p.submissions, assignment_id: a })}`);
    S.results = data.results || [];
    renderTable();
    if (!quiet) toast(`Loaded ${S.results.length} submissions.`, "success");
  } catch (e) {
    toast(e.message, "error");
  }
}

function exportCsv() {
  const header = ["student", "comp1", "comp2", "comp3", "comp4", "total", "confirmed_by_ta", "suggested_total",
    "tests_passed", "tests_run", "build_failures", "crash_or_timeout", "late", "manual_review", "flags", "reasons"];
  const lines = [header.join(",")];
  for (const r of S.results) {
    const m = r.marks;
    const comp = k => { const c = m && m.components.find(x => x.key === k); return c && c.final != null ? fmtMark(c.final) : ""; };
    const cells = isPending(r)
      ? [r.student_id, "", "", "", "", "", "", "", "", "", "", "", "", "not graded", "", ""]
      : [r.student_id, comp("comp1"), comp("comp2"), comp("comp3"), comp("comp4"),
        m && m.total_final != null ? fmtMark(m.total_final) : "", m && m.all_confirmed ? "yes" : "no",
        m && m.total_suggested != null ? fmtMark(m.total_suggested) : "",
        r.total_tests_passed, r.total_tests_run, r.compilation_failures, r.runtime_failures,
        r.late == null ? "unknown" : (r.late ? "yes" : "no"), r.manual_review_recommended ? "yes" : "no",
        ((m && m.flags) || []).join(" | "), (r.manual_review_reasons || []).join(" | ")];
    lines.push(cells.map(v => `"${String(v ?? "").replace(/"/g, '""')}"`).join(","));
  }
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${currentAssignment() || "results"}_grading.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------------------------------------------------------------- batch grading

async function startGrading() {
  const p = paths();
  const assignment = currentAssignment();
  if (!assignment) return toast("Create an assignment first.", "error");
  if (!p.submissions) return toast("Choose the submissions folder first.", "error");
  savePaths();
  const workers = Math.max(1, Math.min(6, parseInt($("#workersInput").value, 10) || 3));
  store.set("ta.workers", workers);

  try {
    const data = await api("/api/grade/start", {
      method: "POST",
      body: {
        assignment_id: assignment, submissions_path: p.submissions, reports_dir: p.reports, results_dir: p.results,
        rerun_all: S.mode === "rerun_all", rerun_llm_only: S.mode === "rerun_llm", workers,
      },
    });
    if (!data.success) return toast(data.message, "error");
    toast("Grading started.", "info");
    beginPolling();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function cancelGrading() {
  try {
    const data = await api("/api/grade/cancel", { method: "POST" });
    toast(data.message, data.success ? "info" : "error");
  } catch (e) { toast(e.message, "error"); }
}

function setRunning(running) {
  $("#startBtn").disabled = running;
  $("#startBtn").innerHTML = running ? `<span class="spinner"></span>Grading…` : `${icon("play")}Start grading`;
  $("#cancelBtn").hidden = !running;
}

function beginPolling() {
  if (S.polling) clearInterval(S.polling);
  $("#progressPanel").hidden = false;
  setRunning(true);
  const tick = async () => {
    let st;
    try { st = await api("/api/grade/status"); } catch (e) { return; }
    const total = st.total_students || 0, done = st.processed_students || 0;
    $("#progressLabel").textContent = st.status_message || "Working…";
    $("#progressCount").textContent = total ? `${done} / ${total}` : "";
    const bar = $("#progressBar");
    bar.classList.toggle("indeterminate", st.is_running && !total);
    bar.style.width = total ? `${Math.round((done / total) * 100)}%` : "";
    $("#currentChips").innerHTML = (st.current_students || []).map(s => `<span class="pill accent"><span class="spinner" style="width:10px;height:10px"></span>${esc(s)}</span>`).join("");
    const logBox = $("#logBox");
    const atBottom = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 8;
    logBox.textContent = (st.logs || []).join("\n");
    if (atBottom) logBox.scrollTop = logBox.scrollHeight;

    // Merge live results into the table as they arrive
    if (st.results && st.results.length) {
      const byId = new Map(S.results.map(r => [r.student_id.toLowerCase(), r]));
      st.results.forEach(r => byId.set(r.student_id.toLowerCase(), r));
      S.results = Array.from(byId.values());
      renderTable();
    }

    if (!st.is_running) {
      clearInterval(S.polling);
      S.polling = null;
      setRunning(false);
      $("#currentChips").innerHTML = "";
      $("#progressPanel").hidden = false;
      bar.classList.remove("indeterminate");
      toast(st.status_message || "Grading finished.", st.status_message && st.status_message.startsWith("Error") ? "error" : "success");
      loadResults();
    }
  };
  tick();
  S.polling = setInterval(tick, 1200);
}

async function gradeOne(studentId, btn) {
  const p = paths();
  const run = async () => {
    const data = await api("/api/grade/single", {
      method: "POST",
      body: { student_id: studentId, submissions_path: p.submissions, assignment_id: currentAssignment(), reports_dir: p.reports, results_dir: p.results, rerun_all: true },
    });
    const idx = S.results.findIndex(r => r.student_id.toLowerCase() === studentId.toLowerCase());
    if (idx >= 0) S.results[idx] = data.result; else S.results.push(data.result);
    renderTable();
    toast(`${studentId}: ${data.result.total_tests_passed}/${data.result.total_tests_run} tests passed.`, "success");
    return data;
  };
  try {
    const data = btn ? await withBusy(btn, "", run) : await run();
    if (S.drawer.sid && S.drawer.sid.toLowerCase() === studentId.toLowerCase()) openStudent(studentId);
    return data;
  } catch (e) {
    toast(`${studentId}: ${e.message}`, "error");
  }
}

// ---------------------------------------------------------------- student drawer

function openDrawer() {
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").classList.add("open");
}

function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").classList.remove("open");
  S.drawer.sid = null;
}

async function openStudent(studentId, tab) {
  const p = paths();
  try {
    const data = await api(`/api/results/${encodeURIComponent(studentId)}?${qs({ results_dir: p.results, reports_dir: p.reports, submissions_path: p.submissions })}`);
    S.drawer = { sid: studentId, data: data.data, report: data.report_markdown || "", tab: tab || (S.drawer.sid === studentId ? S.drawer.tab : "summary") };
    renderDrawer();
    openDrawer();
  } catch (e) {
    toast(e.message, "error");
  }
}

function questionStatus(q) {
  if (!q.found_source) return { cls: "fail", label: "Missing" };
  if (q.grading_error) return { cls: "fail", label: "Error" };
  if (q.compiler_result && !q.compiler_result.success) return { cls: "fail", label: "Build failed" };
  if (q.total_tests_count && q.passed_tests_count === q.total_tests_count) return { cls: q.needs_manual_review ? "warn" : "pass", label: `${q.passed_tests_count}/${q.total_tests_count}` };
  if (q.total_tests_count) return { cls: q.passed_tests_count ? "warn" : "fail", label: `${q.passed_tests_count}/${q.total_tests_count}` };
  return { cls: q.needs_manual_review ? "warn" : "pass", label: "No tests" };
}

function renderDrawer() {
  const d = S.drawer.data;
  if (!d) return;
  $("#drawerTitle").textContent = d.student_id;
  const total = d.total_tests_run || 0;
  $("#drawerMeta").innerHTML = [
    d.assignment_id ? `<span class="pill">${esc(d.assignment_id)}</span>` : "",
    total ? `<span class="pill ${d.total_tests_passed === total ? "pass" : d.total_tests_passed ? "warn" : "fail"}">${d.total_tests_passed}/${total} tests</span>` : "",
    d.marks && d.marks.total_final != null ? `<span class="pill ${d.marks.all_confirmed ? "pass" : "accent"}">${fmtMark(d.marks.total_final)}/${fmtMark(d.marks.total_max)}${d.marks.all_confirmed ? " ✓" : " suggested"}</span>` : "",
    d.late ? `<span class="pill warn">Late</span>` : "",
    d.manual_review_recommended ? `<span class="pill warn">Needs review</span>` : `<span class="pill pass">Clean</span>`,
    d.timestamp ? `<span>Graded ${esc(fmtTime(d.timestamp))}</span>` : "",
  ].join("");

  const qids = Object.keys(d.question_results || {});
  if (S.drawer.tab !== "summary" && !qids.includes(S.drawer.tab)) S.drawer.tab = "summary";
  $("#drawerTabs").innerHTML = [`<button class="tab ${S.drawer.tab === "summary" ? "active" : ""}" data-tab="summary">Summary</button>`]
    .concat(qids.map(qid => {
      const st = questionStatus(d.question_results[qid]);
      return `<button class="tab ${S.drawer.tab === qid ? "active" : ""}" data-tab="${esc(qid)}">${esc(qid.toUpperCase())} <span class="pill ${st.cls}">${esc(st.label)}</span></button>`;
    })).join("");

  $("#drawerBody").innerHTML = S.drawer.tab === "summary" ? renderSummaryTab(d) : renderQuestionTab(d, S.drawer.tab);
  $("#drawerBody").scrollTop = 0;
}

function renderMarksSection(d) {
  const m = d.marks;
  if (!m) return "";
  const rows = m.components.map(c => `<div class="marks-row">
      <div><div class="check-title">${esc(c.label)}</div><div class="check-msg">${esc(c.reason)}</div></div>
      <div class="marks-suggested" title="Suggested">${c.suggested == null ? `<span class="rating fair">needs you</span>` : `${fmtMark(c.suggested)}<span class="mark-max">/${fmtMark(c.max_marks)}</span>`}</div>
      <input class="input marks-input" type="number" min="0" max="${c.max_marks}" step="0.5" data-mark="${esc(c.key)}"
        value="${c.confirmed ? fmtMark(c.final) : ""}" placeholder="${c.suggested == null ? "" : fmtMark(c.suggested)}" title="Your mark (leave empty to keep the suggestion)">
    </div>`).join("");
  return `<div class="section"><h3 class="section-title">Marks
      <span class="row-sub" style="text-transform:none;letter-spacing:0">${m.all_confirmed ? "Confirmed by you" : "Suggested · confirm or edit"}</span></h3>
    ${(m.flags || []).length ? `<div class="callout warn" style="margin-bottom:10px">${icon("alert")}<div>${m.flags.map(f => esc(f)).join("<br>")}</div></div>` : ""}
    <div class="marks-table">
      <div class="marks-row marks-head"><div>Component</div><div>Suggested</div><div>Your mark</div></div>
      ${rows}
      <div class="marks-row marks-foot"><div class="check-title">Total</div>
        <div class="marks-suggested">${fmtMark(m.total_suggested)}<span class="mark-max">/${fmtMark(m.total_max)}</span></div>
        <div class="marks-suggested" id="marksTotalFinal">${fmtMark(m.total_final)}<span class="mark-max">/${fmtMark(m.total_max)}</span></div></div>
    </div>
    <div class="btn-row" style="margin-top:10px">
      <button class="btn btn-primary btn-sm" data-marks-save>${icon("check", 14)}Save marks</button>
      <button class="btn btn-sm" data-marks-accept ${m.components.some(c => c.suggested == null) ? "disabled title='Fill in the components that need you first'" : ""}>Accept all suggestions</button>
      ${Object.keys(d.ta_marks || {}).length ? `<button class="btn btn-sm btn-ghost" data-marks-clear>Clear my marks</button>` : ""}
    </div></div>`;
}

async function saveMarks(mode, btn) {
  const d = S.drawer.data;
  const p = paths();
  const marks = {};
  for (const c of d.marks.components) {
    const inp = document.querySelector(`[data-mark="${c.key}"]`);
    const typed = inp && inp.value.trim() !== "" ? parseFloat(inp.value) : null;
    if (mode === "clear") marks[c.key] = null;
    else if (typed != null) marks[c.key] = typed;
    else if (mode === "accept" || c.confirmed) marks[c.key] = c.confirmed ? c.final : c.suggested;
    else if (mode === "save" && c.suggested != null) marks[c.key] = c.suggested;
  }
  try {
    const r = await withBusy(btn, "Saving", () => api(`/api/results/${encodeURIComponent(S.drawer.sid)}/marks`, {
      method: "POST", body: { marks, results_dir: p.results, reports_dir: p.reports, submissions_path: p.submissions },
    }));
    d.marks = r.marks;
    d.ta_marks = Object.fromEntries((r.marks.components || []).filter(c => c.confirmed).map(c => [c.key, c.final]));
    const row = S.results.find(x => x.student_id === S.drawer.sid);
    if (row) { row.marks = r.marks; row.ta_marks = d.ta_marks; }
    renderTable();
    renderDrawer();
    toast(mode === "clear" ? "Your marks were cleared." : `Saved ${fmtMark(r.marks.total_final)}/${fmtMark(r.marks.total_max)} for ${S.drawer.sid}.`, "success");
  } catch (e) { toast(e.message, "error"); }
}

function renderSummaryTab(d) {
  const parts = [renderMarksSection(d)];
  if (d.error_message) {
    parts.push(`<div class="callout fail">${icon("alert")}<div><strong>Submission could not be opened.</strong> ${esc(d.error_message)}</div></div>`);
  }
  const qids = Object.keys(d.question_results || {});
  if (qids.length) {
    parts.push(`<div class="section"><h3 class="section-title">Questions</h3>${qids.map(qid => {
      const q = d.question_results[qid];
      const st = questionStatus(q);
      const why = !q.found_source ? "No source file matched this question."
        : q.compiler_result && !q.compiler_result.success ? (q.compiler_result.concise_error || "Compilation failed.")
          : (q.manual_review_reasons || [])[0] || "All checks passed.";
      return `<div class="q-summary-row" data-goto="${esc(qid)}">
        <span class="mono" style="font-weight:600">${esc(qid.toUpperCase())}</span>
        <div style="min-width:0"><div style="font-weight:550">${esc(q.question_name)}</div><div class="row-sub" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(why)}</div></div>
        <span class="pill ${st.cls}">${esc(st.label)}</span>
      </div>`;
    }).join("")}</div>`);
  }
  if ((d.manual_review_reasons || []).length) {
    parts.push(`<div class="section"><h3 class="section-title">Why it was flagged</h3><ul class="list-plain">${d.manual_review_reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul></div>`);
  }
  parts.push(`<div class="section"><h3 class="section-title">TA report <button class="btn btn-sm" data-copy-report>${icon("copy", 14)}Copy</button></h3><pre class="report-pre">${esc(S.drawer.report || "No report file found.")}</pre></div>`);
  return parts.join("");
}

function ioBlock(label, value) {
  const v = value ?? "";
  return `<div><div class="io-label">${esc(label)}</div><pre class="code">${v === "" ? `<span class="empty-val">(empty)</span>` : esc(v)}</pre></div>`;
}

function renderQuestionTab(d, qid) {
  const q = d.question_results[qid];
  const parts = [];
  const cr = q.compiler_result;

  if (!q.found_source) {
    return `<div class="callout fail">${icon("alert")}<div><strong>No source file found for ${esc(qid.toUpperCase())}.</strong> The student may have named it unexpectedly — check the archive, or add a source hint to the question.</div></div>`;
  }

  // Facts strip
  const facts = [];
  if (cr) {
    facts.push(cr.success ? `<span class="pill pass">${icon("check", 12)}Builds</span>` : `<span class="pill fail">${icon("x", 12)}Build failed</span>`);
    if (cr.backend) facts.push(`<span class="pill info">${cr.backend === "wsl" ? "Ran in WSL" : "Ran natively"}</span>`);
    if (cr.language_used) facts.push(`<span class="pill">${esc(String(cr.language_used).toUpperCase())}</span>`);
  }
  if (q.total_tests_count) facts.push(`<span class="pill ${q.passed_tests_count === q.total_tests_count ? "pass" : q.passed_tests_count ? "warn" : "fail"}">${q.passed_tests_count}/${q.total_tests_count} tests</span>`);
  if (q.driver_used) facts.push(`<span class="pill accent" title="Tests called the student's functions through a TA-written main()">via TA driver</span>`);
  parts.push(`<div class="section"><h3 class="section-title">${esc(q.question_name)}</h3><div class="facts">${facts.join("")}</div>
    <div class="row-sub" style="margin-top:8px">Files: ${(q.source_files || []).map(f => `<span class="mono">${esc(f.split(/[\\/]/).pop())}</span>`).join(", ") || "—"}</div></div>`);

  if (q.grading_error) {
    parts.push(`<div class="callout fail">${icon("alert")}<div><strong>The grader hit an internal error on this question.</strong><pre class="code" style="margin-top:8px">${esc(q.grading_error)}</pre></div></div>`);
  }
  if (q.driver_note) {
    parts.push(`<div class="callout info" style="margin-top:12px">${icon("info")}<div>${esc(q.driver_note)}</div></div>`);
  }

  if (cr && !cr.success) {
    parts.push(`<div class="section"><h3 class="section-title">Compiler error <button class="btn btn-sm" data-compile-log="${esc(qid)}">${icon("code", 14)}Full log</button></h3>
      <pre class="code">${esc(cr.concise_error || "Compilation failed.")}</pre><div id="compileLog-${esc(qid)}"></div></div>`);
  }

  // Tests
  const tests = q.test_results || [];
  if (tests.length) {
    parts.push(`<div class="section"><h3 class="section-title">Test cases</h3>${tests.map((t, i) => {
      const cls = t.passed ? "pass" : (t.status === "TIMEOUT" ? "warn" : "fail");
      const ic = t.passed ? "check" : (t.status === "TIMEOUT" ? "clock" : "x");
      const statusLabel = t.passed ? "PASS" : t.status.replace("_", " ");
      return `<details class="test-item" ${!t.passed && i < 3 ? "open" : ""}>
        <summary>
          <span class="status-icon ${cls}">${icon(ic, 12)}</span>
          <span class="test-name">${esc(t.test_id)}</span>
          <span class="test-desc">${esc(t.description || "")}</span>
          ${t.is_edge_case ? `<span class="pill">edge</span>` : ""}
          <span class="pill ${cls}">${esc(statusLabel)}</span>
          <span class="test-time">${(t.execution_time_sec || 0).toFixed(2)}s</span>
        </summary>
        <div class="test-body">
          <div class="io-grid">
            ${ioBlock(q.driver_used ? "Input (driver test #" + (i + 1) + ")" : "Input (stdin)", t.input_data)}
            ${ioBlock("Expected output", t.expected_output)}
            ${ioBlock("Actual output", t.actual_output)}
          </div>
          ${t.diff_summary ? `<div class="diff-line">${esc(t.diff_summary)}</div>` : ""}
          ${t.note ? `<div class="note-line">${esc(t.note)}</div>` : ""}
          ${t.stderr && !t.passed ? `<div style="margin-top:10px">${ioBlock("stderr", t.stderr)}</div>` : ""}
        </div>
      </details>`;
    }).join("")}</div>`);
  } else if (cr && cr.success) {
    parts.push(`<div class="section"><div class="callout">${icon("info")}<div>This question has no test cases. Add some under Assignments or with the Test generator.</div></div></div>`);
  }

  // Static checks
  const stat = q.static_results || [];
  if (stat.length) {
    parts.push(`<div class="section"><h3 class="section-title">Static checks</h3>${stat.map(s => {
      const cls = s.passed && s.status === "PASS" ? "pass" : s.passed ? "warn" : (s.status === "PARTIAL" || s.status === "UNCERTAIN" ? "warn" : "fail");
      return `<div class="check-row"><span class="status-icon ${cls}">${icon(cls === "pass" ? "check" : cls === "warn" ? "alert" : "x", 12)}</span>
        <div class="grow"><div class="check-title">${esc(s.description)}</div><div class="check-msg">${esc(s.message)}</div></div>
        <span class="pill ${cls}">${esc(s.status)}</span></div>`;
    }).join("")}</div>`);
  }

  // LLM review
  const lr = q.llm_review;
  if (lr) {
    if (lr.error) {
      parts.push(`<div class="section"><h3 class="section-title">Gemini review</h3><div class="callout warn">${icon("alert")}<div><strong>Not available.</strong> ${esc((lr.issues || [])[0] || lr.error)}</div></div></div>`);
    } else {
      const c = lr.components || {};
      const comp = (name, item, key) => `<div class="comp"><div class="comp-head"><span class="comp-name">${esc(name)}</span>${ratingChip(item?.[key])}</div><div class="comp-text">${esc(item?.feedback || "")}</div></div>`;
      parts.push(`<div class="section"><h3 class="section-title">Gemini review <span class="row-sub" style="text-transform:none;letter-spacing:0">${esc(lr.model_used || "")}</span></h3>
        <div class="comp-grid">
          ${comp("Comp 1 · Conventions & naming", c.comp1_style_conventions, "rating")}
          ${comp("Comp 2 · Basic tests", c.comp2_basic_tests, "status")}
          ${comp("Comp 3 · Logic & border cases", c.comp3_logic_edge_cases, "rating")}
          ${comp("Comp 4 · Comments & submission", c.comp4_comments_submission, "rating")}
        </div>
        ${(lr.issues || []).length ? `<div style="margin-top:12px"><div class="io-label">Observations</div><ul class="list-plain">${lr.issues.map(i => `<li>${esc(i)}</li>`).join("")}</ul></div>` : ""}
        ${(c.extra_comments || []).length ? `<div style="margin-top:12px"><div class="io-label">Extra comments</div><ul class="list-plain">${c.extra_comments.map(i => `<li>${esc(i)}</li>`).join("")}</ul></div>` : ""}
        ${(lr.requirements || []).length ? `<div style="margin-top:12px"><div class="io-label">Rubric</div>${lr.requirements.map(r => `<div class="check-row"><div class="grow"><div class="check-title">${esc(r.requirement)}</div><div class="check-msg">${esc(r.reason)}</div></div>${ratingChip(r.status)}</div>`).join("")}</div>` : ""}
      </div>`);
    }
  }

  // Source
  parts.push(`<div class="section"><h3 class="section-title">Source code <button class="btn btn-sm" data-load-source="${esc(qid)}">${icon("code", 14)}Show source</button></h3><div id="source-${esc(qid)}"></div></div>`);
  return parts.join("");
}

async function loadSource(qid, btn) {
  const p = paths();
  const holder = document.getElementById(`source-${qid}`);
  try {
    const data = await withBusy(btn, "Loading", () => api(`/api/results/${encodeURIComponent(S.drawer.sid)}/source?${qs({ question_id: qid, results_dir: p.results })}`));
    const files = (data.files || []).slice();
    if (data.driver) files.push({ name: `${data.driver.name} (TA driver)`, content: data.driver.content });
    if (!files.length) { holder.innerHTML = `<div class="hint">No source files recorded.</div>`; return; }
    holder.innerHTML = `<div class="file-tabs">${files.map((f, i) => `<button class="btn btn-sm ${i === 0 ? "btn-primary" : ""}" data-file-idx="${i}">${esc(f.name)}</button>`).join("")}</div><pre class="code tall" data-file-view></pre>`;
    const view = $("[data-file-view]", holder);
    const show = i => {
      $$("[data-file-idx]", holder).forEach(b => b.classList.toggle("btn-primary", Number(b.dataset.fileIdx) === i));
      view.textContent = files[i].content ?? "(file no longer available on disk)";
    };
    holder.addEventListener("click", e => { const b = e.target.closest("[data-file-idx]"); if (b) show(Number(b.dataset.fileIdx)); });
    show(0);
    btn.hidden = true;
  } catch (e) {
    holder.innerHTML = `<div class="callout fail">${icon("alert")}<div>${esc(e.message)}</div></div>`;
  }
}

async function loadCompileLog(qid, btn) {
  const p = paths();
  const holder = document.getElementById(`compileLog-${qid}`);
  try {
    const data = await withBusy(btn, "Loading", () => api(`/api/results/${encodeURIComponent(S.drawer.sid)}/compile-log?${qs({ question_id: qid, results_dir: p.results })}`));
    holder.innerHTML = `<pre class="code tall" style="margin-top:8px">${esc(data.log)}</pre>`;
    btn.hidden = true;
  } catch (e) { toast(e.message, "error"); }
}

// ---------------------------------------------------------------- modal

function openModal({ title, subtitle = "", body = "", actions = [], wide = false, onOpen }) {
  const root = $("#modalRoot");
  const layer = document.createElement("div");
  layer.className = "modal-layer";
  layer.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">
    <div class="modal-head"><div><h3 class="modal-title">${esc(title)}</h3>${subtitle ? `<div class="modal-sub">${esc(subtitle)}</div>` : ""}</div>
      <button class="btn btn-ghost btn-icon" data-close title="Close">${icon("x")}</button></div>
    <div class="modal-body">${body}</div>
    ${actions.length ? `<div class="modal-foot">${actions.map((a, i) => `<button class="btn ${a.primary ? "btn-primary" : ""} ${a.danger ? "btn-danger" : ""}" data-action="${i}">${esc(a.label)}</button>`).join("")}</div>` : ""}
  </div>`;
  const close = () => layer.remove();
  layer.addEventListener("mousedown", e => { if (e.target === layer) close(); });
  layer.addEventListener("click", async e => {
    if (e.target.closest("[data-close]")) return close();
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = actions[Number(btn.dataset.action)];
    if (!action.onClick) return close();
    try {
      const keepOpen = await withBusy(btn, action.busyLabel || action.label, () => action.onClick(layer));
      if (keepOpen !== true) close();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  root.appendChild(layer);
  const first = $("input, textarea, select", layer);
  if (first) first.focus();
  if (onOpen) onOpen(layer, close);
  return { layer, close };
}

function confirmDialog(title, message, confirmLabel = "Delete") {
  return new Promise(resolve => {
    const m = openModal({
      title, body: `<p style="margin:0;color:var(--text-2)">${esc(message)}</p>`,
      actions: [
        { label: "Cancel", onClick: () => resolve(false) },
        { label: confirmLabel, danger: true, onClick: () => resolve(true) },
      ],
    });
    m.layer.querySelector("[data-close]").addEventListener("click", () => resolve(false));
  });
}

// ---------------------------------------------------------------- folder picker

function openFolderPicker(inputId, title) {
  const input = document.getElementById(inputId);
  let current = "";
  const m = openModal({
    title, wide: true,
    body: `<div class="chips" data-shortcuts style="margin:0 0 10px"></div>
      <div class="picker-path"><button class="btn btn-sm" data-up>${icon("up", 14)}Up</button><span class="mono" data-path>Loading…</span></div>
      <div class="picker-list" data-list></div>
      <div class="hint" data-zips style="margin-top:8px"></div>
      <div class="input-group" style="margin-top:10px"><input class="input" data-newname placeholder="New folder name"><button class="btn" data-mkdir>${icon("plus", 14)}Create</button></div>`,
    actions: [
      ...(S.status && S.status.platform === "win32" ? [{ label: "Windows dialog…", onClick: async () => {
        const r = await api("/api/system/browse-folder", { method: "POST", body: { initial_dir: current, title } });
        if (r.success && r.path) { input.value = r.path; input.dispatchEvent(new Event("change")); return false; }
        return true;
      } }] : []),
      { label: "Cancel" },
      { label: "Select this folder", primary: true, onClick: () => { input.value = current; input.dispatchEvent(new Event("change")); } },
    ],
  });
  const L = m.layer;
  const load = async path => {
    $("[data-list]", L).innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    try {
      const data = await api(`/api/system/list-dirs?${qs({ path })}`);
      current = data.current_path;
      $("[data-path]", L).textContent = current;
      $("[data-up]", L).disabled = !data.parent_path;
      $("[data-up]", L).onclick = () => data.parent_path && load(data.parent_path);
      $("[data-shortcuts]", L).innerHTML = (data.quick_shortcuts || []).map((s, i) => `<button class="btn btn-sm" data-sc="${i}">${esc(s.label)}</button>`).join("");
      $$("[data-sc]", L).forEach(b => { b.onclick = () => load(data.quick_shortcuts[Number(b.dataset.sc)].path); });
      $("[data-zips]", L).textContent = data.zip_count ? `${data.zip_count} .zip file${data.zip_count === 1 ? "" : "s"} in this folder` : "";
      const dirs = data.directories || [];
      $("[data-list]", L).innerHTML = dirs.length
        ? dirs.map((d, i) => `<button class="picker-item" data-dir="${i}">${icon("folder")}<span>${esc(d)}</span></button>`).join("")
        : `<div class="empty">No subfolders here.</div>`;
      $$("[data-dir]", L).forEach(b => {
        b.onclick = () => {
          const sep = current.includes("\\") ? "\\" : "/";
          load(current.endsWith(sep) ? current + dirs[Number(b.dataset.dir)] : current + sep + dirs[Number(b.dataset.dir)]);
        };
      });
    } catch (e) {
      $("[data-list]", L).innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    }
  };
  $("[data-mkdir]", L).onclick = async () => {
    const name = $("[data-newname]", L).value.trim();
    if (!name) return toast("Enter a folder name.", "error");
    try {
      await api("/api/system/create-dir", { method: "POST", body: { parent_dir: current, folder_name: name } });
      $("[data-newname]", L).value = "";
      load(current);
    } catch (e) { toast(e.message, "error"); }
  };
  load(input.value.trim());
}

// ---------------------------------------------------------------- assignments view

async function renderAssignments() {
  await loadAssignments();
  const list = $("#assignmentsList");
  if (!S.assignments.length) {
    list.innerHTML = `<div class="card"><div class="empty"><div class="empty-title">No assignments yet</div><div>Create one for this week's lab to start grading.</div></div></div>`;
    return;
  }
  list.innerHTML = S.assignments.map(a => {
    const qs_ = a.questions || [];
    return `<div class="card assign-card">
      <div class="card-head">
        <div style="min-width:0">
          <h2 class="card-title">${esc(a.title || a.id)}</h2>
          <div class="assign-id">${esc(a.id)}</div>
          ${a.description ? `<p class="assign-desc">${esc(a.description)}</p>` : ""}
          ${a.error ? `<div class="callout fail" style="margin-top:8px">${icon("alert")}<div>Config error: ${esc(a.error)}</div></div>` : ""}
        </div>
        <div class="btn-row">
          <button class="btn btn-sm" data-a-edit="${esc(a.id)}">${icon("edit", 14)}Edit</button>
          <button class="btn btn-sm" data-a-addq="${esc(a.id)}">${icon("plus", 14)}Question</button>
          <button class="btn btn-sm btn-danger" data-a-del="${esc(a.id)}">${icon("trash", 14)}Delete</button>
        </div>
      </div>
      ${qs_.length ? `<div class="table-wrap"><table class="data">
        <thead><tr><th>ID</th><th>Question</th><th>Language</th><th>Tests</th><th>Timeout</th><th>Flags</th><th></th></tr></thead>
        <tbody>${qs_.map(q => `<tr data-noclick>
          <td class="mono" style="font-weight:600">${esc(q.id.toUpperCase())}</td>
          <td><div style="font-weight:550">${esc(q.name)}</div><div class="row-sub">${esc((q.description || "").slice(0, 140))}</div></td>
          <td><span class="pill">${esc(String(q.language).toUpperCase())}</span></td>
          <td class="num">${(q.test_cases || []).length ? `<span class="pill pass">${q.test_cases.length}</span>` : `<span class="pill warn">0</span>`}</td>
          <td class="num">${esc(q.timeout_sec)}s</td>
          <td class="mono row-sub">${esc((q.compiler_flags || []).join(" "))}</td>
          <td style="text-align:right;white-space:nowrap">
            <button class="btn btn-sm" data-q-tests="${esc(a.id)}|${esc(q.id)}">${icon("beaker", 14)}Tests</button>
            <button class="btn btn-sm btn-ghost btn-icon" data-q-edit="${esc(a.id)}|${esc(q.id)}" title="Edit question">${icon("edit", 14)}</button>
            <button class="btn btn-sm btn-ghost btn-icon btn-danger" data-q-del="${esc(a.id)}|${esc(q.id)}" title="Delete question">${icon("trash", 14)}</button>
          </td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty">No questions yet.</div>`}
    </div>`;
  }).join("");
}

function newAssignmentDialog() {
  openModal({
    title: "New assignment",
    body: `<div class="grid">
      <div class="field"><label>Folder name</label><input class="input mono" data-f="name" placeholder="week_5"><span class="hint">Letters, digits, _ and - only.</span></div>
      <div class="field"><label>Title</label><input class="input" data-f="title" placeholder="Lab 5: Linked lists"></div>
      <div class="field"><label>Description</label><textarea class="textarea prose" data-f="desc" rows="3"></textarea></div>
    </div>`,
    actions: [{ label: "Cancel" }, { label: "Create", primary: true, onClick: async L => {
      const v = k => $(`[data-f="${k}"]`, L).value.trim();
      if (!v("name")) throw new Error("Folder name is required.");
      const r = await api("/api/assignments/new", { method: "POST", body: { name: v("name"), title: v("title"), desc: v("desc") } });
      toast(`Created ${r.id}. It starts with one example question — edit it to match the lab.`, "success");
      await renderAssignments();
    } }],
  });
}

function editAssignmentDialog(id) {
  const a = assignmentById(id);
  const mk = { comp1_max: 3, comp2_max: 3, comp3_max: 6, comp4_max: 3, comp3_test_weight: 0.5, late_penalty: 1, ...((a && a.marking) || {}) };
  openModal({
    title: "Edit assignment", subtitle: id,
    body: `<div class="grid">
      <div class="field"><label>Title</label><input class="input" data-f="title" value="${esc(a?.title || "")}"></div>
      <div class="field"><label>Description</label><textarea class="textarea prose" data-f="desc" rows="3">${esc(a?.description || "")}</textarea></div>
      <div class="label" style="margin-top:4px">Marking scheme</div>
      <div class="grid grid-4">
        ${[["comp1_max", "Comp 1 max"], ["comp2_max", "Comp 2 max"], ["comp3_max", "Comp 3 max"], ["comp4_max", "Comp 4 max"]].map(([k, l]) =>
          `<div class="field"><label>${l}</label><input class="input" type="number" step="0.5" min="0" data-m="${k}" value="${esc(mk[k])}"></div>`).join("")}
        <div class="field" style="grid-column: span 2"><label>Comp 3 share from edge-case tests</label><input class="input" type="number" step="0.1" min="0" max="1" data-m="comp3_test_weight" value="${esc(mk.comp3_test_weight)}"><span class="hint">0.5 = half tests, half Gemini.</span></div>
        <div class="field" style="grid-column: span 2"><label>Late penalty (from Comp 4)</label><input class="input" type="number" step="0.5" min="0" data-m="late_penalty" value="${esc(mk.late_penalty)}"></div>
      </div>
    </div>`,
    actions: [{ label: "Cancel" }, { label: "Save", primary: true, onClick: async L => {
      const marking = Object.fromEntries($$("[data-m]", L).map(i => [i.dataset.m, parseFloat(i.value) || 0]));
      await api(`/api/assignments/${encodeURIComponent(id)}`, { method: "PUT", body: { title: $('[data-f="title"]', L).value.trim(), desc: $('[data-f="desc"]', L).value.trim(), marking } });
      toast("Assignment saved.", "success");
      await renderAssignments();
    } }],
  });
}

function questionForm(q = {}) {
  return `<div class="grid grid-2">
    ${q.id ? "" : `<div class="field"><label>Question ID</label><input class="input mono" data-f="id" placeholder="q1"></div>`}
    <div class="field ${q.id ? "span-2" : ""}"><label>Name</label><input class="input" data-f="name" value="${esc(q.name || "")}" placeholder="Matrix operations"></div>
    <div class="field"><label>Language</label><select class="select" data-f="language">
      <option value="c" ${q.language !== "cpp" ? "selected" : ""}>C (gcc)</option>
      <option value="cpp" ${q.language === "cpp" ? "selected" : ""}>C++ (g++)</option></select></div>
    ${q.id ? `<div class="field"><label>Timeout per test (s)</label><input class="input" type="number" step="0.5" min="0.5" data-f="timeout" value="${esc(q.timeout_sec ?? 2)}"></div>
      <div class="field"><label>Compiler flags</label><input class="input mono" data-f="flags" value="${esc((q.compiler_flags || []).join(" "))}" placeholder="-std=c11 -Wall"></div>
      <div class="field"><label>File name hints</label><input class="input mono" data-f="hints" value="${esc((q.source_hints || []).join(", "))}" placeholder="q1, matrix"><span class="hint">Used to match files to this question in multi-question labs.</span></div>` : ""}
    <div class="field span-2"><label>Description</label><textarea class="textarea prose" data-f="description" rows="4">${esc(q.description || "")}</textarea></div>
  </div>`;
}

function addQuestionDialog(aid) {
  openModal({
    title: "Add question", subtitle: aid, body: questionForm(),
    actions: [{ label: "Cancel" }, { label: "Add question", primary: true, onClick: async L => {
      const v = k => $(`[data-f="${k}"]`, L).value.trim();
      if (!v("id") || !v("name")) throw new Error("Question ID and name are required.");
      await api(`/api/assignments/${encodeURIComponent(aid)}/questions`, { method: "POST", body: { question_id: v("id"), name: v("name"), description: v("description"), language: v("language") } });
      toast("Question added.", "success");
      await renderAssignments();
    } }],
  });
}

function findQuestion(aid, qid) {
  const a = assignmentById(aid);
  return a && (a.questions || []).find(q => q.id === qid);
}

function editQuestionDialog(aid, qid) {
  const q = findQuestion(aid, qid) || { id: qid };
  openModal({
    title: `Edit ${qid.toUpperCase()}`, subtitle: aid, body: questionForm(q), wide: true,
    actions: [{ label: "Cancel" }, { label: "Save", primary: true, onClick: async L => {
      const v = k => $(`[data-f="${k}"]`, L).value.trim();
      await api(`/api/assignments/${encodeURIComponent(aid)}/questions/${encodeURIComponent(qid)}`, {
        method: "PUT",
        body: {
          name: v("name"), description: v("description"), language: v("language"),
          timeout_sec: parseFloat(v("timeout")) || 2,
          compiler_flags: v("flags").split(/\s+/).filter(Boolean),
          source_hints: v("hints").split(",").map(s => s.trim()).filter(Boolean),
        },
      });
      toast("Question saved.", "success");
      await renderAssignments();
    } }],
  });
}

function testForm(t = {}) {
  const mode = t.comparison_mode || "whitespace_normalized";
  const modes = [
    ["whitespace_normalized", "Whitespace-normalized (lenient)"], ["exact", "Exact match"], ["float_tolerance", "Numbers within tolerance"],
    ["token_match", "Tokens in order"], ["sorted_check", "Sorted sequence"], ["regex_match", "Regular expression"],
    ["lines", "Line by line (strict)"],
  ];
  return `<div class="grid grid-4">
    <div class="field"><label>Test ID</label><input class="input mono" data-t="id" value="${esc(t.id || "")}" placeholder="t1_normal"></div>
    <div class="field" style="grid-column: span 3"><label>Description</label><input class="input" data-t="description" value="${esc(t.description || "")}"></div>
    <div class="field" style="grid-column: span 2"><label>Comparison</label><select class="select" data-t="mode">${modes.map(([v, l]) => `<option value="${v}" ${v === mode ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div class="field"><label>Tolerance</label><input class="input" type="number" step="any" data-t="tol" value="${esc(t.float_tolerance ?? 0.0001)}"></div>
    <div class="field"><label>Timeout (s)</label><input class="input" type="number" step="0.5" min="0.5" data-t="timeout" value="${esc(t.timeout_sec ?? 2)}"></div>
    <div class="field"><label>Sort order</label><select class="select" data-t="sort_order">
      <option value="">—</option><option value="ascending" ${t.sort_order === "ascending" ? "selected" : ""}>Ascending</option><option value="descending" ${t.sort_order === "descending" ? "selected" : ""}>Descending</option></select></div>
    <div class="field"><label>Sort type</label><select class="select" data-t="sort_type">
      <option value="">—</option>${["int", "float", "char"].map(x => `<option value="${x}" ${t.sort_type === x ? "selected" : ""}>${x}</option>`).join("")}</select></div>
    <div class="field" style="grid-column: span 2; justify-content:flex-end"><label class="check"><input type="checkbox" data-t="edge" ${t.is_edge_case ? "checked" : ""}> Edge case</label></div>
    <div class="field" style="grid-column: span 2"><label>Input (stdin)</label><textarea class="textarea" data-t="input" rows="6">${esc(t.input_data || "")}</textarea></div>
    <div class="field" style="grid-column: span 2"><label>Expected output</label><textarea class="textarea" data-t="expected" rows="6">${esc(t.expected_output || "")}</textarea></div>
  </div>`;
}

function readTestForm(root) {
  const v = k => $(`[data-t="${k}"]`, root);
  return {
    id: v("id").value.trim(), description: v("description").value.trim(), comparison_mode: v("mode").value,
    float_tolerance: parseFloat(v("tol").value) || 0.0001, timeout_sec: parseFloat(v("timeout").value) || 2,
    sort_order: v("sort_order").value || null, sort_type: v("sort_type").value || null,
    is_edge_case: v("edge").checked, input_data: v("input").value, expected_output: v("expected").value,
  };
}

async function testsDialog(aid, qid) {
  let tests = [];
  const m = openModal({ title: `Tests · ${qid.toUpperCase()}`, subtitle: aid, wide: true, body: `<div data-tests><div class="empty"><span class="spinner"></span></div></div>`, actions: [{ label: "Close" }] });
  const L = m.layer;
  const base = `/api/assignments/${encodeURIComponent(aid)}/questions/${encodeURIComponent(qid)}/tests`;

  const renderList = () => {
    $("[data-tests]", L).innerHTML = `<div class="btn-row" style="margin-bottom:12px"><button class="btn btn-primary btn-sm" data-add>${icon("plus", 14)}Add test</button><span class="hint">${tests.length} test${tests.length === 1 ? "" : "s"} · every student runs these exact cases</span></div>
      ${tests.length ? tests.map((t, i) => `<div class="test-card">
        <div class="test-card-head"><div><span class="test-name">${esc(t.id)}</span> <span class="row-sub">${esc(t.description || "")}</span></div>
          <div class="btn-row"><span class="pill info">${esc(t.comparison_mode)}</span>${t.is_edge_case ? `<span class="pill">edge</span>` : ""}
            <button class="btn btn-sm" data-edit="${i}">${icon("edit", 14)}Edit</button>
            <button class="btn btn-sm btn-danger" data-del="${i}">${icon("trash", 14)}</button></div></div>
        <div class="io-grid-2">${ioBlock("Input", t.input_data)}${ioBlock("Expected", t.expected_output)}</div>
      </div>`).join("") : `<div class="empty">No tests yet.</div>`}`;
    $("[data-add]", L).onclick = () => renderEditor(null);
    $$("[data-edit]", L).forEach(b => { b.onclick = () => renderEditor(tests[Number(b.dataset.edit)]); });
    $$("[data-del]", L).forEach(b => {
      b.onclick = async () => {
        const t = tests[Number(b.dataset.del)];
        if (!(await confirmDialog("Delete test", `Delete test '${t.id}'?`))) return;
        try {
          const r = await api(`${base}/${encodeURIComponent(t.id)}`, { method: "DELETE" });
          tests = r.test_cases; renderList(); renderAssignments();
        } catch (e) { toast(e.message, "error"); }
      };
    });
  };

  const renderEditor = t => {
    $("[data-tests]", L).innerHTML = `${testForm(t || { id: `t${tests.length + 1}` })}
      <div class="btn-row" style="margin-top:14px;justify-content:flex-end"><button class="btn" data-back>Back</button><button class="btn btn-primary" data-save>Save test</button></div>`;
    $("[data-back]", L).onclick = renderList;
    $("[data-save]", L).onclick = async e => {
      const body = readTestForm(L);
      if (!body.id) return toast("Test ID is required.", "error");
      if (t) body.original_id = t.id;
      try {
        const r = await withBusy(e.currentTarget, "Saving", () => api(base, { method: "POST", body }));
        tests = r.test_cases; toast(r.message, "success"); renderList(); renderAssignments();
      } catch (err) { toast(err.message, "error"); }
    };
  };

  try {
    tests = (await api(base)).test_cases || [];
    renderList();
  } catch (e) {
    $("[data-tests]", L).innerHTML = `<div class="callout fail">${icon("alert")}<div>${esc(e.message)}</div></div>`;
  }
}

// ---------------------------------------------------------------- generator view

async function refreshGenerator() {
  if (!S.assignments.length) await loadAssignments();
  const aid = $("#genAssignment").value;
  const a = assignmentById(aid);
  const qsel = $("#genQuestion");
  const prev = qsel.value;
  qsel.innerHTML = (a?.questions || []).map(q => `<option value="${esc(q.id)}">${esc(q.id.toUpperCase())} · ${esc(q.name)} (${(q.test_cases || []).length} tests)</option>`).join("") || `<option value="">No questions</option>`;
  if (prev && (a?.questions || []).some(q => q.id === prev)) qsel.value = prev;
  renderGenList();
}

function renderGenList() {
  const q = findQuestion($("#genAssignment").value, $("#genQuestion").value);
  const tests = q?.test_cases || [];
  const prompt = $("#genPrompt");
  if (q && !prompt.dataset.touched) prompt.value = q.description || "";
  $("#genListTitle").textContent = q ? `Current tests · ${q.id.toUpperCase()}` : "Current tests";
  $("#genListCount").textContent = `${tests.length} test${tests.length === 1 ? "" : "s"}`;
  $("#genList").innerHTML = tests.length ? tests.map(t => `<div class="test-card">
      <div class="test-card-head"><div><span class="test-name">${esc(t.id)}</span> <span class="row-sub">${esc(t.description || "")}</span></div>
        <div class="btn-row"><span class="pill info">${esc(t.comparison_mode)}</span>${t.is_edge_case ? `<span class="pill">edge</span>` : ""}</div></div>
      <div class="io-grid-2">${ioBlock("Input", t.input_data)}${ioBlock("Expected", t.expected_output)}</div>
    </div>`).join("")
    : `<div class="empty">No tests for this question yet.</div>`;
}

async function generateTests() {
  const aid = $("#genAssignment").value, qid = $("#genQuestion").value;
  const prompt = $("#genPrompt").value.trim();
  const count = Math.max(1, Math.min(12, parseInt($("#genCount").value, 10) || 4));
  if (!aid || !qid) return toast("Pick an assignment and question.", "error");
  if (!prompt) return toast("Describe the problem first.", "error");
  const existing = (findQuestion(aid, qid)?.test_cases || []).length;
  if (existing && !(await confirmDialog("Replace tests?", `${qid.toUpperCase()} already has ${existing} test(s). Generated tests will replace them.`, "Replace"))) return;

  const btn = $("#generateBtn");
  const started = Date.now();
  const timer = setInterval(() => { $("#genHint").textContent = `Waiting for Gemini… ${Math.round((Date.now() - started) / 1000)}s`; }, 1000);
  try {
    const r = await withBusy(btn, "Generating", () => api("/api/assignments/generate-tests", { method: "POST", body: { assignment_id: aid, question_id: qid, problem_description: prompt, num_tests: count } }));
    toast(r.message, "success");
    await loadAssignments();
    refreshGenerator();
  } catch (e) {
    toast(e.message, "error", 8000);
  } finally {
    clearInterval(timer);
    $("#genHint").textContent = "";
  }
}

// ---------------------------------------------------------------- settings view

async function renderSettings() {
  await loadStatus();
  const st = S.status;
  if (!st) return;
  const sacText = { on: "On (enforcing)", evaluation: "Evaluation mode", off: "Off", unknown: "Unknown" }[st.smart_app_control] || st.smart_app_control;
  $("#backendNow").textContent = `Currently: ${st.exec_backend === "wsl" ? "WSL" : "native"}`;

  let callout = "";
  if (st.platform === "win32" && st.smart_app_control === "on" && st.exec_backend === "native") {
    callout = `<div class="callout warn">${icon("alert")}<div><strong>Smart App Control is blocking freshly compiled programs.</strong> Windows refuses to start most newly built .exe files (WinError 4551), so tests fail before they run. ${st.wsl_available ? "Switch to Auto or WSL below." : "Install WSL with gcc (<span class='mono'>wsl --install</span>, then <span class='mono'>sudo apt install build-essential</span>) or turn Smart App Control off in Windows Security."}</div></div>`;
  } else if (st.platform === "win32" && st.smart_app_control === "on") {
    callout = `<div class="callout info">${icon("info")}<div>Smart App Control is on, which blocks unsigned .exe files. Student code is built and run inside WSL instead, so it is not affected.</div></div>`;
  }
  $("#sacCallout").innerHTML = callout;

  const opts = [
    ["auto", "Auto (recommended)", "Use WSL when it is available, otherwise the Windows toolchain. Programs that need conio.h / windows.h or MSVC-only functions are rebuilt natively automatically."],
    ["wsl", "WSL", "Always compile and run inside WSL (Linux gcc/g++). Not affected by Smart App Control or antivirus scans; enforces memory and output limits."],
    ["native", "Native Windows", "Use the local MinGW / w64devkit gcc. Fastest, but Smart App Control and Defender may block or slow down new executables."],
  ];
  $("#backendOptions").innerHTML = opts.map(([v, t, d]) => `<label class="option-card ${st.exec_backend_setting === v ? "selected" : ""}">
    <input type="radio" name="backend" value="${v}" ${st.exec_backend_setting === v ? "checked" : ""} ${v === "wsl" && !st.wsl_available ? "disabled" : ""}>
    <div><div class="option-title">${t}${v === "wsl" && !st.wsl_available ? ` <span class="pill">not installed</span>` : ""}</div><div class="option-desc">${d}</div></div></label>`).join("");

  $("#studentsInput").value = st.students || "";
  $("#studentsNow").textContent = st.students ? `Grading only ${st.students_summary}` : "Grading everyone";

  $("#toolchainInfo").innerHTML = `
    <dt>Smart App Control</dt><dd>${esc(sacText)}</dd>
    <dt>WSL toolchain</dt><dd>${st.wsl_available ? `gcc ${esc(st.wsl_gcc_version)}` : "Not available"}</dd>
    <dt>Native gcc</dt><dd class="mono">${esc(st.gcc_path)}</dd>
    <dt>Native g++</dt><dd class="mono">${esc(st.gxx_path)}</dd>
    <dt>Project folder</dt><dd class="mono">${esc(st.project_root)}</dd>`;

  $("#providerSelect").value = st.llm_provider;
  $("#llmReviewToggle").checked = !!st.llm_review;
  $("#llmNow").textContent = st.llm_configured ? `${st.llm_provider === "groq" ? "Groq" : "Gemini"} · key ${st.llm_key_preview}` : "No key configured";
  $("#keyHint").textContent = st.llm_configured ? `Current key: ${st.llm_key_preview}. Stored in .env.` : "Without a key, compile/test/concept checks still run; TA drivers and the LLM review are skipped.";

  const msel = $("#modelSelect");
  msel.innerHTML = `<option value="${esc(st.llm_model)}">${esc(st.llm_model)}</option>`;
  try {
    const data = await api("/api/system/models");
    if (data.models && data.models.length) {
      const ids = data.models.map(m => m.id);
      if (!ids.includes(st.llm_model)) ids.unshift(st.llm_model);
      msel.innerHTML = ids.map(id => `<option value="${esc(id)}" ${id === st.llm_model ? "selected" : ""}>${esc(id)}</option>`).join("");
      $("#modelHint").textContent = data.provider === "groq"
        ? `${data.models.length} models. Free tier: about 8k tokens per minute per model; requests are paced automatically.`
        : `${data.models.length} models available to this key. Flash models are fastest for batch grading.`;
    } else {
      $("#modelHint").textContent = data.error ? `Could not list models: ${data.error}` : "No models returned.";
    }
  } catch (e) {
    $("#modelHint").textContent = e.message;
  }
}

async function saveBackend(value) {
  try {
    await api("/api/system/settings", { method: "POST", body: { exec_backend: value } });
    toast(`Execution backend set to ${value}.`, "success");
    renderSettings();
  } catch (e) { toast(e.message, "error"); }
}

async function saveStudents() {
  await withBusy($("#saveStudentsBtn"), "Saving", async () => {
    try {
      await api("/api/system/settings", { method: "POST", body: { students: $("#studentsInput").value.trim() } });
      toast("Saved. Only these students will be downloaded and graded.", "success");
      await renderSettings();
      updatePathHint();
      loadResults();
    } catch (e) { toast(e.message, "error", 8000); }
  });
}

async function saveLlmSettings() {
  const providerChanged = $("#providerSelect").value !== (S.status && S.status.llm_provider);
  const body = { llm_provider: $("#providerSelect").value, llm_review: $("#llmReviewToggle").checked };
  if (!providerChanged) body.llm_model = $("#modelSelect").value;
  const key = $("#apiKeyInput").value.trim();
  if (key) body.llm_api_key = key;
  await withBusy($("#saveLlmBtn"), "Saving", async () => {
    try {
      await api("/api/system/settings", { method: "POST", body });
      $("#apiKeyInput").value = "";
      toast("AI settings saved.", "success");
      renderSettings();
    } catch (e) { toast(e.message, "error"); }
  });
}

// ---------------------------------------------------------------- Google Classroom

const CR = { status: null, courses: [], work: [], table: null, polling: null };

const CR_STATES = {
  TURNED_IN: ["pass", "Turned in"], RETURNED: ["info", "Returned"], RECLAIMED_BY_STUDENT: ["warn", "Unsubmitted"],
  CREATED: ["", "Not submitted"], NEW: ["", "Not submitted"],
};
const CR_LOCAL = {
  new: ["accent", "New"], updated: ["warn", "Resubmitted"], up_to_date: ["pass", "Downloaded"], kept: ["", "Kept (unsubmitted)"], none: ["", "—"],
};

async function crRefreshStatus() {
  try { CR.status = await api("/api/classroom/status"); } catch (e) { CR.status = null; }
  updateSyncButton();
  return CR.status;
}

function updateSyncButton() {
  const link = CR.status && CR.status.connected && CR.status.links ? CR.status.links[currentAssignment()] : null;
  const btn = $("#syncGradeBtn");
  btn.hidden = !link;
  if (link) btn.title = `Download new and resubmitted work for “${link.coursework_title || "linked classwork"}” from Classroom, then grade`;
}

async function crInit() {
  const st = await crRefreshStatus();
  const setup = $("#crSetup"), main = $("#crMain"), acct = $("#crAccount");
  if (!st) { setup.innerHTML = `<div class="callout fail">${icon("alert")}<div>Could not reach the server.</div></div>`; return; }
  acct.innerHTML = st.connected
    ? `<span class="pill pass">${icon("check", 12)}${esc(st.account_email || "Connected")}</span><button class="btn btn-sm" id="crLogout">Disconnect</button>`
    : "";
  if ($("#crLogout")) $("#crLogout").onclick = async () => { await api("/api/classroom/logout", { method: "POST" }); toast("Disconnected from Google.", "info"); crInit(); };

  if (st.connected) {
    setup.innerHTML = "";
    main.hidden = false;
    $("#crPattern").value = st.id_pattern;
    crFillAssignmentSelect();
    if (!CR.courses.length) await crLoadCourses();
    return;
  }
  main.hidden = true;
  const step = (n, title, body) => `<div class="check-row"><span class="status-icon pass" style="background:var(--accent-soft);color:var(--accent-text);font-size:11px;font-weight:700">${n}</span><div class="grow"><div class="check-title">${title}</div><div class="check-msg">${body}</div></div></div>`;
  setup.innerHTML = `<div class="card">
    <div class="card-head"><h2 class="card-title">One-time setup</h2><span class="card-sub">About 5 minutes · uses read-only access</span></div>
    <div class="card-pad">
      ${st.client_configured ? "" : `
      <p style="margin:0 0 12px;color:var(--text-2)">Google requires every app that reads Classroom to have its own sign-in credentials. Create them once in Google Cloud (free):</p>
      ${step(1, "Create a Google Cloud project", `<a href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noopener">console.cloud.google.com/projectcreate</a> — sign in with the account you use for Classroom.`)}
      ${step(2, "Enable two APIs", `<a href="https://console.cloud.google.com/apis/library/classroom.googleapis.com" target="_blank" rel="noopener">Google Classroom API</a> and <a href="https://console.cloud.google.com/apis/library/drive.googleapis.com" target="_blank" rel="noopener">Google Drive API</a> → Enable.`)}
      ${step(3, "Configure the consent screen", `<a href="https://console.cloud.google.com/auth/branding" target="_blank" rel="noopener">Google Auth Platform</a> → choose <strong>Internal</strong> if your institute account offers it; otherwise <strong>External</strong>, and add your own email under <em>Audience → Test users</em>.`)}
      ${step(4, "Create a Desktop OAuth client", `<a href="https://console.cloud.google.com/auth/clients/create" target="_blank" rel="noopener">Clients → Create client</a> → Application type <strong>Desktop app</strong> → Create → <strong>Download JSON</strong>.`)}
      ${step(5, "Upload the JSON here", `<input type="file" accept=".json,application/json" id="crClientFile" style="margin-top:6px">`)}`}
      <div class="btn-row" style="margin-top:14px">
        <button class="btn btn-primary" id="crLoginBtn" ${st.client_configured ? "" : "disabled"}>${st.login_running ? `<span class="spinner"></span>Waiting for Google sign-in…` : `${icon("link")}Sign in with Google`}</button>
        ${st.client_configured ? `<span class="hint">Credentials uploaded. Sign in opens a Google page in your browser.</span><label class="btn btn-sm btn-ghost">Replace credentials<input type="file" accept=".json" id="crClientFile" hidden></label>` : ""}
      </div>
      ${st.login_error ? `<div class="callout fail" style="margin-top:12px">${icon("alert")}<div>${esc(st.login_error)}</div></div>` : ""}
      <div class="callout info" style="margin-top:14px">${icon("info")}<div>The app only asks for <strong>read-only</strong> access to your courses, rosters, submissions and their Drive files. Your sign-in token is stored locally in <span class="mono">.secrets/</span> inside the project folder.</div></div>
    </div></div>`;

  const fileInput = $("#crClientFile");
  if (fileInput) fileInput.onchange = async () => {
    const f = fileInput.files[0];
    if (!f) return;
    try {
      await api("/api/classroom/client-secrets", { method: "POST", body: { content: await f.text() } });
      toast("Credentials saved. Now sign in with Google.", "success");
      crInit();
    } catch (e) { toast(e.message, "error", 8000); }
  };
  $("#crLoginBtn").onclick = async () => {
    try {
      await api("/api/classroom/login", { method: "POST" });
      toast("Finish signing in on the Google page that just opened.", "info", 8000);
      crWaitForLogin();
    } catch (e) { toast(e.message, "error"); }
  };
  if (st.login_running) crWaitForLogin();
}

function crWaitForLogin() {
  const btn = $("#crLoginBtn");
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner"></span>Waiting for Google sign-in…`; }
  const timer = setInterval(async () => {
    const st = await crRefreshStatus();
    if (!st || st.login_running) return;
    clearInterval(timer);
    if (st.connected) toast(`Signed in${st.account_email ? " as " + st.account_email : ""}.`, "success");
    crInit();
  }, 1500);
}

function crFillAssignmentSelect() {
  const sel = $("#crAssignment");
  const prev = sel.value || store.get("ta.cr.last", {}).assignment || currentAssignment();
  sel.innerHTML = `<option value="">— don't link —</option>` + S.assignments.map(a => `<option value="${esc(a.id)}">${esc(a.title || a.id)}</option>`).join("");
  if (prev && S.assignments.some(a => a.id === prev)) sel.value = prev;
}

async function crLoadCourses() {
  const sel = $("#crCourse");
  sel.innerHTML = `<option>Loading courses…</option>`;
  try {
    CR.courses = (await api("/api/classroom/courses")).courses || [];
  } catch (e) {
    sel.innerHTML = `<option value="">Could not load courses</option>`;
    return toast(e.message, "error", 9000);
  }
  if (!CR.courses.length) { sel.innerHTML = `<option value="">No active courses where you are a teacher</option>`; return; }
  sel.innerHTML = CR.courses.map(c => `<option value="${esc(c.id)}">${esc(c.name)}${c.section ? " · " + esc(c.section) : ""}</option>`).join("");
  const last = store.get("ta.cr.last", {});
  if (last.course && CR.courses.some(c => c.id === last.course)) sel.value = last.course;
  await crLoadWork();
}

async function crLoadWork() {
  const sel = $("#crWork");
  const courseId = $("#crCourse").value;
  if (!courseId) return;
  sel.innerHTML = `<option>Loading…</option>`;
  try {
    CR.work = (await api(`/api/classroom/courses/${encodeURIComponent(courseId)}/coursework`)).coursework || [];
  } catch (e) { sel.innerHTML = `<option value="">Could not load classwork</option>`; return toast(e.message, "error", 9000); }
  sel.innerHTML = CR.work.length
    ? CR.work.map(w => `<option value="${esc(w.id)}">${esc(w.title)}${w.due ? ` · due ${esc(w.due)}` : ""}</option>`).join("")
    : `<option value="">No published classwork</option>`;
  // Prefer the classwork already linked to the chosen pack, then the last one used
  const link = (CR.status.links || {})[$("#crAssignment").value];
  const last = store.get("ta.cr.last", {});
  const want = (link && link.course_id === courseId && link.coursework_id) || last.work;
  if (want && CR.work.some(w => w.id === want)) sel.value = want;
  crSyncDestFromAssignment();
  crUpdateOpenLink();
}

function crUpdateOpenLink() {
  const w = CR.work.find(x => x.id === $("#crWork").value);
  const a = $("#crOpenLink");
  a.hidden = !(w && w.link);
  if (w && w.link) a.href = w.link;
}

function crSyncDestFromAssignment() {
  const aid = $("#crAssignment").value;
  const link = (CR.status.links || {})[aid];
  const saved = aid ? (store.get(`ta.paths.${aid}`) || {}).submissions : "";
  if (link && link.dest) $("#crDest").value = link.dest;
  else if (saved) $("#crDest").value = saved;
}

function crRememberSelection() {
  store.set("ta.cr.last", { course: $("#crCourse").value, work: $("#crWork").value, assignment: $("#crAssignment").value });
  // The download folder doubles as the pack's submissions folder on the Grade page
  const aid = $("#crAssignment").value, dest = $("#crDest").value.trim();
  if (aid && dest) {
    const p = store.get(`ta.paths.${aid}`) || { reports: "reports", results: "results" };
    store.set(`ta.paths.${aid}`, { ...p, submissions: dest });
    if (aid === currentAssignment()) { $("#submissionsPath").value = dest; updatePathHint(); }
  }
}

async function crLoadSubmissions(btn) {
  const courseId = $("#crCourse").value, workId = $("#crWork").value;
  if (!courseId || !workId) return toast("Pick a course and classwork.", "error");
  crRememberSelection();
  const run = async () => {
    CR.table = await api(`/api/classroom/submissions?${qs({ course_id: courseId, coursework_id: workId, dest: $("#crDest").value.trim(), id_pattern: $("#crPattern").value })}`);
    crRenderTable();
  };
  try { btn ? await withBusy(btn, "Loading", run) : await run(); } catch (e) { toast(e.message, "error", 9000); }
}

function crRenderTable() {
  const t = CR.table;
  if (!t) return;
  $("#crTableCard").hidden = false;
  const c = t.counts;
  $("#crCounts").innerHTML = [
    `<span class="pill">${c.students} students</span>`,
    `<span class="pill pass">${c.submitted} submitted</span>`,
    c.late ? `<span class="pill warn">${c.late} late</span>` : "",
    `<span class="pill">${c.missing} not submitted</span>`,
    c.no_id ? `<span class="pill fail">${c.no_id} need a roll ID</span>` : "",
    c.duplicates ? `<span class="pill fail">${c.duplicates} duplicate IDs</span>` : "",
  ].join("");
  $("#crSyncBtn").innerHTML = `${icon("download")}Download new &amp; updated (${c.to_download})`;
  $("#crSyncBtn").disabled = !c.to_download;
  const others = $("#crShowOthers");
  others.hidden = !c.out_of_scope;
  others.textContent = CR.showOthers ? `Hide ${c.out_of_scope} other students` : `Show ${c.out_of_scope} other students`;
  if (c.out_of_scope) $("#crCounts").insertAdjacentHTML("afterbegin", `<span class="pill accent" title="Set in Settings → My students">${esc(t.scope)}</span>`);

  $("#crBody").innerHTML = t.rows.map((r, i) => {
    if (!r.in_scope && !CR.showOthers) return "";
    const [scls, slabel] = CR_STATES[r.state] || ["", r.state];
    const [lcls, llabel] = CR_LOCAL[r.local] || ["", r.local];
    const idCls = !r.roll_id && r.submitted ? "border-color:var(--fail)" : r.duplicate_id ? "border-color:var(--warn)" : "";
    const turned = r.turned_in_at ? new Date(r.turned_in_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
    const preTick = r.in_scope && (r.local === "new" || r.local === "updated");
    return `<tr data-noclick ${r.in_scope ? "" : `style="opacity:.55"`}>
      <td><input type="checkbox" data-cr-pick="${i}" ${r.submitted ? "" : "disabled"} ${preTick ? "checked" : ""}></td>
      <td><div style="font-weight:550">${esc(r.name)}${r.in_scope ? "" : ` <span class="pill">not yours</span>`}</div><div class="row-sub mono">${esc(r.email)}</div></td>
      <td><input class="input mono" style="padding:5px 8px;min-width:104px;${idCls}" data-cr-id="${i}" value="${esc(r.roll_id || "")}" placeholder="${r.submitted ? "required" : ""}" title="${r.duplicate_id ? "Another student has the same ID" : "Saved file name; edits are remembered for this email"}"></td>
      <td><span class="pill ${scls}">${esc(slabel)}</span>${r.late ? ` <span class="pill warn">Late</span>` : ""}<div class="row-sub">${esc(turned)}</div></td>
      <td class="row-sub">${r.attachments.map(a => esc(a.title)).join("<br>") || "—"}</td>
      <td><span class="pill ${lcls}">${esc(llabel)}</span></td>
    </tr>`;
  }).join("") || `<tr><td colspan="6"><div class="empty">No students on this course roster.</div></td></tr>`;
  $("#crSelectAll").checked = false;
}

function crCollectOverrides() {
  const overrides = {};
  $$("[data-cr-id]").forEach(inp => {
    const r = CR.table.rows[Number(inp.dataset.crId)];
    const v = inp.value.trim();
    if (v !== (r.roll_id || "")) overrides[r.email] = v;
  });
  return overrides;
}

async function crStartSync({ onlyEmails = null, then = null, course, work, dest, assignment, courseName, workTitle } = {}) {
  const body = {
    course_id: course, coursework_id: work, dest, only_emails: onlyEmails,
    overrides: CR.table && $("#crMain") && !$("#crMain").hidden ? crCollectOverrides() : null,
    assignment_id: assignment || null, course_name: courseName, coursework_title: workTitle,
  };
  const data = await api("/api/classroom/sync", { method: "POST", body });
  if (!data.success) throw new Error(data.message);
  return new Promise(resolve => {
    if (CR.polling) clearInterval(CR.polling);
    CR.polling = setInterval(async () => {
      let st;
      try { st = await api("/api/classroom/sync/status"); } catch (e) { return; }
      if (then) then(st);
      if (st.running) return;
      clearInterval(CR.polling);
      CR.polling = null;
      resolve(st);
    }, 900);
  });
}

function crShowProgress(st) {
  $("#crProgress").hidden = false;
  $("#crProgressLabel").textContent = st.message;
  $("#crProgressCount").textContent = st.total ? `${st.done} / ${st.total}` : "";
  $("#crProgressBar").style.width = st.total ? `${Math.round((st.done / st.total) * 100)}%` : (st.running ? "10%" : "100%");
  $("#crLog").textContent = (st.log || []).join("\n");
}

async function crSync(onlyEmails, btn) {
  const dest = $("#crDest").value.trim();
  if (!dest) return toast("Choose a download folder.", "error");
  crRememberSelection();
  const course = CR.courses.find(c => c.id === $("#crCourse").value);
  const work = CR.work.find(w => w.id === $("#crWork").value);
  try {
    const st = await withBusy(btn, "Downloading", () => crStartSync({
      onlyEmails, course: $("#crCourse").value, work: $("#crWork").value, dest, assignment: $("#crAssignment").value,
      courseName: course && course.name, workTitle: work && work.title, then: crShowProgress,
    }));
    crShowProgress(st);
    const r = st.result || {};
    const n = (r.downloaded || []).length + (r.updated || []).length;
    toast(st.message, (r.errors || []).length ? "error" : "success", 7000);
    if ((r.unsupported || []).length) toast(`Not a .zip, extract by hand: ${r.unsupported.join(", ")}`, "error", 10000);
    $("#crGradeBtn").hidden = !(n && $("#crAssignment").value);
    await crRefreshStatus();
    await crLoadSubmissions();
  } catch (e) { toast(e.message, "error", 8000); }
}

function crGradeNow() {
  const aid = $("#crAssignment").value;
  if (!aid) return toast("Pick the assignment pack to grade with.", "error");
  $("#assignmentSelect").value = aid;
  store.set("ta.lastAssignment", aid);
  S.mode = "default";
  $$("#modeSeg button").forEach(x => x.classList.toggle("active", x.dataset.mode === "default"));
  showView("grade");
  loadPaths(aid).then(() => loadResults()).then(startGrading);
}

// One click on the Grade page: pull new/resubmitted work for the linked classwork, then grade
async function syncAndGrade() {
  const link = CR.status && CR.status.links ? CR.status.links[currentAssignment()] : null;
  if (!link) return;
  $("#submissionsPath").value = link.dest;
  savePaths();
  updatePathHint();
  $("#progressPanel").hidden = false;
  try {
    const st = await withBusy($("#syncGradeBtn"), "Syncing", () => crStartSync({
      course: link.course_id, work: link.coursework_id, dest: link.dest, assignment: link.assignment_id,
      courseName: link.course_name, workTitle: link.coursework_title,
      then: s => {
        $("#progressLabel").textContent = `Classroom: ${s.message}`;
        $("#progressCount").textContent = s.total ? `${s.done} / ${s.total}` : "";
        $("#progressBar").style.width = s.total ? `${Math.round((s.done / s.total) * 100)}%` : "";
      },
    }));
    const r = st.result || {};
    if ((r.errors || []).length && !(r.downloaded || []).length && !(r.updated || []).length && st.message.startsWith("Error")) {
      return toast(st.message, "error", 9000);
    }
    toast(st.message, "info");
    await loadResults();
    startGrading();
  } catch (e) { toast(e.message, "error", 8000); }
}

// ---------------------------------------------------------------- theme

function applyThemeLabel() {
  const t = document.documentElement.dataset.theme;
  const dark = t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  $("#themeToggle").innerHTML = `${icon(dark ? "sun" : "moon")}<span>${dark ? "Light mode" : "Dark mode"}</span>`;
}

function toggleTheme() {
  const t = document.documentElement.dataset.theme;
  const dark = t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("ta.theme", document.documentElement.dataset.theme); } catch (e) { /* ignore */ }
  applyThemeLabel();
}

// ---------------------------------------------------------------- wiring

function wire() {
  $$(".nav-item[data-view]").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));
  $("#themeToggle").addEventListener("click", toggleTheme);

  // Grade view
  $("#assignmentSelect").addEventListener("change", () => {
    store.set("ta.lastAssignment", currentAssignment());
    updateSyncButton();
    loadPaths(currentAssignment()).then(() => loadResults());
  });
  ["submissionsPath", "reportsDir", "resultsDir"].forEach(id => {
    document.getElementById(id).addEventListener("change", () => { savePaths(); updatePathHint(); loadResults(); });
  });
  $$("[data-browse]").forEach(b => b.addEventListener("click", () => openFolderPicker(b.dataset.browse, b.dataset.title)));
  $("#modeSeg").addEventListener("click", e => {
    const b = e.target.closest("button[data-mode]");
    if (!b) return;
    S.mode = b.dataset.mode;
    $$("#modeSeg button").forEach(x => x.classList.toggle("active", x === b));
  });
  $("#startBtn").addEventListener("click", startGrading);
  $("#cancelBtn").addEventListener("click", cancelGrading);
  $("#refreshBtn").addEventListener("click", () => loadResults({ quiet: false }));
  $("#exportBtn").addEventListener("click", exportCsv);
  $("#searchInput").addEventListener("input", e => { S.search = e.target.value; renderTable(); });

  const setFilter = f => {
    S.filter = f;
    $$("#filterSeg button").forEach(x => x.classList.toggle("active", x.dataset.filter === f));
    renderTable();
  };
  $("#filterSeg").addEventListener("click", e => { const b = e.target.closest("button[data-filter]"); if (b) setFilter(b.dataset.filter); });
  $("#statsRow").addEventListener("click", e => { const s = e.target.closest(".stat"); if (s) setFilter(S.filter === s.dataset.filter ? "all" : s.dataset.filter); });

  $$("th.sortable").forEach(th => th.addEventListener("click", () => {
    S.sort = { key: th.dataset.sort, dir: S.sort.key === th.dataset.sort ? -S.sort.dir : 1 };
    store.set("ta.sort", S.sort);
    renderTable();
  }));

  $("#resultsBody").addEventListener("click", e => {
    const g = e.target.closest("[data-grade]");
    if (g) { e.stopPropagation(); return gradeOne(g.dataset.grade, g); }
    const rc = e.target.closest("[data-recheck]");
    if (rc) { e.stopPropagation(); return gradeOne(rc.dataset.recheck, rc); }
    const tr = e.target.closest("tr[data-sid]");
    if (!tr) return;
    if (tr.dataset.pending) return toast(`${tr.dataset.sid} has not been graded yet — click Grade.`, "info");
    openStudent(tr.dataset.sid);
  });

  // Drawer
  $("#scrim").addEventListener("click", closeDrawer);
  $("#drawerClose").addEventListener("click", closeDrawer);
  $("#drawerRecheck").addEventListener("click", e => S.drawer.sid && gradeOne(S.drawer.sid, e.currentTarget));
  const copyReport = () => copyText(S.drawer.report || "").then(() => toast("Report copied.", "success"), () => toast("Could not copy.", "error"));
  $("#drawerCopy").addEventListener("click", copyReport);
  $("#drawerTabs").addEventListener("click", e => { const t = e.target.closest("[data-tab]"); if (t) { S.drawer.tab = t.dataset.tab; renderDrawer(); } });
  $("#drawerBody").addEventListener("click", e => {
    const go = e.target.closest("[data-goto]");
    if (go) { S.drawer.tab = go.dataset.goto; return renderDrawer(); }
    const src = e.target.closest("[data-load-source]");
    if (src) return loadSource(src.dataset.loadSource, src);
    const log = e.target.closest("[data-compile-log]");
    if (log) return loadCompileLog(log.dataset.compileLog, log);
    if (e.target.closest("[data-copy-report]")) return copyReport();
    const ms = e.target.closest("[data-marks-save]");
    if (ms) return saveMarks("save", ms);
    const ma = e.target.closest("[data-marks-accept]");
    if (ma) return saveMarks("accept", ma);
    const mc = e.target.closest("[data-marks-clear]");
    if (mc) return saveMarks("clear", mc);
  });

  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    const layers = $$(".modal-layer");
    if (layers.length) return layers[layers.length - 1].remove();
    if ($("#drawer").classList.contains("open")) closeDrawer();
  });

  // Assignments view
  $("#newAssignmentBtn").addEventListener("click", newAssignmentDialog);
  $("#assignmentsList").addEventListener("click", async e => {
    const b = e.target.closest("button");
    if (!b) return;
    const d = b.dataset;
    if (d.aEdit) return editAssignmentDialog(d.aEdit);
    if (d.aAddq) return addQuestionDialog(d.aAddq);
    if (d.aDel) {
      if (!(await confirmDialog("Delete assignment", `Delete '${d.aDel}' and all its questions and tests? This cannot be undone.`))) return;
      try { await api(`/api/assignments/${encodeURIComponent(d.aDel)}`, { method: "DELETE" }); toast("Assignment deleted.", "success"); renderAssignments(); }
      catch (err) { toast(err.message, "error"); }
      return;
    }
    const pair = d.qTests || d.qEdit || d.qDel;
    if (!pair) return;
    const [aid, qid] = pair.split("|");
    if (d.qTests) return testsDialog(aid, qid);
    if (d.qEdit) return editQuestionDialog(aid, qid);
    if (d.qDel) {
      if (!(await confirmDialog("Delete question", `Delete ${qid.toUpperCase()} from '${aid}' including its tests?`))) return;
      try { await api(`/api/assignments/${encodeURIComponent(aid)}/questions/${encodeURIComponent(qid)}`, { method: "DELETE" }); toast("Question deleted.", "success"); renderAssignments(); }
      catch (err) { toast(err.message, "error"); }
    }
  });

  // Generator view
  $("#genAssignment").addEventListener("change", () => { $("#genPrompt").dataset.touched = ""; refreshGenerator(); });
  $("#genQuestion").addEventListener("change", () => { $("#genPrompt").dataset.touched = ""; renderGenList(); });
  $("#genPrompt").addEventListener("input", e => { e.target.dataset.touched = "1"; });
  $("#generateBtn").addEventListener("click", generateTests);

  // Classroom view
  $("#syncGradeBtn").addEventListener("click", syncAndGrade);
  $("#crCourse").addEventListener("change", () => { $("#crTableCard").hidden = true; crLoadWork(); });
  $("#crWork").addEventListener("change", () => { $("#crTableCard").hidden = true; crUpdateOpenLink(); });
  $("#crAssignment").addEventListener("change", crSyncDestFromAssignment);
  $("#crLoadBtn").addEventListener("click", e => crLoadSubmissions(e.currentTarget));
  $("#crPattern").addEventListener("change", async e => {
    try {
      await api("/api/classroom/settings", { method: "POST", body: { id_pattern: e.target.value } });
      $("#crPatternHint").textContent = "Pattern saved.";
      if (CR.table) crLoadSubmissions();
    } catch (err) { $("#crPatternHint").textContent = err.message; toast(err.message, "error"); }
  });
  $("#crSyncBtn").addEventListener("click", e => crSync(null, e.currentTarget));
  $("#crSelectedBtn").addEventListener("click", e => {
    const emails = $$("[data-cr-pick]:checked").map(cb => CR.table.rows[Number(cb.dataset.crPick)].email);
    if (!emails.length) return toast("Tick the students to download.", "error");
    crSync(emails, e.currentTarget);
  });
  $("#crSelectAll").addEventListener("change", e => $$("[data-cr-pick]:not(:disabled)").forEach(cb => { cb.checked = e.target.checked; }));
  $("#crShowOthers").addEventListener("click", () => { CR.showOthers = !CR.showOthers; crRenderTable(); });
  $("#crGradeBtn").addEventListener("click", crGradeNow);

  // Settings view
  $("#backendOptions").addEventListener("change", e => { if (e.target.name === "backend") saveBackend(e.target.value); });
  $("#saveLlmBtn").addEventListener("click", saveLlmSettings);
  $("#saveStudentsBtn").addEventListener("click", saveStudents);
  $("#studentsInput").addEventListener("keydown", e => { if (e.key === "Enter") saveStudents(); });
}

async function init() {
  hydrateIcons();
  applyThemeLabel();
  wire();
  $("#workersInput").value = store.get("ta.workers", 3);

  await Promise.all([loadStatus(), loadAssignments(), crRefreshStatus()]);
  await loadPaths(currentAssignment());
  updateSyncButton();
  await loadResults();

  // Resume the progress view if a batch was already running (e.g. after a page reload)
  try {
    const st = await api("/api/grade/status?include_results=false");
    if (st.is_running) beginPolling();
  } catch (e) { /* server offline */ }

  const lastView = store.get("ta.view", "grade");
  if (lastView !== "grade") showView(lastView);
}

document.addEventListener("DOMContentLoaded", init);
