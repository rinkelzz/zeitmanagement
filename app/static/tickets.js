"use strict";
// Helpdesk: Ticketliste, Ticketansicht mit Verlauf, Antworten, Anhängen und Stoppuhr; Verwaltung der Formular-Links.
// Nutzt die Hilfen aus app.js ($, $$, api, url, authHeaders, esc, state, isTeam, isAdmin, okMsg, downloadFile, store, modOn)
// und die Stoppuhr aus times.js (TM, startTimer, stopTimer, elapsed, hmsStr, hmStr).

const TK = { view: null, projectId: null, q: "", list: [], counts: {}, projects: [], people: [], open: null, detail: null,
             replyKind: "reply", pending: [], onSite: false, tick: null, thumbs: {} };
const TK_VIEWS = { new: "Neu", mine: "Mir zugewiesen", open: "Alle offenen", wait: "Wartet auf Rückmeldung", done: "Erledigt" };
const TK_ICON = { new: "", mine: "👤 ", open: "📋 ", wait: "⏳ ", done: "✓ " };
const TK_SRC = { form: "📝 Formular", manual: "✍️ Von Hand", mail: "✉️ E-Mail", portal: "👤 Portal" };
const TK_STATUS = { new: "Neu", open: "In Arbeit", wait: "Wartet", done: "Erledigt" };
const TK_PRIO = { 1: "Dringend", 2: "Hoch", 3: "Normal" };

function tkAge(iso) {
  if (!iso) return "";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "gerade eben";
  if (min < 60) return `vor ${min} Min.`;
  if (min < 24 * 60) return `vor ${Math.round(min / 60)} Std.`;
  if (min < 48 * 60) return "gestern";
  if (min < 7 * 24 * 60) return `vor ${Math.round(min / 1440)} Tagen`;
  return deDate(iso.slice(0, 10)).slice(0, 6);
}
const tkHm = (sec) => `${Math.floor(sec / 3600)}:${p2(Math.floor(sec / 60) % 60)}`;
const tkTimes = () => modOn("times");

// ---------- Liste ----------
async function loadTickets() {
  if (TK.view == null && TK.projectId == null) {
    const saved = store.get("tkView") || "";
    if (saved.startsWith("p:")) TK.projectId = Number(saved.slice(2)); else TK.view = TK_VIEWS[saved] ? saved : (isTeam() ? "mine" : "open");
  }
  if (TK.view === "mine" && !isTeam()) TK.view = "open";
  const [projects, people] = await Promise.all([api("projects"), api("people").catch(() => [])]);
  TK.projects = projects.filter(p => p.kind === "project" && !p.archived);
  TK.people = people;
  if (TK.projectId != null && !TK.projects.find(p => p.id === TK.projectId)) { TK.projectId = null; TK.view = "open"; }
  const want = Number(store.get("ticket") || 0);
  if (want) await openTicket(want); else { showListView(); await refreshTickets(); }
}

async function refreshTickets() {
  $("tkErr").textContent = "";
  const p = new URLSearchParams();
  if (TK.projectId != null) { p.set("project_id", TK.projectId); p.set("view", "open"); } else p.set("view", TK.view);
  if (TK.q) p.set("q", TK.q);
  try {
    const [list, counts] = await Promise.all([api("tickets?" + p), api("tickets/counts")]);
    TK.list = list; TK.counts = counts;
  } catch (e) { $("tkErr").textContent = e.message; }
  setTicketBadge(TK.counts);
  renderTkSide(); renderTkList();
}

function setTicketBadge(c) {
  const n = (c && c.new) || 0;
  $("tkBadge").textContent = n; $("tkBadge").classList.toggle("hidden", !n);
}
async function refreshTicketBadge() {
  if (!modOn("helpdesk")) { $("tkBadge").classList.add("hidden"); return; }
  try { setTicketBadge(await api("tickets/counts")); } catch (e) { /* egal */ }
}

function renderTkSide() {
  const c = TK.counts || {}, per = c.projects || {};
  const views = ["new"].concat(isTeam() ? ["mine"] : []).concat(["open", "wait", "done"]);
  const link = (key, label, n, warn, pre = "") => `<a data-tnav="${key}" class="${(TK.projectId == null && TK.view === key) || TK.projectId === key ? "on" : ""}">` +
    `${pre}${label}${n ? `<span class="n ${warn ? "warn" : ""}">${n}</span>` : ""}</a>`;
  let h = "<h4>Tickets</h4>" + views.map(v => link(v, TK_VIEWS[v], v === "done" ? 0 : c[v], v === "new" && c.new > 0,
    v === "new" ? '<span class="dot"></span>' : TK_ICON[v])).join("");
  h += "<h4>Projekte</h4>";
  let last;
  TK.projects.forEach(pj => {
    const cust = pj.customer || "Ohne Kunde";
    if (cust !== last) { h += `<div class="cust">${esc(cust)}</div>`; last = cust; }
    const x = per[String(pj.id)] || {};
    h += `<a data-tnav="${pj.id}" class="${TK.projectId === pj.id ? "on" : ""}"><span class="sq" style="background:${esc(pj.color)}"></span>${esc(pj.name)}` +
         (x.open ? `<span class="n ${x.urgent ? "warn" : ""}">${x.open}</span>` : "") + "</a>";
  });
  if (!TK.projects.length) h += '<div class="cust">Noch keine Projekte – unter Aufgaben anlegen.</div>';
  $("tkSide").innerHTML = h;
  $$("#tkSide [data-tnav]").forEach(a => a.onclick = () => tkNav(a.dataset.tnav));
  $("tkViewSel").innerHTML = views.map(v => `<option value="${v}" ${TK.projectId == null && TK.view === v ? "selected" : ""}>${TK_VIEWS[v]}${c[v] && v !== "done" ? " (" + c[v] + ")" : ""}</option>`).join("") +
    TK.projects.map(pj => `<option value="${pj.id}" ${TK.projectId === pj.id ? "selected" : ""}>${esc((pj.customer ? pj.customer + " · " : "") + pj.name)}</option>`).join("");
}

function tkNav(key) {
  if (/^\d+$/.test(String(key))) { TK.projectId = Number(key); store.set("tkView", "p:" + key); }
  else { TK.projectId = null; TK.view = key; store.set("tkView", key); }
  refreshTickets();
}

function tkRow(t) {
  const prio = t.priority < 3 ? `<span class="prio p${t.priority}">${TK_PRIO[t.priority]}</span>` : "";
  const tm = t.minutes || t.running ? hmStr(t.minutes) : "–";
  const play = tkTimes() && t.status !== "done"
    ? `<button class="play ${t.running ? "run" : ""}" data-tplay="${t.id}" title="${t.running ? "Stoppuhr anhalten" : "Stoppuhr starten"}">${t.running ? "■" : "▶"}</button>` : "";
  return `<div class="ticket ${t.unread ? "unread" : ""} ${t.running ? "isrun" : ""}">
    <div class="body" data-topen="${t.id}"><div class="t">${esc(t.title)}</div>
      <div class="meta"><span class="tnr">${esc(t.number)}</span>${prio}<span class="ptag"><i style="background:${esc(t.project_color)}"></i>${esc(t.project)}</span>
        ${t.requester_name ? `<span>${esc(t.requester_name)}</span>` : ""}<span>${TK_SRC[t.source] || ""}</span>
        ${t.files ? `<span>📎 ${t.files}</span>` : ""}${isTeam() && t.assignee && TK.view !== "mine" ? `<span>→ ${esc(t.assignee)}</span>` : ""}
        <span>${t.status === "done" ? "erledigt " + tkAge(t.done_at) : tkAge(t.created_at)}</span></div></div>
    <span class="st ${t.status}">${TK_STATUS[t.status]}</span>
    <div class="right">${tkTimes() ? `<span class="tm">${tm}</span>` : ""}${play}</div></div>`;
}

function renderTkList() {
  const pj = TK.projectId != null ? TK.projects.find(p => p.id === TK.projectId) : null;
  $("tkTitle").innerHTML = pj ? `<span class="ptag"><i style="background:${esc(pj.color)}"></i>${esc(pj.name)}</span>` +
    (pj.customer ? ` <span class="muted small">${esc(pj.customer)}</span>` : "") : esc(TK_VIEWS[TK.view] || "Tickets");
  const list = TK.list;
  if (!list.length) {
    $("tkList").innerHTML = `<div class="emptylist">${TK.q ? "Nichts gefunden." : TK.view === "done" ? "Noch nichts erledigt." :
      "Keine offenen Tickets 🎉" + (isAdmin() ? '<br><span class="small">Meldeformulare richtest du unter Verwaltung → Helpdesk ein.</span>' : "")}</div>`;
    return;
  }
  let h = "";
  if (["open", "mine"].includes(TK.view) || TK.projectId != null) {
    for (const st of ["new", "open", "wait"]) {
      const g = list.filter(t => t.status === st);
      if (g.length) h += `<div class="group">${st === "wait" ? "Wartet auf Rückmeldung" : TK_STATUS[st]} · ${g.length}</div>` + g.map(tkRow).join("");
    }
  } else h = list.map(tkRow).join("");
  $("tkList").innerHTML = h;
  $$("#tkList [data-topen]").forEach(el => el.onclick = () => openTicket(Number(el.dataset.topen)));
  $$("#tkList [data-tplay]").forEach(b => b.onclick = async (ev) => {
    ev.stopPropagation();
    const t = TK.list.find(x => x.id === Number(b.dataset.tplay));
    try { if (t.running) await stopTimer(); else await startTimer({ ticket_id: t.id }); await refreshTickets(); }
    catch (e) { $("tkErr").textContent = e.message; }
  });
}

// ---------- neues Ticket ----------
function tkPeopleOpts(sel, projectId, withNone) {
  const pj = TK.projects.find(p => p.id === projectId);
  const ids = pj && pj.member_ids && pj.member_ids.length ? pj.member_ids : null;
  const list = TK.people.filter(u => !isTeam() || !ids || ids.includes(u.id) || u.is_admin || u.id === sel);
  return (withNone ? `<option value="0">– niemand –</option>` : "") +
    list.map(u => `<option value="${u.id}" ${u.id === sel ? "selected" : ""}>${esc(u.display_name)}</option>`).join("");
}
function showNewTicket() {
  const box = $("tkNew");
  if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
  if (!TK.projects.length) { $("tkErr").textContent = "Zuerst ein Projekt anlegen (unter Aufgaben)."; return; }
  const pid = TK.projectId || TK.projects[0].id;
  box.innerHTML = `<div class="tform" style="display:grid;gap:10px">
    <div class="row"><label style="flex:1">Projekt <select id="ntkProject">${TK.projects.map(p => `<option value="${p.id}" ${p.id === pid ? "selected" : ""}>${esc((p.customer ? p.customer + " · " : "") + p.name)}</option>`).join("")}</select></label>
      <label>Priorität <select id="ntkPrio"><option value="3">Normal</option><option value="2">Hoch</option><option value="1">Dringend</option></select></label>
      <label class="teamOnly">Bearbeiter <select id="ntkWho">${tkPeopleOpts(state.me.id, pid, true)}</select></label></div>
    <label>Worum geht es? <input id="ntkTitle" maxlength="200" placeholder="z. B. Drucker Empfang druckt nicht"></label>
    <label>Beschreibung <textarea id="ntkBody" maxlength="20000" style="min-height:80px"></textarea></label>
    <div class="row"><label>Melder <input id="ntkName" maxlength="100" placeholder="Name (optional)"></label>
      <label>E-Mail <input id="ntkMail" maxlength="200" placeholder="für Antworten per Mail"></label>
      <label>Telefon <input id="ntkPhone" maxlength="50"></label></div>
    <div class="row"><button class="primary" id="ntkSave">Ticket anlegen</button><button id="ntkCancel">Abbrechen</button>
      <span class="muted small">Ohne Melder-Adresse ist es ein internes Ticket (nur Notizen).</span></div></div>`;
  box.classList.remove("hidden");
  applyMode();
  $("ntkProject").onchange = () => { $("ntkWho").innerHTML = tkPeopleOpts(state.me.id, Number($("ntkProject").value), true); };
  $("ntkCancel").onclick = () => box.classList.add("hidden");
  $("ntkSave").onclick = async () => {
    $("tkErr").textContent = "";
    try {
      const t = await api("tickets", { method: "POST", body: {
        project_id: Number($("ntkProject").value), title: $("ntkTitle").value.trim(), body: $("ntkBody").value,
        priority: Number($("ntkPrio").value), assignee_id: isTeam() ? (Number($("ntkWho").value) || null) : null,
        requester_name: $("ntkName").value.trim(), requester_email: $("ntkMail").value.trim(), requester_phone: $("ntkPhone").value.trim() } });
      box.classList.add("hidden");
      await openTicket(t.id);
    } catch (e) { $("tkErr").textContent = e.message; }
  };
  $("ntkTitle").focus();
}

// ---------- Ticketansicht ----------
function showListView() {
  TK.open = null; TK.detail = null; clearInterval(TK.tick);
  store.set("ticket", "");
  $("tkListView").classList.remove("hidden"); $("tkDetailView").classList.add("hidden");
  if (state.tab === "tickets") history.replaceState(null, "", "#tickets");
}

async function openTicket(id) {
  try { TK.detail = await api("tickets/" + id); }
  catch (e) { store.set("ticket", ""); showListView(); await refreshTickets(); $("tkErr").textContent = e.message; return; }
  if (TK.open !== id) { TK.pending = []; TK.replyKind = TK.detail.requester_email ? "reply" : "note"; TK.onSite = false; }
  TK.open = id;
  store.set("ticket", String(id));
  history.replaceState(null, "", "#tickets/" + id);
  $("tkListView").classList.add("hidden"); $("tkDetailView").classList.remove("hidden");
  renderTicket();
  window.scrollTo(0, 0);
  refreshTicketBadge();
}

function tkFileChip(f, removable) {
  const pdf = f.mime === "application/pdf";
  const thumb = TK.thumbs[f.id];
  return `<button type="button" class="file" data-tfile="${f.id}" title="${esc(f.name)} herunterladen">
    <span class="th ${pdf ? "pdf" : ""}" ${thumb ? `style="background-image:url('${thumb}')"` : ""} ${!pdf ? `data-thumb="${f.id}"` : ""}>${pdf ? "PDF" : thumb ? "" : "🖼"}</span>
    <span>${esc(f.name)}<small>${fmtSize(f.size)}</small></span>${removable ? `<span data-tdel="${f.id}" title="entfernen" style="margin-left:6px">✕</span>` : ""}</button>`;
}

function renderTicket() {
  const t = TK.detail, boss = isAdmin() || !isTeam();
  const filesOf = (mid) => t.attachments.filter(f => f.message_id === mid);
  const author = (m) => m.author || (m.kind === "requester" ? t.requester_name || "Melder" : "");
  let h = `<div class="thead">
    <div class="row small"><a href="#tickets" id="tkBack">← Tickets</a><span class="tnr">${esc(t.number)}</span>
      <span class="muted">${TK_SRC[t.source] || ""} · ${fmtDT(t.created_at)}</span></div>
    <h2 id="tkHeadTitle" title="Zum Ändern klicken">${esc(t.title)}</h2>
    <div class="row">
      <select id="tkStatus" aria-label="Status">${Object.keys(TK_STATUS).map(s => `<option value="${s}" ${s === t.status ? "selected" : ""}>${s === "wait" ? "Wartet auf Rückmeldung" : TK_STATUS[s]}</option>`).join("")}</select>
      <select id="tkPrio" aria-label="Priorität">${[1, 2, 3].map(p => `<option value="${p}" ${p === t.priority ? "selected" : ""}>Priorität: ${TK_PRIO[p]}</option>`).join("")}</select>
      ${isTeam() ? `<select id="tkWho" aria-label="Bearbeiter">${tkPeopleOpts(t.assignee_id, t.project_id, true).replace("– niemand –", "Bearbeiter: niemand")}</select>` : ""}
      ${t.status !== "done" ? '<button id="tkDoneBtn">Erledigt ✓</button>' : '<button id="tkReopen">Wieder öffnen</button>'}
      ${t.can_delete ? '<button class="danger" id="tkDel" title="Ticket mit Verlauf und Anhängen löschen">Löschen</button>' : ""}
    </div></div>`;
  const firstFilesShown = new Set();
  for (const m of t.messages) {
    const fl = filesOf(m.id);
    fl.forEach(f => firstFilesShown.add(f.id));
    const files = fl.length ? `<div class="files">${fl.map(f => tkFileChip(f)).join("")}</div>` : "";
    if (m.kind === "system") { h += `<div class="sys">⚙ ${esc(m.body)} · ${fmtDT(m.created_at)}</div>`; continue; }
    const req = m.kind === "requester";
    const av = `<span class="av ${req ? "melder" : ""}">${esc(initials(author(m)))}</span>`;
    if (m.kind === "note") {
      h += `<div class="msg note">${av}<div><div class="who"><span class="lbl">Interne Notiz</span><b>${esc(author(m))}</b><span>${fmtDT(m.created_at)}</span></div>
        <p>${esc(m.body)}</p>${files}</div></div>`;
    } else {
      h += `<div class="msg">${av}<div><div class="who"><b>${esc(author(m))}</b><span>${req ? (TK_SRC[t.source] || "").replace(/^\S+\s/, "über ") : "Antwort per Mail"} · ${fmtDT(m.created_at)}</span></div>
        <p>${esc(m.body)}</p>${files}</div></div>`;
    }
  }
  const loose = t.attachments.filter(f => !firstFilesShown.has(f.id) && !TK.pending.some(p => p.id === f.id) && f.message_id == null && f.from_requester);
  if (loose.length) h += `<div class="files" style="margin:10px 0 0 44px">${loose.map(f => tkFileChip(f)).join("")}</div>`;
  const canReply = !!t.requester_email;
  const note = TK.replyKind === "note";
  h += `<div class="reply ${note ? "isnote" : ""}">
    <div class="tabs"><button data-rk="reply" class="${!note ? "on" : ""}" ${canReply ? "" : 'disabled title="Keine E-Mail-Adresse des Melders hinterlegt"'}>Antwort an Melder</button>
      <button data-rk="note" class="${note ? "on" : ""}">Interne Notiz</button></div>
    <textarea id="tkText" maxlength="20000" placeholder="${note ? "Notiz – sieht nur das Team" : "Antwort schreiben … geht per Mail an " + esc(t.requester_name || t.requester_email)}"></textarea>
    <div class="pending">${TK.pending.map(f => tkFileChip(f, true)).join("")}</div>
    <div class="bar2">
      <label class="btn mini" style="cursor:pointer">📎 Datei anhängen<input type="file" id="tkFile" multiple accept="image/png,image/jpeg,application/pdf" class="hidden"></label>
      <span class="muted small" style="margin-right:auto">${note ? "nur intern" : canReply ? "geht an " + esc(t.requester_email) + (t.mail_ready ? "" : " – <b>kein Mailserver eingerichtet</b>") : ""}</span>
      <button id="tkSend">${note ? "Notiz speichern" : "Senden"}</button>
      ${t.status !== "done" ? `<button class="primary" id="tkSendDone">${note ? "Speichern" : "Senden"} + Erledigt</button>` : ""}
    </div></div><div class="err" id="tkDErr"></div>`;
  $("tkMain").innerHTML = h;

  // Seitenleiste
  let side = "";
  if (tkTimes()) {
    const mine = TM.running && TM.running.ticket_id === t.id;
    side += `<div class="card"><h3>Zeit</h3>
      <div class="watch ${mine ? "run" : ""}"><div>${mine ? `<span class="pulse" style="display:inline-block;margin-right:6px"></span><span class="small">läuft seit ${timeOf(TM.running.start)}</span>` : '<span class="small muted">Stoppuhr</span>'}<br>
        <b id="tkElapsed">${mine ? hmsStr(elapsed()) : "0:00:00"}</b></div>
        ${t.status === "done" && !mine ? "" : `<button class="big ${mine ? "stop" : "primary"}" id="tkWatch">${mine ? "Stopp" : "Start"}</button>`}</div>
      ${mine ? "" : `<label class="inline small" style="margin-top:10px"><input type="checkbox" id="tkOnSite" ${TK.onSite ? "checked" : ""}> vor Ort</label>`}
      <div style="margin-top:8px">${t.times.map(e => `<div class="bk"><span>${esc(deDate(e.start.slice(0, 10)).slice(0, 6))} · ${timeOf(e.start)}–${e.running ? "läuft" : timeOf(e.end)}${isTeam() ? " · " + esc(e.user_name) : ""}</span>
        <span class="d" ${e.running ? 'style="color:var(--ok)"' : ""}>${tkHm(e.seconds)}</span>${e.note || e.on_site ? `<small>${esc([e.on_site ? "vor Ort" : "", e.note].filter(Boolean).join(" · "))}</small>` : ""}</div>`).join("")}
        ${t.times.length ? `<div class="sum"><span>Summe</span><span>${tkHm(t.seconds)}</span></div>` : '<p class="muted small" style="margin:6px 0 0">Noch keine Zeit gebucht.</p>'}</div>
      <p class="muted small" style="margin:8px 0 0">Wird auf <b>${esc(t.project)}</b> gebucht und erscheint unter Zeiten und Auswertung.</p></div>`;
  }
  side += `<div class="card"><h3>Melder</h3><dl class="kv">
      <dt>Name</dt><dd><input data-req="requester_name" value="${esc(t.requester_name)}" maxlength="100"></dd>
      <dt>E-Mail</dt><dd><input data-req="requester_email" value="${esc(t.requester_email)}" maxlength="200"></dd>
      <dt>Telefon</dt><dd><input data-req="requester_phone" value="${esc(t.requester_phone)}" maxlength="50"></dd></dl></div>
    <div class="card"><h3>Details</h3><dl class="kv">
      <dt>Projekt</dt><dd><select id="tkProject" style="width:100%">${TK.projects.map(p => `<option value="${p.id}" ${p.id === t.project_id ? "selected" : ""}>${esc((p.customer ? p.customer + " · " : "") + p.name)}</option>`).join("")}
        ${TK.projects.some(p => p.id === t.project_id) ? "" : `<option selected>${esc(t.project)}</option>`}</select></dd>
      <dt>Eingang</dt><dd>${esc(t.source_label)}</dd>
      <dt>Erstellt</dt><dd>${fmtDT(t.created_at)}</dd>
      ${t.done_at ? `<dt>Erledigt</dt><dd>${fmtDT(t.done_at)}</dd>` : ""}
      <dt>Anhänge</dt><dd>${t.attachments.length || "keine"}</dd></dl></div>`;
  $("tkPanel").innerHTML = side;
  bindTicket();
  loadThumbs();
  clearInterval(TK.tick);
  if (TM.running && TM.running.ticket_id === t.id) TK.tick = setInterval(() => { const el = $("tkElapsed"); if (el) el.textContent = hmsStr(elapsed()); }, 1000);
}

async function tkPatch(body) {
  $("tkDErr").textContent = "";
  try { TK.detail = await api("tickets/" + TK.open, { method: "PATCH", body }); renderTicket(); }
  catch (e) { $("tkDErr").textContent = e.message; }
}

function bindTicket() {
  const t = TK.detail;
  $("tkBack").onclick = (ev) => { ev.preventDefault(); showListView(); refreshTickets(); };
  $("tkStatus").onchange = () => tkPatch({ status: $("tkStatus").value });
  $("tkPrio").onchange = () => tkPatch({ priority: Number($("tkPrio").value) });
  if ($("tkWho")) $("tkWho").onchange = () => tkPatch({ assignee_id: Number($("tkWho").value) });
  if ($("tkDoneBtn")) $("tkDoneBtn").onclick = () => tkPatch({ status: "done" });
  if ($("tkReopen")) $("tkReopen").onclick = () => tkPatch({ status: "open" });
  if ($("tkDel")) $("tkDel").onclick = async () => {
    if (!confirm(`${t.number} mit Verlauf und Anhängen löschen? Gebuchte Zeiten bleiben erhalten.`)) return;
    try { await api("tickets/" + t.id, { method: "DELETE" }); showListView(); await refreshTickets(); } catch (e) { $("tkDErr").textContent = e.message; }
  };
  $("tkHeadTitle").onclick = () => {
    const v = prompt("Titel des Tickets", t.title);
    if (v && v.trim() && v.trim() !== t.title) tkPatch({ title: v.trim() });
  };
  $("tkProject").onchange = () => {
    if (confirm("Ticket in dieses Projekt verschieben? Nicht abgerechnete Zeiten wandern mit.")) tkPatch({ project_id: Number($("tkProject").value) });
    else $("tkProject").value = t.project_id;
  };
  $$("#tkPanel [data-req]").forEach(inp => inp.onchange = () => tkPatch({ [inp.dataset.req]: inp.value.trim() }));
  $$("#tkMain [data-rk]").forEach(b => b.onclick = () => {
    if (b.disabled) return;
    const keep = $("tkText").value; TK.replyKind = b.dataset.rk; renderTicket(); $("tkText").value = keep; $("tkText").focus();
  });
  $$("[data-tfile]").forEach(b => b.onclick = (ev) => {
    if (ev.target.dataset.tdel) return;
    const f = TK.detail.attachments.find(x => x.id === Number(b.dataset.tfile)) || TK.pending.find(x => x.id === Number(b.dataset.tfile));
    downloadFile(`api/v1/tickets/${TK.open}/files/${b.dataset.tfile}`, f ? f.name : "datei").catch(e => { $("tkDErr").textContent = e.message; });
  });
  $$("[data-tdel]").forEach(x => x.onclick = async (ev) => {
    ev.stopPropagation();
    try { await api(`tickets/${TK.open}/files/${x.dataset.tdel}`, { method: "DELETE" }); TK.pending = TK.pending.filter(p => p.id !== Number(x.dataset.tdel)); keepText(renderTicket); }
    catch (e) { $("tkDErr").textContent = e.message; }
  });
  $("tkFile").onchange = () => uploadTicketFiles(Array.from($("tkFile").files));
  $("tkSend").onclick = () => sendMessage(false);
  if ($("tkSendDone")) $("tkSendDone").onclick = () => sendMessage(true);
  if ($("tkOnSite")) $("tkOnSite").onchange = () => { TK.onSite = $("tkOnSite").checked; };
  if ($("tkWatch")) $("tkWatch").onclick = async () => {
    $("tkDErr").textContent = "";
    try {
      if (TM.running && TM.running.ticket_id === t.id) await stopTimer();
      else await startTimer({ ticket_id: t.id, on_site: TK.onSite });
      keepText(() => openTicket(t.id));
    } catch (e) { $("tkDErr").textContent = e.message; }
  };
}

async function keepText(fn) {
  const v = $("tkText") ? $("tkText").value : "";
  await fn();
  if ($("tkText")) $("tkText").value = v;
}

async function uploadTicketFiles(files) {
  $("tkDErr").textContent = "";
  const errs = [];
  for (const f of files) {
    if (f.size > 10 * 1048576) { errs.push(`${f.name}: zu groß (höchstens 10 MB)`); continue; }
    try {
      const res = await fetch(url(`api/v1/tickets/${TK.open}/files?name=${encodeURIComponent(f.name)}`), {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: authHeaders({ "Content-Type": "application/octet-stream" }), body: f });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Fehler " + res.status);
      TK.pending.push(d);
    } catch (e) { errs.push(`${f.name}: ${e.message}`); }
  }
  await keepText(renderTicket);
  if (errs.length) $("tkDErr").textContent = errs.join(" · ");
}

async function sendMessage(done) {
  const body = $("tkText").value.trim();
  $("tkDErr").textContent = "";
  if (!body) { $("tkDErr").textContent = TK.replyKind === "note" ? "Bitte eine Notiz schreiben." : "Bitte eine Antwort schreiben."; return; }
  try {
    TK.detail = await api(`tickets/${TK.open}/messages`, { method: "POST",
      body: { kind: TK.replyKind, body, file_ids: TK.pending.map(f => f.id), done } });
    TK.pending = [];
    renderTicket();
    if (TK.replyKind === "reply") okMsg($("tkDErr"), done ? "Gesendet und erledigt." : "Gesendet.");
  } catch (e) { $("tkDErr").textContent = e.message; }
}

async function loadThumbs() {
  for (const el of $$("[data-thumb]")) {
    const id = Number(el.dataset.thumb);
    if (TK.thumbs[id]) continue;
    try {
      const res = await fetch(url(`api/v1/tickets/${TK.open}/files/${id}?inline=true`), { credentials: "same-origin", headers: authHeaders() });
      if (!res.ok) continue;
      TK.thumbs[id] = URL.createObjectURL(await res.blob());
      $$(`[data-thumb="${id}"]`).forEach(x => { x.style.backgroundImage = `url('${TK.thumbs[id]}')`; x.textContent = ""; });
    } catch (e) { /* ohne Vorschau */ }
  }
}

// ---------- Verwaltung → Helpdesk ----------
const HD = { forms: [], settings: null };
async function loadHelpdeskAdmin() {
  const [forms, s] = await Promise.all([api("helpdesk/forms"), api("helpdesk/settings")]);
  HD.forms = forms.forms; HD.settings = s;
  $("hdUrlWarn").classList.toggle("hidden", forms.public_url_set);
  renderHdForms(); fillHdSettings();
}
function renderHdForms() {
  const people = (state.users || []).filter(u => u.active);
  $("hdForms").innerHTML = `<thead><tr><th>Projekt</th><th>Link</th>${isTeam() ? "<th>Bearbeiter</th>" : ""}<th class="num">offen</th><th></th></tr></thead><tbody>` +
    (HD.forms.length ? HD.forms.map(f => {
      const live = f.token && f.active;
      return `<tr class="${live ? "" : "inactive"}"><td><span class="ptag"><i style="background:${esc(f.project_color)}"></i>${esc(f.project)}</span>
        ${f.customer ? `<span class="muted small">${esc(f.customer)}</span>` : ""}</td>
      <td>${live ? `<div class="linkbox"><code title="${esc(f.url)}">${esc(f.url)}</code><button class="mini" data-hcopy="${f.project_id}">Kopieren</button>
        <a class="btn mini" href="${esc(f.url)}" target="_blank" rel="noopener" title="Formular öffnen">↗</a></div>` : '<span class="muted">kein öffentliches Formular</span>'}</td>
      ${isTeam() ? `<td>${f.token ? `<select data-hwho="${f.project_id}"><option value="0">– Chef –</option>${people.map(u => `<option value="${u.id}" ${u.id === f.assignee_id ? "selected" : ""}>${esc(u.display_name)}</option>`).join("")}</select>` : "—"}</td>` : ""}
      <td class="num">${f.open || ""}</td>
      <td class="nowrap">${!f.token ? `<button class="mini primary" data-hon="${f.project_id}">Formular aktivieren</button>` :
        f.active ? `<button class="mini" data-hnew="${f.project_id}">Neuer Link</button> <button class="mini" data-hoff="${f.project_id}">Abschalten</button>` :
        `<button class="mini primary" data-hon="${f.project_id}">Wieder aktivieren</button>`}</td></tr>`;
    }).join("") : `<tr><td colspan="5" class="muted">Noch keine Projekte – unter Aufgaben → Projekte anlegen.</td></tr>`) + "</tbody>";
  const put = async (pid, body) => {
    $("hdFormsErr").textContent = "";
    try { HD.forms = (await api("helpdesk/forms/" + pid, { method: "PUT", body })).forms; renderHdForms(); }
    catch (e) { $("hdFormsErr").textContent = e.message; }
  };
  $$("[data-hon]").forEach(b => b.onclick = () => put(b.dataset.hon, { active: true }));
  $$("[data-hoff]").forEach(b => b.onclick = () => confirm("Formular abschalten? Der Link funktioniert dann nicht mehr.") && put(b.dataset.hoff, { active: false }));
  $$("[data-hnew]").forEach(b => b.onclick = () => confirm("Neuen Link erzeugen? Der alte funktioniert dann nicht mehr – z. B. auf Aushängen.") && put(b.dataset.hnew, { regenerate: true }));
  $$("[data-hwho]").forEach(s => s.onchange = () => put(s.dataset.hwho, { assignee_id: Number(s.value) }));
  $$("[data-hcopy]").forEach(b => b.onclick = () => {
    const f = HD.forms.find(x => x.project_id === Number(b.dataset.hcopy));
    try { navigator.clipboard.writeText(f.url); okMsg($("hdFormsErr"), "Link kopiert."); } catch (e) { prompt("Link kopieren:", f.url); }
  });
}
function fillHdSettings() {
  const s = HD.settings;
  $("hdConfirm").checked = s.mail_confirm; $("hdReply").checked = s.mail_reply; $("hdDone").checked = s.mail_done;
  $("hdNotify").checked = s.notify_new; $("hdFiles").checked = s.attachments;
  $("hdHint").value = s.form_hint || ""; $("hdPrivacy").value = s.privacy_url || "";
  $("hdRetention").value = String(s.retention_months);
}
$("hdSave").onclick = async () => {
  $("hdErr").textContent = "";
  try {
    HD.settings = await api("helpdesk/settings", { method: "PATCH", body: {
      mail_confirm: $("hdConfirm").checked, mail_reply: $("hdReply").checked, mail_done: $("hdDone").checked,
      notify_new: $("hdNotify").checked, attachments: $("hdFiles").checked, form_hint: $("hdHint").value.trim(),
      privacy_url: $("hdPrivacy").value.trim(), retention_months: Number($("hdRetention").value) } });
    fillHdSettings(); okMsg($("hdErr"), "Gespeichert.");
  } catch (e) { $("hdErr").textContent = e.message; }
};

// ---------- Ereignisse ----------
$("tkNewBtn").onclick = showNewTicket;
$("tkViewSel").onchange = () => tkNav($("tkViewSel").value);
let tkSearchT = null;
$("tkSearch").oninput = () => { clearTimeout(tkSearchT); tkSearchT = setTimeout(() => { TK.q = $("tkSearch").value.trim(); refreshTickets(); }, 250); };
setInterval(() => {
  if (!state.me || document.hidden || !modOn("helpdesk")) return;
  if (state.tab === "tickets" && TK.open == null) refreshTickets(); else refreshTicketBadge();
}, 60000);
window.loadTickets = loadTickets;
window.refreshTicketBadge = refreshTicketBadge;
window.loadHelpdeskAdmin = loadHelpdeskAdmin;
