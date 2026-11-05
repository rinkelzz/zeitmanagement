"use strict";
// Kalender: Monat, Woche, Tag; Termine anlegen/bearbeiten; Aufgaben einblenden; Kalender-Abo unter „Mein Zugang“.
// Nutzt die Hilfen aus app.js/tasks.js ($, $$, api, esc, state, isTeam, isAdmin, okMsg, store, RECUR_OPTS).

const CAL = { view: null, anchor: new Date(), events: [], tasks: [], shifts: [], projects: [], people: [], edit: null };
const HOUR_PX = 44;
const MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const WD_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const ALARM_OPTS = [["", "keine"], ["0", "zu Beginn"], ["5", "5 Minuten vorher"], ["15", "15 Minuten vorher"], ["30", "30 Minuten vorher"],
                    ["60", "1 Stunde vorher"], ["120", "2 Stunden vorher"], ["1440", "1 Tag vorher"], ["2880", "2 Tage vorher"]];

const cIso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const cAdd = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const cMonday = (d) => { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 12); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
const hhmm = (iso) => iso.slice(11, 16);
const minOfDay = (iso) => Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16));

function calRange() {
  const a = CAL.anchor;
  if (CAL.view === "month") {
    const first = new Date(a.getFullYear(), a.getMonth(), 1, 12);
    const start = cMonday(first);
    return [start, cAdd(start, 41)];
  }
  if (CAL.view === "week") { const s = cMonday(a); return [s, cAdd(s, 6)]; }
  const d = new Date(a.getFullYear(), a.getMonth(), a.getDate(), 12);
  return [d, d];
}

async function loadCalendar() {
  if (!CAL.view) CAL.view = store.get("calView") || (window.innerWidth < 720 ? "day" : "week");
  const [projects, people] = await Promise.all([api("projects"), api("people")]);
  CAL.projects = projects.filter(p => !p.archived); CAL.people = people;
  await refreshCalendar();
}

async function refreshCalendar() {
  $("calErr").textContent = "";
  $$("[data-cview]").forEach(b => b.classList.toggle("on", b.dataset.cview === CAL.view));
  const [a, b] = calRange();
  try {
    const r = await api(`events?from=${cIso(a)}&to=${cIso(b)}&with_tasks=${$("calTasks").checked}`);
    CAL.events = r.events; CAL.tasks = r.tasks; CAL.shifts = r.shifts || [];
  } catch (e) { $("calErr").textContent = e.message; return; }
  const an = CAL.anchor;
  $("calTitle").textContent = CAL.view === "month" ? `${MONTHS[an.getMonth()]} ${an.getFullYear()}`
    : CAL.view === "week" ? `KW ${isoWeekNo(a)} · ${a.getDate()}.${a.getMonth() + 1}.–${b.getDate()}.${b.getMonth() + 1}.${b.getFullYear()}`
    : an.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  if (CAL.view === "month") renderMonth(a); else renderGrid(a, CAL.view === "week" ? 7 : 1);
}
function isoWeekNo(d) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7; t.setUTCDate(t.getUTCDate() + 4 - day);
  return Math.ceil(((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / 864e5 + 1) / 7);
}

function eventsOn(day) {
  return CAL.events.filter(e => e.day <= day && e.last_day >= day && (e.all_day || e.day === day || hhmm(e.start) !== "00:00" || e.day < day));
}
function chip(e, day) {
  const cont = e.day < day;
  return `<div class="cev ${e.all_day ? "allday" : ""}" data-ev="${e.id}" data-evday="${e.day}" style="--c:${esc(e.color)}" title="${esc(e.title)}">` +
    `${e.all_day || cont ? "" : `<b>${hhmm(e.start)}</b> `}${e.recur ? "🔁 " : ""}${esc(e.title)}</div>`;
}
function taskChip(t) {
  return `<div class="ctask ${t.done ? "done" : ""}" data-task="${t.id}" title="Aufgabe${t.project ? " – " + esc(t.project) : ""}">` +
    `${t.done ? "☑" : "☐"} ${t.time ? `<b>${t.time}</b> ` : ""}${esc(t.title)}</div>`;
}

function shiftChip(s) {
  return `<div class="cshift" data-goroster="${s.day}" style="--c:${esc(s.color)}" title="Dienstplan: ${esc(s.label)}${s.note ? " – " + esc(s.note) : ""}">` +
    `${s.kind === "work" ? `<b>${s.start_time}–${s.end_time}</b> ` : ""}${esc(s.label)}</div>`;
}
const shiftsOn = (iso) => CAL.shifts.filter(s => s.day === iso).map(shiftChip).join("");

function renderMonth(start) {
  const today = cIso(new Date()), month = CAL.anchor.getMonth();
  let h = '<div class="mgrid">' + WD_SHORT.map(w => `<div class="mhead">${w}</div>`).join("");
  for (let i = 0; i < 42; i++) {
    const d = cAdd(start, i), iso = cIso(d);
    const evs = eventsOn(iso), tks = CAL.tasks.filter(t => t.day === iso);
    const items = CAL.shifts.filter(s => s.day === iso).map(shiftChip).concat(evs.map(e => chip(e, iso)), tks.map(taskChip));
    const max = 4;
    h += `<div class="mcell ${d.getMonth() !== month ? "other" : ""} ${iso === today ? "today" : ""} ${i % 7 > 4 ? "we" : ""}" data-day="${iso}">
      <div class="mday"><span>${d.getDate()}</span></div>${items.slice(0, max).join("")}
      ${items.length > max ? `<div class="more" data-goday="${iso}">+${items.length - max} weitere</div>` : ""}</div>`;
  }
  $("calBody").innerHTML = h + "</div>";
  bindCal();
}

function layoutDay(list) {
  // nebeneinander, wenn sich Termine überschneiden
  const items = list.map(e => ({ e, s: e.dayStart, en: e.dayEnd })).sort((a, b) => a.s - b.s || b.en - a.en);
  let cluster = [], clusterEnd = -1;
  const flush = () => { const cols = Math.max(...cluster.map(x => x.col)) + 1; cluster.forEach(x => { x.cols = cols; }); cluster = []; };
  const colEnds = [];
  items.forEach(it => {
    if (it.s >= clusterEnd && cluster.length) { flush(); colEnds.length = 0; }
    let c = colEnds.findIndex(end => end <= it.s);
    if (c < 0) { c = colEnds.length; colEnds.push(it.en); } else colEnds[c] = it.en;
    it.col = c; cluster.push(it); clusterEnd = Math.max(clusterEnd, it.en);
  });
  if (cluster.length) flush();
  return items;
}

function renderGrid(start, ndays) {
  const days = Array.from({ length: ndays }, (_, i) => cAdd(start, i));
  const today = cIso(new Date());
  const cols = `grid-template-columns: 48px repeat(${ndays}, minmax(0, 1fr))`;
  let h = `<div class="wgrid"><div class="whead" style="${cols}"><div></div>` +
    days.map(d => `<div class="${cIso(d) === today ? "today" : ""}" data-goday="${cIso(d)}"><span class="muted">${WD_SHORT[(d.getDay() + 6) % 7]}</span> <b>${d.getDate()}.</b></div>`).join("") + "</div>";
  // ganztägig + Aufgaben ohne Zeit
  h += `<div class="wallday" style="${cols}"><div class="muted small">ganzt.</div>` + days.map(d => {
    const iso = cIso(d);
    return `<div data-day="${iso}">${shiftsOn(iso)}${CAL.events.filter(e => e.all_day && e.day <= iso && e.last_day >= iso).map(e => chip(e, iso)).join("")}` +
      CAL.tasks.filter(t => t.day === iso && !t.time).map(taskChip).join("") + "</div>";
  }).join("") + "</div>";
  h += `<div class="wbody" id="wbody"><div class="wtimes" style="${cols}"><div class="hours">` +
    Array.from({ length: 24 }, (_, i) => `<div style="height:${HOUR_PX}px">${i ? String(i).padStart(2, "0") + ":00" : ""}</div>`).join("") + "</div>";
  days.forEach(d => {
    const iso = cIso(d);
    const timed = CAL.events.filter(e => !e.all_day && e.day <= iso && e.last_day >= iso).map(e => ({
      ...e, dayStart: e.day < iso ? 0 : minOfDay(e.start), dayEnd: e.last_day > iso ? 1440 : Math.max(minOfDay(e.end), (e.day < iso ? 0 : minOfDay(e.start)) + 15) }));
    const tk = CAL.tasks.filter(t => t.day === iso && t.time).map(t => ({ task: t, dayStart: minOfDay("0000-00-00T" + t.time), dayEnd: minOfDay("0000-00-00T" + t.time) + 30 }));
    let col = `<div class="wday ${iso === today ? "today" : ""}" data-day="${iso}" style="height:${24 * HOUR_PX}px">`;
    layoutDay(timed.concat(tk)).forEach(it => {
      const top = it.s / 60 * HOUR_PX, ht = Math.max(18, (it.en - it.s) / 60 * HOUR_PX - 2);
      const left = `calc(${it.col / it.cols * 100}% + 2px)`, width = `calc(${100 / it.cols}% - 4px)`;
      if (it.e.task) {
        const t = it.e.task;
        col += `<div class="wev wtask ${t.done ? "done" : ""}" data-task="${t.id}" style="top:${top}px;height:${ht}px;left:${left};width:${width}">${t.done ? "☑" : "☐"} ${esc(t.title)}</div>`;
      } else {
        const e = it.e;
        const short = ht < 34;
        col += `<div class="wev ${short ? "short" : ""}" data-ev="${e.id}" data-evday="${e.day}" style="--c:${esc(e.color)};top:${top}px;height:${ht}px;left:${left};width:${width}" title="${esc(e.title)} · ${hhmm(e.start)}–${hhmm(e.end)}">
          <b>${esc(e.title)}</b><span>${short ? "" : ""}${hhmm(e.start)}${short ? "" : "–" + hhmm(e.end)}${e.recur ? " · 🔁" : ""}${!short && e.location ? " · " + esc(e.location) : ""}</span></div>`;
      }
    });
    if (iso === today) { const n = new Date(); col += `<div class="nowline" style="top:${(n.getHours() * 60 + n.getMinutes()) / 60 * HOUR_PX}px"></div>`; }
    h += col + "</div>";
  });
  $("calBody").innerHTML = h + "</div></div></div>";
  const wb = $("wbody");
  if (wb) wb.scrollTop = 7 * HOUR_PX;
  bindCal();
}

function bindCal() {
  $$("#calBody [data-ev]").forEach(el => el.onclick = (ev) => { ev.stopPropagation(); openEvent(Number(el.dataset.ev), el.dataset.evday); });
  $$("#calBody [data-task]").forEach(el => el.onclick = (ev) => { ev.stopPropagation(); store.set("taskView", "open"); showTab("tasks"); });
  $$("#calBody [data-goroster]").forEach(el => el.onclick = (ev) => {
    ev.stopPropagation(); if (window.RS) RS.anchor = new Date(el.dataset.goroster + "T12:00:00"); showTab("roster");
  });
  $$("#calBody [data-goday]").forEach(el => el.onclick = (ev) => {
    ev.stopPropagation(); CAL.anchor = new Date(el.dataset.goday + "T12:00:00"); CAL.view = "day"; store.set("calView", "day"); refreshCalendar();
  });
  $$("#calBody .mcell").forEach(el => el.onclick = () => newEvent(el.dataset.day, 9 * 60));
  $$("#calBody .wday").forEach(el => el.onclick = (ev) => {
    const y = ev.offsetY + (ev.target === el ? 0 : ev.target.offsetTop);
    newEvent(el.dataset.day, Math.min(23 * 60, Math.floor(y / HOUR_PX * 2) * 30));
  });
  $$("#calBody .wallday > div[data-day]").forEach(el => el.onclick = () => newEvent(el.dataset.day, null));
}

// --- Termin-Formular ---
function newEvent(day, startMin) {
  const allDay = startMin == null;
  const s = startMin ?? 9 * 60, e = Math.min(s + 60, 23 * 60 + 59);
  const t = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  showEventForm({ id: null, title: "", all_day: allDay, day, start: `${day}T${t(s)}`, end: `${day}T${t(e)}`, last_day: day,
                  location: "", notes: "", project_id: null, recur: null, recur_until: null, alarm_minutes: null, attendee_ids: [], can_edit: true });
}
async function openEvent(id, day) {
  try {
    const e = await api(`events/${id}`);
    // für die Serie: Datum/Uhrzeit des Serienbeginns bearbeiten, aber wissen, welcher Termin geklickt wurde
    showEventForm({ ...e, clickedDay: day });
  } catch (err) { $("calErr").textContent = err.message; }
}
function showEventForm(e) {
  CAL.edit = e;
  const ro = !e.can_edit;
  const proj = '<option value="">– kein Projekt –</option>' + CAL.projects.map(p => `<option value="${p.id}" ${p.id === e.project_id ? "selected" : ""}>${esc((p.customer ? p.customer + " · " : "") + p.name)}</option>`).join("");
  const rec = RECUR_OPTS.map(([v, l]) => `<option value="${v}" ${(e.recur || "").split("@")[0] === v ? "selected" : ""}>${l}</option>`).join("") +
    (e.recur && !RECUR_OPTS.some(([v]) => v === e.recur.split("@")[0]) ? `<option value="${esc(e.recur)}" selected>${esc(e.recur_label)}</option>` : "");
  const who = isTeam() ? `<div><span class="muted small">Teilnehmer (sehen den Termin auch)</span><div class="who" style="display:flex;flex-wrap:wrap;gap:4px 14px">` +
    CAL.people.filter(u => u.id !== (e.created_by || state.me.id)).map(u => `<label class="inline"><input type="checkbox" data-att="${u.id}" ${e.attendee_ids.includes(u.id) ? "checked" : ""}> ${esc(u.display_name)}</label>`).join("") + "</div></div>" : "";
  const sd = (e.series_start || e.start).slice(0, 10), st = hhmm(e.series_start || e.start);
  const ed = e.all_day ? (e.id ? cIso(cAdd(new Date((e.series_end || e.end).slice(0, 10) + "T12:00:00"), -1)) : e.last_day) : (e.series_end || e.end).slice(0, 10);
  const et = hhmm(e.series_end || e.end);
  $("evForm").innerHTML = `<div class="toolbar"><h2>${e.id ? (ro ? "Termin" : "Termin bearbeiten") : "Neuer Termin"}</h2><button class="mini" id="evClose">✕</button></div>
    <fieldset ${ro ? "disabled" : ""} class="evfields">
    <label>Titel <input id="evTitle" maxlength="200" value="${esc(e.title)}" placeholder="z. B. Termin bei Physio Henze"></label>
    <label class="inline"><input type="checkbox" id="evAllDay" ${e.all_day ? "checked" : ""}> ganztägig</label>
    <div class="row"><label>Beginn <input type="date" id="evSD" value="${sd}"></label><label class="evTime">&nbsp;<input type="time" id="evST" value="${st}" step="300"></label>
      <label>Ende <input type="date" id="evED" value="${ed}"></label><label class="evTime">&nbsp;<input type="time" id="evET" value="${et}" step="300"></label></div>
    <div class="row"><label>Wiederholung <select id="evRec">${rec}</select></label>
      <label id="evUntilWrap">bis (optional) <input type="date" id="evUntil" value="${esc(e.recur_until || "")}"></label>
      <label>Erinnerung (Handy) <select id="evAlarm">${ALARM_OPTS.map(([v, l]) => `<option value="${v}" ${String(e.alarm_minutes ?? "") === v ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
    <div class="row"><label style="flex:1">Ort <input id="evLoc" maxlength="200" value="${esc(e.location)}"></label>
      <label style="flex:1">Projekt <select id="evProj">${proj}</select></label></div>
    ${who}
    <label>Notiz <textarea id="evNotes" maxlength="5000">${esc(e.notes)}</textarea></label>
    </fieldset>
    ${e.recur && e.id ? `<p class="muted small" style="margin:0">🔁 Serie ${esc(e.recur_label)} – Änderungen gelten für alle Termine der Serie.</p>` : ""}
    ${ro ? `<p class="muted small">Angelegt von ${esc(e.owner || "?")} – nur die anlegende Person oder der Chef kann ihn ändern.</p>` : ""}
    <div class="row">${ro ? "" : '<button class="primary" id="evSave">Speichern</button>'}<button id="evCancel">Schließen</button>
      ${!ro && e.id ? (e.recur ? `<button class="danger" id="evDelOne">Nur diesen Termin löschen</button><button class="danger" id="evDelAll">Ganze Serie löschen</button>`
                              : '<button class="danger" id="evDelAll">Löschen</button>') : ""}</div>
    <div class="err" id="evErr"></div>`;
  $("evModal").classList.remove("hidden");
  const sync = () => {
    $$(".evTime").forEach(x => x.classList.toggle("hidden", $("evAllDay").checked));
    $("evUntilWrap").classList.toggle("hidden", !$("evRec").value);
  };
  $("evAllDay").onchange = sync; $("evRec").onchange = sync; sync();
  $("evSD").onchange = () => { if ($("evED").value < $("evSD").value) $("evED").value = $("evSD").value; };
  $("evST").onchange = () => {
    if ($("evSD").value === $("evED").value && $("evET").value <= $("evST").value) {
      const m = Math.min(minOfDay("0000-00-00T" + $("evST").value) + 60, 1439);
      $("evET").value = `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
    }
  };
  $("evClose").onclick = $("evCancel").onclick = closeEventForm;
  if ($("evSave")) $("evSave").onclick = saveEvent;
  if ($("evDelAll")) $("evDelAll").onclick = () => deleteEvent(false);
  if ($("evDelOne")) $("evDelOne").onclick = () => deleteEvent(true);
  if (!ro) $("evTitle").focus();
}
function closeEventForm() { $("evModal").classList.add("hidden"); $("evForm").innerHTML = ""; CAL.edit = null; }

async function saveEvent() {
  const e = CAL.edit;
  const allDay = $("evAllDay").checked;
  const body = {
    title: $("evTitle").value.trim(), all_day: allDay,
    start: `${$("evSD").value}T${allDay ? "00:00" : $("evST").value}`, end: `${$("evED").value}T${allDay ? "00:00" : $("evET").value}`,
    location: $("evLoc").value.trim(), notes: $("evNotes").value, project_id: $("evProj").value ? Number($("evProj").value) : null,
    recur: $("evRec").value || null, recur_until: $("evRec").value && $("evUntil").value ? $("evUntil").value : null,
    alarm_minutes: $("evAlarm").value === "" ? null : Number($("evAlarm").value),
  };
  if (isTeam()) body.attendee_ids = $$("#evForm [data-att]").filter(c => c.checked).map(c => Number(c.dataset.att));
  if (!body.title) { $("evErr").textContent = "Bitte einen Titel eingeben"; return; }
  if (e.id && body.recur === (e.recur || null)) delete body.recur;  // Monatstag der Serie nicht unnötig neu setzen
  try {
    await api(e.id ? `events/${e.id}` : "events", { method: e.id ? "PATCH" : "POST", body });
    closeEventForm(); await refreshCalendar();
  } catch (err) { $("evErr").textContent = err.message; }
}
async function deleteEvent(onlyThis) {
  const e = CAL.edit;
  const day = e.clickedDay || e.day;
  if (!confirm(onlyThis ? `Nur den Termin am ${day.slice(8, 10)}.${day.slice(5, 7)}.${day.slice(0, 4)} löschen?` : `„${e.title}“ ${e.recur ? "mit allen Terminen der Serie " : ""}löschen?`)) return;
  try { await api(`events/${e.id}` + (onlyThis ? `?day=${day}` : ""), { method: "DELETE" }); closeEventForm(); await refreshCalendar(); }
  catch (err) { $("evErr").textContent = err.message; }
}

// --- Kalender-Abo (Mein Zugang) ---
async function loadFeed(created) {
  $("feedErr").textContent = "";
  try {
    const st = await api("me/calendar-feed");
    let h = "";
    if (created) {
      h += `<div class="feedlink"><code id="feedUrl">${esc(created.url)}</code>
        <div class="row"><button class="mini" id="feedCopy">Link kopieren</button><a class="btn small" href="${esc(created.webcal)}">Auf diesem Gerät abonnieren</a></div>
        <p class="muted small" style="margin:6px 0 0">Der Link wird nur jetzt angezeigt. Wie ein Passwort behandeln – wer ihn kennt, sieht deine Termine.</p></div>`;
    }
    h += `<div class="row" style="margin-top:8px">${st.active
      ? `<span>✅ Abo aktiv${st.last_used_at ? ` · zuletzt abgerufen ${esc(fmtDT(st.last_used_at))}` : " · noch nicht abgerufen"}</span>
         <label class="inline"><input type="checkbox" id="feedTasks" ${st.with_tasks ? "checked" : ""}> fällige Aufgaben mitschicken</label>
         <button class="mini" id="feedNew">Neuen Link erzeugen</button><button class="mini danger" id="feedDel">Link sperren</button>`
      : `<label class="inline"><input type="checkbox" id="feedTasks" checked> fällige Aufgaben mitschicken</label><button class="primary" id="feedNew">Abo-Link erzeugen</button>`}</div>`;
    if (!st.public) h += `<p class="small" style="color:var(--warn)">Hinweis: Ohne öffentliche https-Adresse (Verwaltung → Einstellungen) funktioniert das Abo nur im Heimnetz.</p>`;
    $("feedBox").innerHTML = h;
    if ($("feedCopy")) $("feedCopy").onclick = () => { try { navigator.clipboard.writeText($("feedUrl").textContent); okMsg($("feedErr"), "Kopiert."); } catch (e) { /* egal */ } };
    $("feedNew").onclick = async () => {
      if (st.active && !confirm("Neuen Link erzeugen? Der bisherige Link hört sofort auf zu funktionieren.")) return;
      try { loadFeed(await api("me/calendar-feed", { method: "POST", body: { with_tasks: $("feedTasks").checked } })); }
      catch (e) { $("feedErr").textContent = e.message; }
    };
    if ($("feedDel")) $("feedDel").onclick = async () => {
      if (!confirm("Abo-Link sperren? Abonnierte Kalender bekommen dann keine Termine mehr.")) return;
      try { await api("me/calendar-feed", { method: "DELETE" }); loadFeed(); } catch (e) { $("feedErr").textContent = e.message; }
    };
    if (st.active) $("feedTasks").onchange = async () => {
      try { await api("me/calendar-feed", { method: "PATCH", body: { with_tasks: $("feedTasks").checked } }); okMsg($("feedErr"), "Gespeichert."); }
      catch (e) { $("feedErr").textContent = e.message; }
    };
  } catch (e) { $("feedErr").textContent = e.message; }
}

// --- Ereignisse ---
function calMove(dir) {
  const a = CAL.anchor;
  CAL.anchor = CAL.view === "month" ? new Date(a.getFullYear(), a.getMonth() + dir, 1, 12) : cAdd(a, dir * (CAL.view === "week" ? 7 : 1));
  refreshCalendar();
}
$("calPrev").onclick = () => calMove(-1);
$("calNext").onclick = () => calMove(1);
$("calToday").onclick = () => { CAL.anchor = new Date(); refreshCalendar(); };
$$("[data-cview]").forEach(b => b.onclick = () => { CAL.view = b.dataset.cview; store.set("calView", CAL.view); refreshCalendar(); });
$("calTasks").onchange = refreshCalendar;
$("calNew").onclick = () => newEvent(cIso(CAL.view === "month" ? new Date() : CAL.anchor), 9 * 60);
$("evModal").addEventListener("click", (e) => { if (e.target.id === "evModal") closeEventForm(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && CAL.edit) closeEventForm(); });

window.loadCalendar = loadCalendar;
window.loadFeed = loadFeed;
