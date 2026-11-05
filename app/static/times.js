"use strict";
// Projektzeiten (Stoppuhr, Nachtragen, Woche), Kopfzeilen-Stoppuhr und Auswertung.
// Nutzt die Hilfen aus app.js ($, $$, api, esc, state, isTeam, isAdmin, okMsg, downloadFile, p2).

const TM = { running: null, offset: 0, tick: null, poll: null, projects: [], tasksByProject: {}, mode: "watch",
             weekStart: null, entries: [], openEdit: null, people: [], viewUser: null };
const RP = { view: "sum", data: null, customers: [], projects: [] };
const WDS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

const dIso = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
const hmStr = (min) => `${Math.floor(min / 60)}:${p2(Math.round(min) % 60)}`;
const hmsStr = (sec) => `${Math.floor(sec / 3600)}:${p2(Math.floor(sec / 60) % 60)}:${p2(sec % 60)}`;
function mondayOf(d) { const x = new Date(d); x.setHours(12, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }
function addDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
function isoWeek(d) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7; t.setUTCDate(t.getUTCDate() + 4 - day);
  return Math.ceil(((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / 864e5 + 1) / 7);
}
const timeOf = (iso) => iso ? iso.slice(11, 16) : "";
const boss = () => isTeam() && isAdmin();

// ---------- Stoppuhr (überall: Kopfzeile) ----------
function setRunning(r) {
  TM.running = r;
  TM.offset = r ? r.seconds - (Date.now() / 1000) : 0;
  clearInterval(TM.tick);
  if (r) TM.tick = setInterval(drawClock, 1000);
  drawClock();
  if (typeof renderTasks === "function" && state.tab === "tasks" && T.tasks.length) renderTasks();
}
function elapsed() { return TM.running ? Math.max(0, Math.floor(Date.now() / 1000 + TM.offset)) : 0; }
function drawClock() {
  const r = TM.running, sec = elapsed();
  $("hdTimer").classList.toggle("hidden", !r);
  if (r) {
    $("hdProj").textContent = (r.ticket ? r.ticket + " · " + (r.ticket_title || r.project) : r.project + (r.task ? " · " + r.task : "")) + " ·";
    $("hdTime").textContent = hmsStr(sec);
  }
  if (state.tab === "times" && TM.mode === "watch") {
    $("tmElapsed").textContent = hmsStr(sec);
    $("tmElapsed").classList.toggle("idle", !r);
  }
}
async function refreshRunning() {
  try { setRunning((await api("times/running")).running); } catch (e) { /* abgemeldet o. Ä. */ }
}
async function startTimer(body) {
  const r = await api("times/start", { method: "POST", body });
  setRunning(r.running);
  return r;
}
async function stopTimer(note) {
  const r = await api("times/stop", { method: "POST", body: note != null ? { note } : {} });
  setRunning(null);
  return r;
}
$("hdStop").onclick = async () => {
  try { const r = await stopTimer(); if (state.tab === "times") { await loadWeek(); fillClock(); }
        okMsg($("tmErr"), `Gestoppt: ${r.stopped.project} – ${hmStr(r.stopped.minutes)} h`); }
  catch (e) { alert(e.message); }
};
$("hdTimer").addEventListener("click", (e) => { if (e.target.id !== "hdStop") showTab("times"); });

// ▶ an Aufgaben (wird von tasks.js aufgerufen)
window.playTask = async (task) => {
  try {
    let msg;
    if (TM.running && TM.running.task_id === task.id) {
      const r = await stopTimer();
      msg = `Gestoppt: ${r.stopped.project} – ${hmStr(r.stopped.minutes)} h gebucht`;
    } else {
      const r = await startTimer({ task_id: task.id });
      msg = `Stoppuhr läuft: ${r.running.project} · ${task.title}` + (r.stopped ? ` (vorher ${r.stopped.project} gestoppt – ${hmStr(r.stopped.minutes)} h)` : "");
    }
    if (typeof refreshTasks === "function") await refreshTasks();
    okMsg($("taskErr"), msg);
  } catch (e) { $("taskErr").textContent = e.message; }
};
window.timerTaskId = () => TM.running ? TM.running.task_id : null;

// ---------- Zeiten-Seite ----------
async function loadTimes() {
  const [projects, people] = await Promise.all([api("projects"), api("people")]);
  TM.projects = projects.filter(p => p.kind === "project");
  TM.people = people;
  $$(".teamBoss").forEach(el => el.classList.toggle("hidden", !boss()));
  if (boss()) {
    const opts = TM.people.map(u => `<option value="${u.id}">${esc(u.display_name)}</option>`).join("");
    $("mnUser").innerHTML = opts; $("mnUser").value = String(state.me.id);
    $("wkUser").innerHTML = opts; $("wkUser").value = String(TM.viewUser || state.me.id);
  }
  if (!TM.weekStart) TM.weekStart = mondayOf(new Date());
  await refreshRunning();
  fillProjectSelect();
  fillClock();
  await loadWeek();
}

function projectOpts(sel, withEmpty) {
  let h = withEmpty ? '<option value="">– Projekt wählen –</option>' : "", last;
  const sorted = TM.projects.slice().sort((a, b) => (a.customer || "~").localeCompare(b.customer || "~", "de") || a.name.localeCompare(b.name, "de"));
  sorted.forEach(p => {
    const cust = p.customer || "Ohne Kunde";
    if (cust !== last) { if (last !== undefined) h += "</optgroup>"; h += `<optgroup label="${esc(cust)}">`; last = cust; }
    h += `<option value="${p.id}" ${p.id === sel ? "selected" : ""}>${esc(p.name)}</option>`;
  });
  return h + (last !== undefined ? "</optgroup>" : "");
}
function fillProjectSelect() {
  const sel = TM.running ? TM.running.project_id : Number(store.get("tmProject")) || null;
  $("tmProject").innerHTML = projectOpts(sel, true);
  if (!TM.projects.length) $("tmProject").innerHTML = '<option value="">– noch kein Projekt – unter Aufgaben anlegen –</option>';
}
async function fillTaskSelect(projectId, sel) {
  if (!projectId || !modOn("tasks")) { $("tmTask").innerHTML = '<option value="">–</option>'; return; }
  if (!TM.tasksByProject[projectId]) TM.tasksByProject[projectId] = await api(`tasks?project_id=${projectId}`);
  const list = TM.tasksByProject[projectId];
  $("tmTask").innerHTML = '<option value="">– keine bestimmte –</option>' +
    list.map(t => `<option value="${t.id}" ${t.id === sel ? "selected" : ""}>${esc(t.title)}</option>`).join("") +
    (sel && !list.some(t => t.id === sel) && TM.running && TM.running.task ? `<option value="${sel}" selected>${esc(TM.running.task)}</option>` : "");
}
function fillClock() {
  const r = TM.running;
  if (r) {
    $("tmProject").value = String(r.project_id);
    $("tmNote").value = r.note || ""; $("tmOnSite").checked = r.on_site;
    fillTaskSelect(r.project_id, r.task_id).catch(() => {});
    $("tmInfo").innerHTML = `Läuft seit ${timeOf(r.start)} Uhr <button class="mini" id="tmFix">Beginn korrigieren</button> · ` +
      "Ein Wechsel des Projekts stoppt die laufende Zeit und startet eine neue.";
    $("tmFix").onclick = fixStart;
  } else {
    fillTaskSelect(Number($("tmProject").value) || null, null).catch(() => {});
    $("tmInfo").textContent = TM.projects.length ? "Projekt wählen und starten – die Zeit läuft weiter, auch wenn du die Seite schließt."
                                                 : "Zeiten werden auf Projekte gebucht. Lege unter „Aufgaben“ zuerst ein Projekt an.";
  }
  $("tmToggle").textContent = r ? "■ Stopp" : "▶ Start";
  $("tmToggle").classList.toggle("stop", !!r); $("tmToggle").classList.toggle("primary", !r);
  drawClock();
}
async function fixStart() {
  const r = TM.running;
  const v = prompt("Beginn der laufenden Zeit (hh:mm):", timeOf(r.start));
  if (!v || !/^\d{1,2}:\d{2}$/.test(v.trim())) return;
  try {
    const upd = await api(`times/${r.id}`, { method: "PATCH", body: { start: r.start.slice(0, 11) + v.trim().padStart(5, "0") } });
    setRunning(upd); fillClock(); await loadWeek();
  } catch (e) { $("tmErr").textContent = e.message; }
}
async function toggleTimer() {
  $("tmErr").textContent = "";
  $("tmToggle").disabled = true;
  try {
    if (TM.running) {
      const r = await stopTimer($("tmNote").value.trim());
      okMsg($("tmErr"), `Gebucht: ${r.stopped.project} – ${hmStr(r.stopped.minutes)} h` + budgetNote(r.stopped));
      $("tmNote").value = "";
    } else {
      const project_id = Number($("tmProject").value);
      if (!project_id) throw new Error("Bitte ein Projekt wählen");
      store.set("tmProject", project_id);
      await startTimer({ project_id, task_id: Number($("tmTask").value) || null, note: $("tmNote").value.trim(), on_site: $("tmOnSite").checked });
    }
    fillClock(); await loadWeek();
  } catch (e) { $("tmErr").textContent = e.message; }
  finally { $("tmToggle").disabled = false; }
}
async function onProjectChange() {
  const pid = Number($("tmProject").value) || null;
  if (pid) store.set("tmProject", pid);
  if (TM.running && TM.mode === "watch" && pid && pid !== TM.running.project_id) {
    // Wechsel während die Uhr läuft → alte beenden, neue starten
    try {
      const r = await startTimer({ project_id: pid, on_site: $("tmOnSite").checked });
      okMsg($("tmErr"), `${r.stopped.project} gestoppt (${hmStr(r.stopped.minutes)} h) – läuft jetzt auf ${r.running.project}`);
      $("tmNote").value = ""; fillClock(); await loadWeek();
    } catch (e) { $("tmErr").textContent = e.message; }
    return;
  }
  await fillTaskSelect(pid, null);
}
async function patchRunning(body) {
  if (!TM.running || TM.mode !== "watch") return;
  try { setRunning(await api(`times/${TM.running.id}`, { method: "PATCH", body })); await loadWeek(); }
  catch (e) { $("tmErr").textContent = e.message; }
}
function setMode(mode) {
  TM.mode = mode;
  $$("#clockCard [data-mode]").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  $$("#clockCard .watchOnly").forEach(el => el.classList.toggle("hidden", mode !== "watch"));
  $$("#clockCard .manualOnly").forEach(el => el.classList.toggle("hidden", mode !== "manual"));
  if (mode === "manual") {
    if (!$("mnDay").value) $("mnDay").value = dIso(new Date());
    if (TM.running) { $("tmNote").value = ""; }
  } else fillClock();
}
function durToMin(v) {
  v = (v || "").trim().replace(",", ".");
  if (!v) return null;
  const m = v.match(/^(\d{1,2}):(\d{2})$/);
  if (m) return Number(m[1]) * 60 + Number(m[2]);
  if (/^\d+(\.\d+)?$/.test(v)) return Math.round(Number(v) * 60);
  throw new Error("Dauer als 1:30 oder 1,5 eingeben");
}
function minToTime(min) { min = ((min % 1440) + 1440) % 1440; return `${p2(Math.floor(min / 60))}:${p2(min % 60)}`; }
function timeToMin(t) { const [h, m] = (t || "").split(":").map(Number); return h * 60 + m; }
function syncDur(from) {
  try {
    if (from === "dur") {
      const d = durToMin($("mnDur").value);
      if (d != null && $("mnFrom").value) $("mnTo").value = minToTime(timeToMin($("mnFrom").value) + d);
    } else if ($("mnFrom").value && $("mnTo").value) {
      let d = timeToMin($("mnTo").value) - timeToMin($("mnFrom").value);
      if (d < 0) d += 1440;
      $("mnDur").value = hmStr(d);
    }
  } catch (e) { /* während des Tippens */ }
}
async function saveManual() {
  $("tmErr").textContent = "";
  try {
    const project_id = Number($("tmProject").value);
    if (!project_id) throw new Error("Bitte ein Projekt wählen");
    const day = $("mnDay").value, from = $("mnFrom").value;
    let to = $("mnTo").value;
    if (!day || !from) throw new Error("Bitte Datum und Beginn angeben");
    if (!to) { const d = durToMin($("mnDur").value); if (d == null) throw new Error("Bitte Ende oder Dauer angeben"); to = minToTime(timeToMin(from) + d); }
    const endDay = timeToMin(to) <= timeToMin(from) ? dIso(addDays(new Date(day + "T12:00:00"), 1)) : day;  // über Mitternacht
    const body = { project_id, task_id: Number($("tmTask").value) || null, start: `${day}T${from}`, end: `${endDay}T${to}`,
                   note: $("tmNote").value.trim(), on_site: $("tmOnSite").checked };
    if (boss() && Number($("mnUser").value) !== state.me.id) body.user_id = Number($("mnUser").value);
    const e = await api("times", { method: "POST", body });
    okMsg($("tmErr"), `Nachgetragen: ${e.project} – ${hmStr(e.minutes)} h` + budgetNote(e));
    $("mnFrom").value = to; $("mnTo").value = ""; $("mnDur").value = ""; $("tmNote").value = "";
    TM.weekStart = mondayOf(new Date(day + "T12:00:00"));
    await loadWeek();
  } catch (e) { $("tmErr").textContent = e.message; }
}

function budgetNote(e) {
  return e.budget_warning === 100 ? " · ⚠ Projektbudget überschritten" : e.budget_warning === 80 ? " · ⚠ 80 % des Projektbudgets verbraucht" : "";
}

// ---------- Woche ----------
async function loadWeek() {
  $("wkErr").textContent = "";
  const from = dIso(TM.weekStart), to = dIso(addDays(TM.weekStart, 6));
  const uid = boss() ? Number($("wkUser").value || state.me.id) : state.me.id;
  TM.entries = await api(`times?from=${from}&to=${to}` + (uid !== state.me.id ? `&user_id=${uid}` : ""));
  renderWeek();
}
function renderWeek() {
  const today = dIso(new Date());
  const days = Array.from({ length: 7 }, (_, i) => dIso(addDays(TM.weekStart, i)));
  const sums = Object.fromEntries(days.map(d => [d, 0]));
  TM.entries.forEach(e => { if (sums[e.day] != null) sums[e.day] += e.running ? Math.floor(elapsed() / 60) : e.minutes; });
  const max = Math.max(480, ...Object.values(sums));
  const end = addDays(TM.weekStart, 6);
  $("wkTitle").textContent = `Woche ${isoWeek(TM.weekStart)} · ${TM.weekStart.getDate()}.${TM.weekStart.getMonth() + 1}.–${end.getDate()}.${end.getMonth() + 1}.${end.getFullYear()}`;
  const total = Object.values(sums).reduce((a, b) => a + b, 0);
  $("wkBar").innerHTML = days.map((d, i) => `<div class="wd ${d === today ? "today" : ""} ${i > 4 ? "we" : ""}" data-day="${d}">${WDS[i]} ${d.slice(8, 10)}.
      <b>${sums[d] ? hmStr(sums[d]) : "–"}</b><div class="bar"><i style="width:${Math.round(sums[d] * 100 / max)}%"></i></div></div>`).join("");
  let h = "";
  days.slice().reverse().forEach(d => {
    const list = TM.entries.filter(e => e.day === d);
    if (!list.length) return;
    const dt = new Date(d + "T12:00:00");
    h += `<div class="day" id="day-${d}"><span>${dt.toLocaleDateString("de-DE", { weekday: "long", day: "2-digit", month: "2-digit" })}</span><span>${hmStr(sums[d])} h</span></div>`;
    list.forEach(e => {
      h += `<div class="entry ${e.running ? "running" : ""}"><span class="time">${timeOf(e.start)} – ${e.running ? "läuft" : timeOf(e.end)}</span>
        <div><span class="ptag"><i style="background:${esc(e.project_color)}"></i> <b>${esc(e.project)}</b></span>${e.customer ? ` <span class="muted small">· ${esc(e.customer)}</span>` : ""}
          ${e.task || e.note || e.ticket ? `<div class="note">${esc([e.ticket ? e.ticket + " " + (e.ticket_title || "") : "", e.task, e.note].filter(Boolean).join(" – "))}</div>` : ""}</div>
        <span class="tagcol">${e.on_site ? '<span class="tag">vor Ort</span>' : ""}${e.billed ? ` <span class="tag billed" title="abgerechnet ${esc(fmtDT(e.billed_at))}${e.billed_ref ? " – " + esc(e.billed_ref) : ""}">🔒 abgerechnet</span>` : ""}</span>
        <span class="dur">${e.running ? hmStr(Math.floor(elapsed() / 60)) : hmStr(e.minutes)}</span>
        <span class="acts">${e.billed ? "" : `<button class="mini" data-eedit="${e.id}">Bearbeiten</button>`}</span></div>`;
      if (TM.openEdit === e.id) h += entryEditor(e);
    });
  });
  $("wkList").innerHTML = (h || '<div class="emptylist">In dieser Woche ist noch nichts gebucht.</div>') +
    (total ? `<div class="wkTotal">Summe der Woche: <b>${hmStr(total)} h</b></div>` : "");
  $$("#wkBar [data-day]").forEach(el => el.onclick = () => { const t = $("day-" + el.dataset.day); if (t) t.scrollIntoView({ behavior: "smooth", block: "center" }); });
  $$("#wkList [data-eedit]").forEach(b => b.onclick = () => { TM.openEdit = TM.openEdit === Number(b.dataset.eedit) ? null : Number(b.dataset.eedit); renderWeek(); });
  if (TM.openEdit) bindEntryEditor();
}
function entryEditor(e) {
  return `<div class="eedit" data-edit="${e.id}"><div class="row">
      <label>Projekt <select data-f="project_id">${projectOpts(e.project_id, false)}</select></label>
      <label>Datum <input type="date" data-f="day" value="${e.day}"></label>
      <label>von <input type="time" data-f="from" value="${timeOf(e.start)}"></label>
      ${e.running ? "" : `<label>bis <input type="time" data-f="to" value="${timeOf(e.end)}"></label>`}
      <label class="inline"><input type="checkbox" data-f="on_site" ${e.on_site ? "checked" : ""}> vor Ort</label></div>
    <label>Notiz <input data-f="note" maxlength="500" value="${esc(e.note)}"></label>
    <div class="row"><button class="primary" data-save>Speichern</button><button data-close>Schließen</button><button class="danger" data-del>Löschen</button>
      ${e.task ? `<span class="muted small">Aufgabe: ${esc(e.task)}</span>` : ""}
      ${e.ticket ? `<span class="muted small">Ticket: ${esc(e.ticket)} ${esc(e.ticket_title || "")}</span>` : ""}</div></div>`;
}
function bindEntryEditor() {
  const box = $("wkList").querySelector(".eedit");
  if (!box) return;
  const id = Number(box.dataset.edit), e = TM.entries.find(x => x.id === id);
  const f = (k) => box.querySelector(`[data-f="${k}"]`);
  box.querySelector("[data-close]").onclick = () => { TM.openEdit = null; renderWeek(); };
  box.querySelector("[data-del]").onclick = async () => {
    if (!confirm("Diese Buchung löschen?")) return;
    try { await api(`times/${id}`, { method: "DELETE" }); TM.openEdit = null; if (e.running) setRunning(null); await loadWeek(); fillClock(); }
    catch (err) { $("wkErr").textContent = err.message; }
  };
  box.querySelector("[data-save]").onclick = async () => {
    const body = { note: f("note").value.trim(), on_site: f("on_site").checked };
    const pid = Number(f("project_id").value);
    if (pid !== e.project_id) body.project_id = pid;
    const day = f("day").value;
    body.start = `${day}T${f("from").value}`;
    if (!e.running) {
      const endDay = timeToMin(f("to").value) <= timeToMin(f("from").value) ? dIso(addDays(new Date(day + "T12:00:00"), 1)) : day;
      body.end = `${endDay}T${f("to").value}`;
    }
    try {
      const upd = await api(`times/${id}`, { method: "PATCH", body });
      if (upd.running) setRunning(upd);
      TM.openEdit = null; await loadWeek(); fillClock();
    } catch (err) { $("wkErr").textContent = err.message; }
  };
}

// ---------- Auswertung ----------
function periodRange(kind) {
  const now = new Date(), y = now.getFullYear(), m = now.getMonth();
  const mon = mondayOf(now);
  switch (kind) {
    case "week": return [dIso(mon), dIso(addDays(mon, 6))];
    case "lastweek": return [dIso(addDays(mon, -7)), dIso(addDays(mon, -1))];
    case "month": return [dIso(new Date(y, m, 1)), dIso(new Date(y, m + 1, 0))];
    case "lastmonth": return [dIso(new Date(y, m - 1, 1)), dIso(new Date(y, m, 0))];
    case "year": return [`${y}-01-01`, `${y}-12-31`];
    case "lastyear": return [`${y - 1}-01-01`, `${y - 1}-12-31`];
    default: return [$("rpFrom").value, $("rpTo").value];
  }
}
async function loadReports() {
  const [customers, projects, people] = await Promise.all([api("customers"), api("projects?include_archived=true"), api("people")]);
  RP.customers = customers; RP.projects = projects.filter(p => p.kind === "project");
  const cur = $("rpCustomer").value;
  $("rpCustomer").innerHTML = '<option value="">alle Kunden</option><option value="0">ohne Kunde</option>' +
    customers.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  $("rpCustomer").value = cur;
  fillReportProjects();
  $$(".teamBoss").forEach(el => el.classList.toggle("hidden", !boss()));
  if (boss()) {
    const u = $("rpUser").value;
    $("rpUser").innerHTML = '<option value="">alle</option>' + people.map(p => `<option value="${p.id}">${esc(p.display_name)}</option>`).join("");
    $("rpUser").value = u;
  }
  if (!$("rpFrom").value) { const [a, b] = periodRange("month"); $("rpFrom").value = a; $("rpTo").value = b; }
  if (modOn("helpdesk")) {
    try {
      const cats = await api("helpdesk/categories"), cur = $("rpCategory").value;
      $("rpCategory").innerHTML = '<option value="">alle</option>' + cats.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
      $("rpCategory").value = cur;
    } catch (e) { /* egal */ }
  }
  await runReport();
}
function fillReportProjects() {
  const c = $("rpCustomer").value, cur = $("rpProject").value;
  const list = RP.projects.filter(p => c === "" || (c === "0" ? !p.customer_id : String(p.customer_id) === c));
  $("rpProject").innerHTML = '<option value="">alle Projekte</option>' +
    list.map(p => `<option value="${p.id}">${esc(p.name)}${p.archived ? " (archiviert)" : ""}</option>`).join("");
  $("rpProject").value = list.some(p => String(p.id) === cur) ? cur : "";
}
function reportParams() {
  const [from, to] = periodRange($("rpPeriod").value);
  const p = new URLSearchParams({ from, to, group: $("rpGroup").value || "customer" });
  if ($("rpCustomer").value !== "") p.set("customer_id", $("rpCustomer").value);
  if ($("rpBilled").value !== "all") p.set("billed", $("rpBilled").value);
  if ($("rpProject").value) p.set("project_id", $("rpProject").value);
  if (boss() && $("rpUser").value) p.set("user_id", $("rpUser").value);
  if (modOn("helpdesk") && $("rpCategory").value) p.set("category_id", $("rpCategory").value);
  return p;
}
async function runReport() {
  $("rpErr").textContent = "";
  $$(".rpCustom").forEach(el => el.classList.toggle("hidden", $("rpPeriod").value !== "custom"));
  try {
    const d = await api("reports/times?" + reportParams().toString());
    RP.data = d;
    const g = $("rpGroup").value || "customer";
    $("rpGroup").innerHTML = Object.entries(d.group_options).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("rpGroup").value = d.group_options[g] ? g : "customer";
    renderReport();
  } catch (e) { $("rpErr").textContent = e.message; }
  if (window.loadHdStats) loadHdStats();
}
function renderReport() {
  const d = RP.data;
  const de = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`;
  $("rpStats").innerHTML = [["Gesamt", d.total + " h"], ["davon offen", hmStr(d.open_minutes) + " h"], ["abgerechnet", hmStr(d.billed_minutes) + " h"],
                            ["Projekte", d.projects], ["Buchungen", d.count]]
    .concat(d.show_users ? [["Personen", d.people]] : [])
    .map(([l, v]) => `<div class="stat"><span class="muted small">${l}</span><b>${v}</b></div>`).join("");
  $("rpTitle").textContent = `${de(d.from)} – ${de(d.to)} · ${RP.view === "sum" ? "nach " + d.group_label : "Einzelbuchungen"}`;
  const total = d.total_minutes || 1;
  if (!d.count) {
    $("rpTable").innerHTML = '<tbody><tr><td class="muted">Im gewählten Zeitraum gibt es keine abgeschlossenen Buchungen.</td></tr></tbody>';
  } else if (RP.view === "sum") {
    const people = (u) => d.show_users ? u.map(n => `<span class="av ${"c" + ((n.length % 6) + 1)}" title="${esc(n)}" style="width:22px;height:22px;font-size:9px">${esc(initials(n))}</span>`).join("") : "";
    $("rpTable").innerHTML = `<thead><tr><th>${esc(d.group_label)}</th>${d.show_users ? "<th>Personen</th>" : ""}<th class="num">Buchungen</th><th style="width:34%">Anteil</th><th class="num">Stunden</th></tr></thead><tbody>` +
      d.groups.map(g => {
        const pct = Math.round(g.minutes * 100 / total);
        return `<tr class="grp"><td>${esc(g.label)}</td>${d.show_users ? `<td>${people(g.users)}</td>` : ""}<td class="num">${g.count}</td>
          <td><span class="hb" style="width:${pct * 0.8}%"></span>${pct} %</td><td class="num">${hmStr(g.minutes)}</td></tr>` +
          g.children.map(c => { const cp = Math.round(c.minutes * 100 / total);
            return `<tr><td class="sub">${esc(c.label)}</td>${d.show_users ? `<td>${people(c.users)}</td>` : ""}<td class="num">${c.count}</td>
              <td><span class="hb" style="width:${cp * 0.8}%;opacity:.45"></span>${cp} %</td><td class="num">${hmStr(c.minutes)}</td></tr>`; }).join("");
      }).join("") + `</tbody><tfoot><tr><td><b>Summe</b></td>${d.show_users ? "<td></td>" : ""}<td class="num"><b>${d.count}</b></td><td></td><td class="num"><b>${d.total}</b></td></tr></tfoot>`;
  } else {
    $("rpTable").innerHTML = `<thead><tr><th>Datum</th><th>Zeit</th>${d.team ? "<th>Mitarbeiter</th>" : ""}<th>Kunde · Projekt</th><th>Aufgabe / Notiz</th><th class="num">Dauer</th><th></th></tr></thead><tbody>` +
      d.entries.map(x => `<tr><td>${de(x.day)}</td><td>${x.start}–${x.end}</td>${d.team ? `<td>${esc(x.user)}</td>` : ""}
        <td><span class="ptag"><i style="background:${esc(x.project_color)}"></i>${x.customer !== "Ohne Kunde" ? esc(x.customer) + " · " : ""}${esc(x.project)}</span></td>
        <td class="wrap small">${esc([x.task, x.note].filter(Boolean).join(" – "))}${x.on_site ? ' <span class="tag">vor Ort</span>' : ""}</td>
        <td class="num">${hmStr(x.minutes)}</td>
        <td class="nowrap">${x.billed ? `<span class="tag billed" title="${esc(x.billed_ref || "")}">🔒 ${x.billed_ref ? esc(x.billed_ref) : de(x.billed_day)}</span>` : ""}
          ${d.can_bill ? `<button class="mini" data-bill1="${x.id}" data-billed="${x.billed ? 1 : 0}" title="${x.billed ? "Abrechnung zurücknehmen" : "als abgerechnet markieren"}">${x.billed ? "↺" : "€"}</button>` : ""}</td></tr>`).join("") +
      `</tbody><tfoot><tr><td colspan="${d.team ? 5 : 4}"><b>Summe</b></td><td class="num"><b>${d.total}</b></td><td></td></tr></tfoot>`;
    $$("#rpTable [data-bill1]").forEach(b => b.onclick = () => billing(b.dataset.billed === "1" ? "unbill" : "bill", [Number(b.dataset.bill1)]));
  }
  $("rpNote").textContent = (d.team && !boss() ? "Du siehst hier nur deine eigenen Zeiten. " : "") +
    "Gezählt werden abgeschlossene Buchungen – eine laufende Stoppuhr erst nach dem Stoppen. Abgerechnete Zeiten sind gegen Änderungen gesperrt.";
  const bud = Object.entries(d.budgets || {});
  $("rpBudgets").innerHTML = bud.length ? '<span class="muted">Budgets (gesamt):</span>' + bud.map(([name, b]) => `<span><b>${esc(name)}</b> ${budgetBar(b)}</span>`).join("") : "";
  $("rpBillBar").classList.toggle("hidden", !d.can_bill || !d.count);
  $("rpBillBtn").disabled = !d.open_minutes; $("rpUnbillBtn").disabled = !d.billed_minutes;
  $("rpBillBtn").textContent = `Offene als abgerechnet markieren (${hmStr(d.open_minutes)} h) …`;
}

async function billing(action, ids) {
  const d = RP.data;
  let ref = "";
  if (action === "bill") {
    const what = ids ? "diese Buchung" : `alle offenen Buchungen der Auswahl (${hmStr(d.open_minutes)} h)`;
    ref = prompt(`${what[0].toUpperCase() + what.slice(1)} als abgerechnet markieren.\n\nVermerk, z. B. Rechnungsnummer (optional):`, "");
    if (ref === null) return;
  } else if (!confirm(ids ? "Abrechnung dieser Buchung zurücknehmen?" : `Abrechnung für alle abgerechneten Buchungen der Auswahl (${hmStr(d.billed_minutes)} h) zurücknehmen?`)) return;
  const params = Object.fromEntries(reportParams());
  const body = { action, ref, from: params.from, to: params.to };
  ["customer_id", "project_id", "user_id"].forEach(k => { if (params[k] !== undefined) body[k] = Number(params[k]); });
  if (ids) body.entry_ids = ids;
  try {
    const r = await api("reports/times/billing", { method: "POST", body });
    await runReport();
    okMsg($("rpErr"), `${r.count} Buchung${r.count !== 1 ? "en" : ""} (${hmStr(r.minutes)} h) ${action === "bill" ? "als abgerechnet markiert" : "wieder offen"}.`);
  } catch (e) { $("rpErr").textContent = e.message; }
}

// ---------- Ereignisse ----------
$$("#clockCard [data-mode]").forEach(b => b.onclick = () => setMode(b.dataset.mode));
$("tmToggle").onclick = toggleTimer;
$("tmProject").onchange = onProjectChange;
$("tmTask").onchange = () => { if (TM.running && TM.mode === "watch") patchRunning({ task_id: Number($("tmTask").value) || null }); };
$("tmNote").addEventListener("change", () => patchRunning({ note: $("tmNote").value.trim() }));
$("tmNote").addEventListener("keydown", (e) => { if (e.key === "Enter") { if (TM.mode === "manual") saveManual(); else if (!TM.running) toggleTimer(); } });
$("tmOnSite").onchange = () => patchRunning({ on_site: $("tmOnSite").checked });
$("mnSave").onclick = saveManual;
$("mnDur").addEventListener("input", () => syncDur("dur"));
$("mnFrom").addEventListener("change", () => syncDur($("mnDur").value && !$("mnTo").value ? "dur" : "to"));
$("mnTo").addEventListener("change", () => syncDur("to"));
$("wkPrev").onclick = () => { TM.weekStart = addDays(TM.weekStart, -7); TM.openEdit = null; loadWeek().catch(e => { $("wkErr").textContent = e.message; }); };
$("wkNext").onclick = () => { TM.weekStart = addDays(TM.weekStart, 7); TM.openEdit = null; loadWeek().catch(e => { $("wkErr").textContent = e.message; }); };
$("wkToday").onclick = () => { TM.weekStart = mondayOf(new Date()); TM.openEdit = null; loadWeek().catch(e => { $("wkErr").textContent = e.message; }); };
$("wkUser").onchange = () => { TM.viewUser = Number($("wkUser").value); TM.openEdit = null; loadWeek().catch(e => { $("wkErr").textContent = e.message; }); };
["rpPeriod", "rpFrom", "rpTo", "rpProject", "rpUser", "rpGroup", "rpBilled", "rpCategory"].forEach(id => $(id).onchange = runReport);
$("rpBillBtn").onclick = () => billing("bill", null);
$("rpUnbillBtn").onclick = () => billing("unbill", null);
$("rpCustomer").onchange = () => { fillReportProjects(); runReport(); };
$$("[data-rpview]").forEach(b => b.onclick = () => {
  RP.view = b.dataset.rpview; $$("[data-rpview]").forEach(x => x.classList.toggle("on", x === b)); if (RP.data) renderReport();
});
$$("[data-exp]").forEach(b => b.onclick = async () => {
  b.disabled = true; $("rpErr").textContent = "";
  try { await downloadFile(`api/v1/reports/times/export?format=${b.dataset.exp}&` + reportParams().toString(), `zeitnachweis.${b.dataset.exp}`); }
  catch (e) { $("rpErr").textContent = e.message; }
  finally { b.disabled = false; }
});
// Stoppuhr regelmäßig abgleichen (z. B. am Handy gestartet)
TM.poll = setInterval(() => { if (state.me && !document.hidden) refreshRunning(); }, 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.me) refreshRunning(); });

window.loadTimes = loadTimes;
window.loadReports = loadReports;
window.refreshRunning = refreshRunning;
