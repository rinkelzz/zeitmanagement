"use strict";
// Aufgaben: Seitenleiste, Schnelleingabe, Liste, Bearbeiten, Projekte/Listen verwalten.
// Nutzt die Hilfen aus app.js ($, $$, api, esc, state, isTeam, isAdmin, okMsg, initials).

const T = { view: null, projectId: null, projects: [], customers: [], people: [], counts: {}, tasks: [], openEdit: null,
            editProject: null, parseTimer: null };
const COLORS = ["#0f766e", "#2563eb", "#7c3aed", "#c2410c", "#be185d", "#4d7c0f", "#b45309", "#0891b2", "#64748b", "#94a3b8"];
const VIEW_LABELS = { today: "☀️ Heute", overdue: "⚠️ Überfällig", upcoming: "📅 Demnächst", mine: "👤 Meine Aufgaben",
                      open: "📋 Alle offenen", inbox: "📥 Ohne Liste", done: "✓ Erledigt" };
const WDAY = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const RECUR_OPTS = [["", "keine"], ["d:1", "täglich"], ["wd", "werktags (Mo–Fr)"], ["w:1", "wöchentlich"], ["w:2", "alle 2 Wochen"],
                    ["m:1", "monatlich"], ["m:3", "alle 3 Monate"], ["y:1", "jährlich"]];
const hmT = (min) => `${Math.floor(min / 60)}:${String(min % 60).padStart(2, "0")}`;
function budgetBar(b) {
  if (!b) return "";
  const col = b.level >= 100 ? "var(--bad)" : b.level >= 80 ? "var(--warn)" : "var(--accent)";
  return `<span class="budget" title="Budget: ${hmT(b.used_minutes)} von ${hmT(b.budget_minutes)} h gebucht">
    <span class="bbar"><i style="width:${Math.min(100, b.percent)}%;background:${col}"></i></span>
    <span style="color:${b.level ? col : "inherit"}">${hmT(b.used_minutes)} / ${hmT(b.budget_minutes)} h · ${b.percent} %</span></span>`;
}

function isoToday(offset = 0) { const d = new Date(); d.setDate(d.getDate() + offset); return isoDay(d); }
function isoDay(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function dueLabel(iso) {
  if (!iso) return "";
  const today = isoToday(), d = new Date(iso + "T12:00:00");
  if (iso === today) return "Heute";
  if (iso === isoToday(1)) return "Morgen";
  if (iso === isoToday(-1)) return "Gestern";
  const s = `${WDAY[d.getDay()]}, ${iso.slice(8, 10)}.${iso.slice(5, 7)}.`;
  return iso.slice(0, 4) === today.slice(0, 4) ? s : s + iso.slice(0, 4);
}
const avClass = (id) => "c" + ((id % 6) + 1);
const prioTag = (p) => p === 1 ? '<span class="prio p1">Hoch</span>' : p === 2 ? '<span class="prio p2">Mittel</span>' : "";

function defaultView() { return isTeam() ? "mine" : "open"; }

async function loadTasks() {
  if (!T.view && T.projectId == null) {
    const saved = store.get("taskView") || "";
    if (saved.startsWith("p:")) T.projectId = Number(saved.slice(2)); else T.view = VIEW_LABELS[saved] ? saved : defaultView();
  }
  if (T.view === "mine" && !isTeam()) T.view = "open";
  const [projects, customers, people] = await Promise.all([api("projects"), api("customers"), api("people")]);
  T.projects = projects; T.customers = customers; T.people = people;
  if (T.projectId != null && !T.projects.find(p => p.id === T.projectId)) { T.projectId = null; T.view = defaultView(); }
  await refreshTasks();
}

async function refreshTasks() {
  $("taskErr").textContent = "";
  const p = new URLSearchParams();
  if (T.projectId != null) { p.set("project_id", T.projectId); if ($("showDone").checked) p.set("include_done", "true"); }
  else p.set("view", T.view);
  const [tasks, counts] = await Promise.all([api("tasks?" + p.toString()), api("tasks/counts")]);
  T.tasks = tasks; T.counts = counts;
  if (T.projectId != null) {  // Zähler der Seitenleiste aktuell halten
    T.projects = await api("projects");
  }
  renderSide(); renderHead(); renderTasks();
}

function renderSide() {
  const c = T.counts;
  const views = ["today", "overdue", "upcoming"].concat(isTeam() ? ["mine"] : []).concat(["open", "inbox", "done"]);
  const link = (key, label, n, warn, extra = "") =>
    `<a data-nav="${key}" class="${(T.projectId == null && T.view === key) || T.projectId === key ? "on" : ""}" ${extra}>${label}` +
    (n ? `<span class="n ${warn ? "warn" : ""}">${n}</span>` : "") + "</a>";
  let h = "<h4>Ansichten</h4>" + views.map(v => link(v, VIEW_LABELS[v], v === "done" ? 0 : c[v], v === "overdue")).join("");
  const projects = T.projects.filter(p => p.kind === "project");
  const lists = T.projects.filter(p => p.kind === "list");
  const pItem = (p) => `<a data-nav="${p.id}" class="${T.projectId === p.id ? "on" : ""}"><span class="sq" style="background:${esc(p.color)}"></span>` +
    `${esc(p.name)}${p.open ? `<span class="n ${p.overdue ? "warn" : ""}">${p.open}</span>` : ""}</a>`;
  if (projects.length || isAdmin() || !isTeam()) {
    h += `<h4>Projekte${isAdmin() || !isTeam() ? '<button data-new="project" title="Neues Projekt">＋</button>' : ""}</h4>`;
    let lastCust;
    projects.forEach(p => {
      const cust = p.customer || "Ohne Kunde";
      if (cust !== lastCust) { h += `<div class="cust">${esc(cust)}</div>`; lastCust = cust; }
      h += pItem(p);
    });
    if (!projects.length) h += '<div class="cust">noch keine – mit Kunde, später auch für Zeiten</div>';
  }
  h += '<h4>Aufgabenlisten<button data-new="list" title="Neue Aufgabenliste">＋</button></h4>' + lists.map(pItem).join("");
  if (!lists.length) h += '<a class="add" data-new="list">＋ Neue Liste</a>';
  $("taskSide").innerHTML = h;
  $$("#taskSide [data-nav]").forEach(a => a.onclick = () => selectNav(a.dataset.nav));
  $$("#taskSide [data-new]").forEach(b => b.onclick = (e) => { e.preventDefault(); openProjectForm(null, b.dataset.new); });

  // Handy: Auswahlfeld statt Seitenleiste
  const opt = (v, l, sel) => `<option value="${v}" ${sel ? "selected" : ""}>${esc(l)}</option>`;
  $("taskViewSel").innerHTML = views.map(v => opt(v, VIEW_LABELS[v] + (c[v] && v !== "done" ? ` (${c[v]})` : ""), T.projectId == null && T.view === v)).join("") +
    (projects.length ? '<optgroup label="Projekte">' + projects.map(p => opt(p.id, (p.customer ? p.customer + " · " : "") + p.name, T.projectId === p.id)).join("") + "</optgroup>" : "") +
    (lists.length ? '<optgroup label="Aufgabenlisten">' + lists.map(p => opt(p.id, p.name, T.projectId === p.id)).join("") + "</optgroup>" : "") +
    '<option value="new:list">＋ Neue Aufgabenliste …</option>';
}

function selectNav(key) {
  T.openEdit = null; closeProjectForm();
  if (/^\d+$/.test(String(key))) { T.projectId = Number(key); T.view = null; store.set("taskView", "p:" + key); }
  else { T.projectId = null; T.view = key; store.set("taskView", key); }
  refreshTasks().catch(e => { $("taskErr").textContent = e.message; });
}

function currentProject() { return T.projectId != null ? T.projects.find(p => p.id === T.projectId) : null; }

function renderHead() {
  const p = currentProject();
  $("taskTitle").innerHTML = p ? `<span class="ptag"><i style="background:${esc(p.color)};width:12px;height:12px;border-radius:3px"></i>${esc(p.name)}</span>` +
    (p.customer ? ` <span class="muted small">· ${esc(p.customer)}</span>` : "") + (p.kind === "list" ? ' <span class="muted small">· Aufgabenliste</span>' : "") +
    budgetBar(p.budget)
    : esc(VIEW_LABELS[T.view].replace(/^\S+\s/, ""));
  $("showDoneWrap").classList.toggle("hidden", !p);
  $("projEditBtn").classList.toggle("hidden", !(p && p.can_manage));
  $("quickWrap").classList.toggle("hidden", T.view === "done");
  const small = window.innerWidth < 720;
  $("qaText").placeholder = small ? "Neue Aufgabe …" : p ? `Neue Aufgabe in „${p.name}“ … z. B. „Angebot schicken fr !hoch“`
                              : "Neue Aufgabe … z. B. „Backup prüfen morgen !hoch #büro“";
}

function groupOf(t) {
  if (t.done) return "done";
  if (!t.due_date) return "none";
  const today = isoToday();
  return t.due_date < today ? "over" : t.due_date === today ? "today" : "later";
}
const GROUPS = { over: "Überfällig", today: "Heute", later: "Demnächst", none: "Ohne Datum", done: "Erledigt" };

function renderTasks() {
  const list = T.tasks;
  if (!list.length) {
    const msg = T.view === "done" ? "Noch nichts erledigt." : T.view === "today" ? "Heute ist nichts fällig. 🎉"
      : T.view === "overdue" ? "Nichts überfällig – stark!" : "Keine offenen Aufgaben.";
    $("taskList").innerHTML = `<div class="emptylist">${msg}</div>`;
    return;
  }
  let h = "", last = null;
  const grouped = T.view !== "done";
  list.forEach(t => {
    const g = groupOf(t);
    if (grouped && g !== last) {
      const n = list.filter(x => groupOf(x) === g).length;
      const day = g === "today" ? " · " + new Date().toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" }) : "";
      h += `<div class="group ${g === "over" ? "over" : ""}">${GROUPS[g]}${day} · ${n}</div>`;
      last = g;
    }
    h += taskRow(t);
    if (T.openEdit === t.id) h += taskEditor(t);
  });
  $("taskList").innerHTML = h;
  $$("#taskList [data-cb]").forEach(b => b.onclick = () => toggleDone(Number(b.dataset.cb)));
  $$("#taskList [data-play]").forEach(b => b.onclick = () => window.playTask(T.tasks.find(x => x.id === Number(b.dataset.play))));
  $$("#taskList [data-open]").forEach(b => b.onclick = () => { T.openEdit = T.openEdit === Number(b.dataset.open) ? null : Number(b.dataset.open); renderTasks(); });
  if (T.openEdit) bindEditor();
}

function taskRow(t) {
  const g = groupOf(t);
  const due = t.due_date ? `<span class="${g === "over" ? "over" : g === "today" ? "today" : ""}">📅 ${esc(dueLabel(t.due_date))}</span>` : "";
  const proj = t.project && t.project_id !== T.projectId ? `<span class="ptag"><i style="background:${esc(t.project_color)}"></i>${esc(t.project)}</span>` : "";
  const notes = t.notes ? '<span title="Notiz">📝</span>' : "";
  const time = t.due_time && !t.done ? `<span title="Erinnerung zu dieser Uhrzeit">⏰ ${esc(t.due_time)}</span>` : "";
  const rec = t.recur ? `<span title="wiederkehrend">🔁 ${esc(t.recur_label)}</span>` : "";
  const nItems = t.items.length, nDone = t.items.filter(i => i.done).length;
  const items = nItems ? `<span title="Checkliste" class="${nDone === nItems ? "itemsdone" : ""}">☑ ${nDone}/${nItems}</span>` : "";
  const done = t.done ? `<span>erledigt ${esc(fmtDT(t.completed_at))}${isTeam() && t.completed_by_name ? " von " + esc(t.completed_by_name) : ""}</span>` : "";
  const av = isTeam() ? t.assignee_ids.map((id, i) => `<span class="av ${avClass(id)}" title="${esc(t.assignees[i])}">${esc(initials(t.assignees[i]))}</span>`).join("") : "";
  const running = window.timerTaskId && window.timerTaskId() === t.id;
  const play = !t.done && t.project_kind === "project"
    ? `<button class="play ${running ? "run" : ""}" data-play="${t.id}" title="${running ? "Stoppuhr stoppen" : "Zeit auf diese Aufgabe buchen"}">${running ? "■" : "▶"}</button>` : "";
  return `<div class="task ${t.done ? "isdone" : ""}">
    <button class="cb ${t.done ? "done" : t.priority === 1 ? "p1" : t.priority === 2 ? "p2" : ""}" data-cb="${t.id}" title="${t.done ? "wieder öffnen" : "erledigt"}"></button>
    <div class="body" data-open="${t.id}"><div class="t">${esc(t.title)}</div>
      <div class="meta">${due}${time}${t.done ? "" : prioTag(t.priority)}${rec}${proj}${items}${notes}${done}</div></div>
    <div class="tright"><span>${av}</span>${play}</div></div>`;
}

function projectOptions(sel) {
  const ps = T.projects.filter(p => !p.archived);
  return `<option value="">– ohne Liste –</option>` +
    (ps.some(p => p.kind === "project") ? '<optgroup label="Projekte">' + ps.filter(p => p.kind === "project").map(p =>
      `<option value="${p.id}" ${p.id === sel ? "selected" : ""}>${esc((p.customer ? p.customer + " · " : "") + p.name)}</option>`).join("") + "</optgroup>" : "") +
    (ps.some(p => p.kind === "list") ? '<optgroup label="Aufgabenlisten">' + ps.filter(p => p.kind === "list").map(p =>
      `<option value="${p.id}" ${p.id === sel ? "selected" : ""}>${esc(p.name)}</option>`).join("") + "</optgroup>" : "");
}

function taskEditor(t) {
  const who = isTeam() ? `<div><span class="muted small">Zuständig</span><div class="who">` +
    T.people.map(u => `<label class="inline"><input type="checkbox" data-who="${u.id}" ${t.assignee_ids.includes(u.id) ? "checked" : ""}> ${esc(u.display_name)}</label>`).join("") +
    "</div></div>" : "";
  return `<div class="tedit" data-edit="${t.id}">
    <label>Titel <input data-f="title" maxlength="300" value="${esc(t.title)}"></label>
    <div class="grid">
      <label>Fällig am <input type="date" data-f="due_date" value="${esc(t.due_date || "")}"></label>
      <label>Priorität <select data-f="priority">
        <option value="1" ${t.priority === 1 ? "selected" : ""}>Hoch</option>
        <option value="2" ${t.priority === 2 ? "selected" : ""}>Mittel</option>
        <option value="3" ${t.priority === 3 ? "selected" : ""}>Normal</option></select></label>
      <label>Uhrzeit (Erinnerung) <input type="time" data-f="due_time" value="${esc(t.due_time || "")}"></label>
      <label>Wiederholung <select data-f="recur">${RECUR_OPTS.map(([v, l]) => `<option value="${v}" ${(t.recur || "").split("@")[0] === v ? "selected" : ""}>${l}</option>`).join("") +
        (t.recur && !RECUR_OPTS.some(([v]) => v === t.recur.split("@")[0]) ? `<option value="${esc(t.recur)}" selected>${esc(t.recur_label)}</option>` : "")}</select></label>
      <label>Projekt / Liste <select data-f="project_id">${projectOptions(t.project_id)}</select></label>
    </div>
    ${who}
    <div><span class="muted small">Checkliste</span>
      <div class="items">${t.items.map(i => `<div class="item"><input type="checkbox" data-item="${i.id}" ${i.done ? "checked" : ""}>
        <span class="${i.done ? "idone" : ""}">${esc(i.title)}</span><button class="mini" data-itemdel="${i.id}" title="entfernen">✕</button></div>`).join("")}
        <div class="item"><input data-newitem maxlength="300" placeholder="＋ Punkt hinzufügen (Enter)"></div></div></div>
    <label>Notiz <textarea data-f="notes" maxlength="10000">${esc(t.notes)}</textarea></label>
    <div class="row"><button class="primary" data-save>Speichern</button><button data-close>Schließen</button>
      ${t.can_delete ? '<button class="danger" data-del>Löschen</button>' : ""}
      <span class="muted small">angelegt ${esc(fmtDT(t.created_at))}${isTeam() && t.created_by_name ? " von " + esc(t.created_by_name) : ""}</span></div>
  </div>`;
}

function bindEditor() {
  const box = $("taskList").querySelector(".tedit");
  if (!box) return;
  const id = Number(box.dataset.edit);
  const f = (k) => box.querySelector(`[data-f="${k}"]`);
  box.querySelector("[data-close]").onclick = () => { T.openEdit = null; renderTasks(); };
  box.querySelector("[data-save]").onclick = async () => {
    const body = { title: f("title").value.trim(), due_date: f("due_date").value || null, priority: Number(f("priority").value),
                   project_id: f("project_id").value ? Number(f("project_id").value) : null, notes: f("notes").value,
                   due_time: f("due_time").value || null };
    const t0 = T.tasks.find(x => x.id === id);
    if ((f("recur").value || null) !== (t0.recur || null)) body.recur = f("recur").value || null;
    if (isTeam()) body.assignee_ids = $$("[data-who]", box).filter(c => c.checked).map(c => Number(c.dataset.who));
    try { await api(`tasks/${id}`, { method: "PATCH", body }); T.openEdit = null; await refreshTasks(); }
    catch (e) { $("taskErr").textContent = e.message; }
  };
  const del = box.querySelector("[data-del]");
  if (del) del.onclick = async () => {
    if (!confirm("Aufgabe wirklich löschen?")) return;
    try { await api(`tasks/${id}`, { method: "DELETE" }); T.openEdit = null; await refreshTasks(); }
    catch (e) { $("taskErr").textContent = e.message; }
  };
  // Checkliste: sofort speichern
  const reload = (t) => { const i = T.tasks.findIndex(x => x.id === id); if (i >= 0) T.tasks[i] = t; renderTasks(); };
  $$("[data-item]", box).forEach(c => c.onchange = async () => {
    try { reload(await api(`tasks/${id}/items/${c.dataset.item}`, { method: "PATCH", body: { done: c.checked } })); }
    catch (e) { $("taskErr").textContent = e.message; }
  });
  $$("[data-itemdel]", box).forEach(b => b.onclick = async () => {
    try { reload(await api(`tasks/${id}/items/${b.dataset.itemdel}`, { method: "DELETE" })); } catch (e) { $("taskErr").textContent = e.message; }
  });
  const ni = box.querySelector("[data-newitem]");
  ni.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || !ni.value.trim()) return;
    try { reload(await api(`tasks/${id}/items`, { method: "POST", body: { title: ni.value.trim() } }));
          const again = $("taskList").querySelector(".tedit [data-newitem]"); if (again) again.focus(); }
    catch (err) { $("taskErr").textContent = err.message; }
  });
  if (!box.contains(document.activeElement)) f("title").focus();
}

async function toggleDone(id) {
  const t = T.tasks.find(x => x.id === id);
  if (!t) return;
  try {
    const r = await api(`tasks/${id}`, { method: "PATCH", body: { done: !t.done } });
    await refreshTasks();
    if (r.next) okMsg($("taskErr"), `Erledigt – nächste „${r.next.title}“ am ${dueLabel(r.next.due_date)}`);
  } catch (e) { $("taskErr").textContent = e.message; }
}

// --- Schnelleingabe ---
async function addQuick() {
  const text = $("qaText").value.trim();
  if (!text) return;
  $("qaAdd").disabled = true;
  try {
    const body = { text };
    if (T.projectId != null) body.project_id = T.projectId;
    if (T.view === "today") body.due_date = isoToday();   // in „Heute“ angelegt = heute fällig (Kürzel im Text gehen vor)
    const t = await api("tasks", { method: "POST", body });
    $("qaText").value = ""; $("qaPreview").innerHTML = "";
    await refreshTasks();
    okMsg($("taskErr"), `„${t.title}“ angelegt` + (t.project && t.project_id !== T.projectId ? ` in ${t.project}` : ""));
  } catch (e) { $("taskErr").textContent = e.message; }
  finally { $("qaAdd").disabled = false; $("qaText").focus(); }
}

function previewQuick() {
  clearTimeout(T.parseTimer);
  const text = $("qaText").value;
  if (!/(!|#|@|\bheute\b|\bmorgen\b|übermorgen|\b(mo|di|mi|do|fr|sa|so\.)\b|tag|woche|\d+\.\d+\.|\d:\d\d|uhr|\bum\b|lich\b|jede|alle|werktags)/i.test(text)) { $("qaPreview").innerHTML = ""; return; }
  T.parseTimer = setTimeout(async () => {
    try {
      const r = await api("tasks/parse?text=" + encodeURIComponent(text));
      const chips = [];
      if (r.due_date) chips.push(`📅 ${esc(dueLabel(r.due_date))}`);
      if (r.due_time) chips.push(`⏰ ${esc(r.due_time)}`);
      if (r.recur) chips.push(`🔁 ${esc(r.recur_label)}`);
      if (r.priority === 1) chips.push("❗ Hoch"); else if (r.priority === 2) chips.push("Mittel"); else if (r.priority === 3) chips.push("Normal");
      if (r.project) chips.push(`📁 ${esc(r.project)}`);
      r.assignees.forEach(a => chips.push(`👤 ${esc(a)}`));
      $("qaPreview").innerHTML = (r.title ? `<span class="chip">„${esc(r.title)}“</span>` : "") + chips.map(c => `<span class="chip">${c}</span>`).join("") +
        r.unknown.map(u => `<span class="chip warn" title="nicht gefunden – bleibt im Titel">${esc(u)}?</span>`).join("");
    } catch (e) { /* Vorschau ist nur eine Hilfe */ }
  }, 250);
}

// --- Projekte und Listen anlegen/bearbeiten ---
function openProjectForm(project, kind) {
  T.editProject = project;
  kind = project ? project.kind : kind;
  const canProject = isAdmin() || !isTeam();
  if (kind === "project" && !canProject) kind = "list";
  const color = project ? project.color : (kind === "list" ? "#94a3b8" : COLORS[T.projects.length % COLORS.length]);
  const members = project ? project.member_ids : [];
  const custOpts = '<option value="">– ohne Kunde –</option>' + T.customers.filter(c => !c.archived || (project && c.id === project.customer_id))
    .map(c => `<option value="${c.id}" ${project && c.id === project.customer_id ? "selected" : ""}>${esc(c.name)}</option>`).join("") +
    (canProject ? '<option value="new">＋ Neuer Kunde …</option>' : "");
  $("projForm").innerHTML = `<h2 style="margin:0">${project ? (kind === "list" ? "Liste" : "Projekt") + " bearbeiten" : "Neu anlegen"}</h2>
    ${project ? "" : `<div class="row">
      <label class="inline"><input type="radio" name="pfKind" value="list" ${kind === "list" ? "checked" : ""}> Aufgabenliste</label>
      ${canProject ? `<label class="inline"><input type="radio" name="pfKind" value="project" ${kind === "project" ? "checked" : ""}> Projekt (mit Kunde, später für Zeiten)</label>` : ""}</div>`}
    <div class="row">
      <label style="flex:1;min-width:200px">Name <input id="pfName" maxlength="100" value="${esc(project ? project.name : "")}"></label>
      <label id="pfCustWrap" style="min-width:200px">Kunde <select id="pfCust">${custOpts}</select></label>
      <label id="pfNewCustWrap" class="hidden">Neuer Kunde <input id="pfNewCust" maxlength="100"></label>
      <label id="pfBudgetWrap" class="w120">Budget (Stunden) <input id="pfBudget" inputmode="decimal" placeholder="ohne"
        value="${project && project.budget ? String(project.budget.budget_minutes / 60).replace(".", ",") : ""}"></label>
    </div>
    <div><span class="muted small">Farbe</span><div class="swatches" id="pfColors">${COLORS.map(c =>
      `<button type="button" data-color="${c}" class="${c === color ? "on" : ""}" style="background:${c}" title="${c}"></button>`).join("")}</div></div>
    ${isTeam() && isAdmin() ? `<div><span class="muted small">Mitglieder (sehen ${kind === "list" ? "die Liste" : "das Projekt"} mit allen Aufgaben – der Chef sieht immer alles)</span>
      <div class="who" style="display:flex;flex-wrap:wrap;gap:4px 14px">${T.people.map(u =>
      `<label class="inline"><input type="checkbox" data-mem="${u.id}" ${members.includes(u.id) || (!project && u.id === state.me.id) ? "checked" : ""}> ${esc(u.display_name)}</label>`).join("")}</div></div>`
    : isTeam() && project ? `<div><span class="muted small">Mitglieder</span><div class="who" style="display:flex;flex-wrap:wrap;gap:4px 14px">${T.people.map(u =>
      `<label class="inline"><input type="checkbox" data-mem="${u.id}" ${members.includes(u.id) ? "checked" : ""} ${u.id === state.me.id ? "disabled" : ""}> ${esc(u.display_name)}</label>`).join("")}</div></div>` : ""}
    <div class="row"><button class="primary" id="pfSave">${project ? "Speichern" : "Anlegen"}</button><button id="pfCancel">Abbrechen</button>
      ${project ? `<button id="pfArchive">${project.archived ? "Wieder aktivieren" : "Archivieren"}</button><button class="danger" id="pfDelete">Löschen</button>` : ""}</div>
    <div class="err" id="pfErr"></div>`;
  $("projForm").classList.remove("hidden");
  const kindNow = () => (($$('input[name="pfKind"]').find(r => r.checked) || {}).value) || kind;
  const syncKind = () => { $("pfCustWrap").classList.toggle("hidden", kindNow() !== "project");
                           $("pfBudgetWrap").classList.toggle("hidden", kindNow() !== "project" || !canProject); };
  $$('input[name="pfKind"]').forEach(r => r.onchange = syncKind); syncKind();
  $("pfCust").onchange = () => $("pfNewCustWrap").classList.toggle("hidden", $("pfCust").value !== "new");
  $$("#pfColors [data-color]").forEach(b => b.onclick = () => { $$("#pfColors button").forEach(x => x.classList.remove("on")); b.classList.add("on"); });
  $("pfCancel").onclick = closeProjectForm;
  $("pfSave").onclick = () => saveProject(kindNow());
  if (project) {
    $("pfArchive").onclick = async () => {
      try { await api(`projects/${project.id}`, { method: "PATCH", body: { archived: !project.archived } });
            closeProjectForm(); T.projectId = null; T.view = defaultView(); await loadTasks(); }
      catch (e) { $("pfErr").textContent = e.message; }
    };
    $("pfDelete").onclick = async () => {
      if (!confirm(`„${project.name}“ löschen?`)) return;
      try { await api(`projects/${project.id}`, { method: "DELETE" }); closeProjectForm(); T.projectId = null; T.view = defaultView(); await loadTasks(); }
      catch (e) { $("pfErr").textContent = e.message; }
    };
  }
  $("pfName").focus();
}
function closeProjectForm() { $("projForm").classList.add("hidden"); $("projForm").innerHTML = ""; T.editProject = null; }

async function saveProject(kind) {
  $("pfErr").textContent = "";
  const p = T.editProject;
  try {
    let customerId = kind === "project" && $("pfCust").value && $("pfCust").value !== "new" ? Number($("pfCust").value) : null;
    if (kind === "project" && $("pfCust").value === "new") {
      const name = $("pfNewCust").value.trim();
      if (!name) throw new Error("Bitte den Namen des neuen Kunden eintragen");
      customerId = (await api("customers", { method: "POST", body: { name } })).id;
    }
    const body = { name: $("pfName").value.trim(), color: ($$("#pfColors button.on")[0] || {}).dataset.color || COLORS[0] };
    if (kind === "project") body.customer_id = customerId;
    if (kind === "project" && (isAdmin() || !isTeam())) {
      const raw = $("pfBudget").value.trim().replace(",", ".");
      if (raw && !(Number(raw) > 0)) throw new Error("Budget bitte als Stunden angeben, z. B. 40 oder 12,5");
      if (raw || (p && p.budget)) body.budget_minutes = raw ? Math.round(Number(raw) * 60) : null;
    }
    const mem = $$("#projForm [data-mem]");
    if (mem.length) body.member_ids = mem.filter(c => c.checked).map(c => Number(c.dataset.mem));
    if (!body.name) throw new Error("Bitte einen Namen eingeben");
    const saved = p ? await api(`projects/${p.id}`, { method: "PATCH", body })
                    : await api("projects", { method: "POST", body: { ...body, kind } });
    closeProjectForm();
    T.projectId = saved.id; T.view = null; store.set("taskView", "p:" + saved.id);
    await loadTasks();
  } catch (e) { $("pfErr").textContent = e.message; }
}

// --- Ereignisse ---
$("qaAdd").onclick = addQuick;
$("qaText").addEventListener("keydown", e => { if (e.key === "Enter") addQuick(); });
$("qaText").addEventListener("input", previewQuick);
$("showDone").onchange = () => refreshTasks().catch(e => { $("taskErr").textContent = e.message; });
$("projEditBtn").onclick = () => { const p = currentProject(); if (p) openProjectForm(p); };
$("taskViewSel").onchange = () => {
  const v = $("taskViewSel").value;
  if (v === "new:list") { openProjectForm(null, "list"); renderSide(); return; }
  selectNav(v);
};
window.loadTasks = loadTasks;
