"use strict";
// Dienstplan: Wochenraster Mitarbeiter × Tage, Schichten per Klick, Vorwoche übernehmen, veröffentlichen, PDF.
// Schichtarten verwalten unter Verwaltung → Dienstplan. Nutzt die Hilfen aus app.js und calendar.js (cIso, cAdd, cMonday, isoWeekNo).

const RS = { anchor: new Date(), data: null, cell: null };
const RS_WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const hm = (m) => `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;

async function loadRoster() {
  $("rsErr").textContent = "";
  try { RS.data = await api(`roster?week=${cIso(cMonday(RS.anchor))}`); }
  catch (e) { $("rsErr").textContent = e.message; return; }
  renderRoster();
}

function shiftPill(s, edit) {
  const time = s.kind === "work" ? `${s.start_time}–${s.end_time}` : "";
  const name = s.kind === "work" ? (s.short || "") : s.label;
  return `<div class="rshift ${s.kind}" style="--c:${esc(s.color)}" ${edit ? `data-shift="${s.id}"` : ""}
    title="${esc(s.label)}${time ? " · " + time : ""}${s.break_minutes ? ` · ${s.break_minutes} Min. Pause` : ""}${s.note ? " · " + esc(s.note) : ""}">
    ${name ? `<b>${esc(name)}</b>` : ""}${time ? `<span>${time}</span>` : ""}${s.note ? '<i title="Notiz">✎</i>' : ""}</div>`;
}

function renderRoster() {
  const d = RS.data;
  const ws = new Date(d.week_start + "T12:00:00");
  const we = cAdd(ws, 6);
  $("rsTitle").textContent = `KW ${isoWeekNo(ws)} · ${ws.getDate()}.${ws.getMonth() + 1}.–${we.getDate()}.${we.getMonth() + 1}.${we.getFullYear()}`;
  const edit = d.can_edit;
  $("rsState").innerHTML = d.published
    ? `<span class="tag pub">✓ veröffentlicht</span>${d.published_at ? `<span class="muted small"> ${esc(fmtDT(d.published_at))}${d.published_by ? " · " + esc(d.published_by) : ""}</span>` : ""}`
    : (edit ? '<span class="tag draft">Entwurf – Mitarbeiter sehen ihn noch nicht</span>' : "");
  $$(".rsBoss").forEach(el => el.classList.toggle("hidden", !edit));
  $("rsPublish").textContent = d.published ? "Erneut veröffentlichen" : "Veröffentlichen";
  $("rsUnpublish").classList.toggle("hidden", !edit || !d.published);
  $("rsPdf").classList.toggle("hidden", !d.visible);
  if (!d.visible) {
    $("rsBody").innerHTML = `<div class="empty"><div style="font-size:34px">🗓️</div><h3>Noch nicht veröffentlicht</h3>
      <p>Der Dienstplan für diese Woche ist noch in Arbeit. Sobald der Chef ihn veröffentlicht, steht er hier – und in deinem Kalender.</p></div>`;
    $("rsMine").innerHTML = "";
    return;
  }
  if (!d.users.length) {
    $("rsBody").innerHTML = `<div class="empty"><h3>Niemand im Dienstplan</h3><p>Unter Verwaltung → Dienstplan festlegen, wer eingeplant wird.</p></div>`;
    return;
  }
  const today = cIso(new Date());
  const byCell = {};
  d.shifts.forEach(s => { (byCell[s.user_id + "|" + s.day] = byCell[s.user_id + "|" + s.day] || []).push(s); });
  let h = `<table class="rgrid ${edit ? "editable" : ""}"><thead><tr><th class="rname">Mitarbeiter</th>` +
    d.days.map((day, i) => `<th class="${day === today ? "today" : ""} ${i > 4 ? "we" : ""}">${RS_WD[i]} <span class="muted">${Number(day.slice(8))}.${Number(day.slice(5, 7))}.</span></th>`).join("") +
    `<th class="rsum">Std.</th></tr></thead><tbody>`;
  d.users.forEach(u => {
    const mine = u.id === state.me.id;
    h += `<tr class="${mine ? "mine" : ""}"><th class="rname"><span class="av sm">${esc(initials(u.display_name))}</span>${esc(u.display_name)}</th>`;
    d.days.forEach((day, i) => {
      const cells = byCell[u.id + "|" + day] || [];
      h += `<td class="rcell ${day === today ? "today" : ""} ${i > 4 ? "we" : ""}" ${edit ? `data-cell="${u.id}|${day}"` : ""}>` +
        cells.map(s => shiftPill(s, edit)).join("") + (edit && !cells.length ? '<span class="radd">＋</span>' : "") + "</td>";
    });
    h += `<td class="rsum">${hm(d.totals[u.id] || 0)}</td></tr>`;
  });
  h += `</tbody><tfoot><tr><th class="rname muted small">Besetzung</th>` + d.coverage.map((c, i) => `<td class="${i > 4 ? "we" : ""}">
    <b>${c.working}</b> <span class="muted small">im Dienst</span>
    <div class="muted small">${Object.entries(c.by_type).map(([k, v]) => `${v}× ${esc(k)}`).join(" · ")}</div></td>`).join("") +
    `<td class="rsum muted small">${hm(Object.values(d.totals).reduce((a, b) => a + b, 0))}</td></tr></tfoot></table>`;
  $("rsBody").innerHTML = `<div class="scroll rscroll">${h}</div>`;
  // Handy: eigene Woche als Liste
  const own = d.shifts.filter(s => s.user_id === state.me.id);
  $("rsMine").innerHTML = d.users.some(u => u.id === state.me.id) ? `<h3>Meine Woche</h3>` + d.days.map((day, i) => {
    const ss = own.filter(s => s.day === day);
    return `<div class="rmine ${day === today ? "today" : ""}"><span class="d">${RS_WD[i]} ${Number(day.slice(8))}.${Number(day.slice(5, 7))}.</span>
      <span>${ss.length ? ss.map(s => shiftPill(s, false)).join("") : '<span class="muted">frei</span>'}</span></div>`;
  }).join("") : "";
  if (edit) {
    $$("#rsBody [data-cell]").forEach(td => td.onclick = (ev) => {
      const sh = ev.target.closest("[data-shift]");
      const [uid, day] = td.dataset.cell.split("|");
      openCell(Number(uid), day, sh ? Number(sh.dataset.shift) : null);
    });
  }
}

// --- Zelle bearbeiten ---
function openCell(uid, day, shiftId) {
  const d = RS.data;
  const u = d.users.find(x => x.id === uid);
  const s = shiftId ? d.shifts.find(x => x.id === shiftId) : null;
  const existing = d.shifts.filter(x => x.user_id === uid && x.day === day);
  RS.cell = { uid, day, shift: s };
  const dt = new Date(day + "T12:00:00");
  const typeBtn = (t) => `<button class="rtype ${s && s.shift_type_id === t.id ? "on" : ""}" data-type="${t.id}" style="--c:${esc(t.color)}">
    <b>${esc(t.short)}</b><span>${esc(t.name)}</span><small>${t.kind === "work" ? `${t.start_time}–${t.end_time}` : "ganzer Tag"}</small></button>`;
  const work = d.types.filter(t => t.kind === "work"), off = d.types.filter(t => t.kind === "off");
  $("rsForm").innerHTML = `<div class="toolbar"><h2>${esc(u.display_name)} · ${RS_WD[(dt.getDay() + 6) % 7]} ${dt.getDate()}.${dt.getMonth() + 1}.</h2>
      <button class="mini" id="rsClose">✕</button></div>
    ${s ? `<p class="muted small" style="margin:0 0 8px">Eingetragen: ${shiftPill(s, false)} – andere Schichtart wählen oder Zeiten anpassen.</p>`
        : existing.length ? `<p class="muted small" style="margin:0 0 8px">Schon eingetragen: ${existing.map(x => shiftPill(x, false)).join(" ")} – eine weitere Schicht kommt dazu (geteilter Dienst).</p>` : ""}
    <h4 class="rh">Dienst</h4><div class="rtypes">${work.map(typeBtn).join("")}</div>
    <h4 class="rh">Abwesend</h4><div class="rtypes">${off.map(typeBtn).join("")}</div>
    <details class="more" id="rsCustomBox" ${s && s.kind === "work" ? "open" : ""}><summary>Eigene Zeiten${s ? " / Notiz" : ""}</summary>
      <div class="row"><label>von <input type="time" id="rsFrom" step="300" value="${s && s.start_time ? s.start_time : ""}"></label>
        <label>bis <input type="time" id="rsTo" step="300" value="${s && s.end_time ? s.end_time : ""}"></label>
        <label>Pause (Min.) <input type="number" id="rsBreak" class="w80" min="0" max="240" step="5" value="${s ? s.break_minutes : 0}"></label></div>
      <label style="margin-top:8px">Notiz (z. B. Station, Filiale) <input id="rsNote" maxlength="200" value="${esc(s ? s.note : "")}"></label>
      <div class="row" style="margin-top:8px"><button class="primary" id="rsSaveCustom">${s ? "Speichern" : "Mit eigenen Zeiten eintragen"}</button></div>
    </details>
    <div class="row" style="margin-top:12px">${s ? '<button class="danger" id="rsDel">Schicht entfernen</button>' : ""}
      ${!s && existing.length ? '<button class="danger" id="rsDelAll">Tag leeren</button>' : ""}<button id="rsCancel">Schließen</button></div>
    <div class="err" id="rsFormErr"></div>`;
  $("rsModal").classList.remove("hidden");
  $("rsClose").onclick = $("rsCancel").onclick = closeCell;
  $$("#rsForm [data-type]").forEach(b => b.onclick = () => pickType(Number(b.dataset.type)));
  $("rsSaveCustom").onclick = saveCustom;
  if ($("rsDel")) $("rsDel").onclick = () => removeShifts([s.id]);
  if ($("rsDelAll")) $("rsDelAll").onclick = () => removeShifts(existing.map(x => x.id));
}
function closeCell() { $("rsModal").classList.add("hidden"); $("rsForm").innerHTML = ""; RS.cell = null; }

async function cellCall(fn) {
  try { await fn(); closeCell(); await loadRoster(); }
  catch (e) { $("rsFormErr").textContent = e.message; }
}
function pickType(typeId) {
  const c = RS.cell;
  const t = RS.data.types.find(x => x.id === typeId);
  const note = $("rsNote").value.trim();
  if (c.shift) return cellCall(() => api(`shifts/${c.shift.id}`, { method: "PATCH", body: { shift_type_id: typeId, note } }));
  // Abwesenheit ersetzt, was an dem Tag steht; ein Dienst kommt dazu – außer es war eine Abwesenheit eingetragen
  const day = RS.data.shifts.filter(x => x.user_id === c.uid && x.day === c.day);
  const replace = t.kind === "off" || day.some(x => x.kind === "off");
  if (replace && day.length && !confirm("An diesem Tag ist schon etwas eingetragen. Ersetzen?")) return;
  return cellCall(() => api("shifts", { method: "POST", body: { user_id: c.uid, day: c.day, shift_type_id: typeId, note, replace } }));
}
function saveCustom() {
  const c = RS.cell;
  const body = { start_time: $("rsFrom").value || null, end_time: $("rsTo").value || null,
                 break_minutes: Number($("rsBreak").value || 0), note: $("rsNote").value.trim() };
  if (c.shift) {
    if (c.shift.kind === "off") { delete body.start_time; delete body.end_time; delete body.break_minutes; }
    return cellCall(() => api(`shifts/${c.shift.id}`, { method: "PATCH", body }));
  }
  if (!body.start_time || !body.end_time) { $("rsFormErr").textContent = "Bitte Beginn und Ende angeben"; return; }
  return cellCall(() => api("shifts", { method: "POST", body: { user_id: c.uid, day: c.day, ...body } }));
}
function removeShifts(ids) {
  return cellCall(async () => { for (const id of ids) await api(`shifts/${id}`, { method: "DELETE" }); });
}

// --- Woche ---
function rsMove(n) { RS.anchor = cAdd(cMonday(RS.anchor), n * 7); loadRoster(); }
async function copyPrev() {
  const ws = cMonday(RS.anchor);
  const has = RS.data && RS.data.shifts.length;
  if (has && !confirm("Diese Woche ist schon geplant. Alles durch die Vorwoche ersetzen?")) return;
  try {
    const r = await api("roster/copy", { method: "POST", body: { from_week: cIso(cAdd(ws, -7)), to_week: cIso(ws), replace: true } });
    await loadRoster();
    okMsg($("rsErr"), r.copied ? `${r.copied} Schichten aus der Vorwoche übernommen` + (r.skipped ? ` (${r.skipped} übersprungen)` : "") : "Die Vorwoche ist leer.");
  } catch (e) { $("rsErr").textContent = e.message; }
}
async function publishWeek() {
  const notify = isTeam() && confirm("Woche veröffentlichen?\n\nOK = Mitarbeiter bekommen ihre Schichten per Mail\nAbbrechen = dann frage ich, ob ohne Mail");
  if (!notify && isTeam() && !confirm("Ohne Mail veröffentlichen?")) return;
  try {
    const r = await api("roster/publish", { method: "POST", body: { week: cIso(cMonday(RS.anchor)), notify } });
    await loadRoster();
    okMsg($("rsErr"), "Veröffentlicht" + (notify ? ` – ${r.sent} Mail${r.sent === 1 ? "" : "s"} verschickt` : ""));
    if (r.failed && r.failed.length) $("rsErr").textContent = "Nicht zugestellt: " + r.failed.join("; ");
  } catch (e) { $("rsErr").textContent = e.message; }
}
async function unpublishWeek() {
  if (!confirm("Veröffentlichung zurücknehmen? Mitarbeiter sehen die Woche dann nicht mehr.")) return;
  try { await api("roster/unpublish", { method: "POST", body: { week: cIso(cMonday(RS.anchor)) } }); await loadRoster(); }
  catch (e) { $("rsErr").textContent = e.message; }
}

// --- Verwaltung → Dienstplan ---
async function loadShiftAdmin() {
  $("stErr").textContent = "";
  const types = await api("shift-types?include_archived=true");
  $("stTable").innerHTML = `<tr><th>Farbe</th><th>Kürzel</th><th>Name</th><th>Art</th><th>von</th><th>bis</th><th>Pause</th><th>Std.</th><th></th></tr>` +
    types.map(t => `<tr class="${t.archived ? "muted" : ""}" data-st="${t.id}">
      <td><input type="color" data-f="color" value="${esc(t.color)}" ${t.archived ? "disabled" : ""}></td>
      <td><input data-f="short" class="w50" maxlength="3" value="${esc(t.short)}" ${t.archived ? "disabled" : ""}></td>
      <td><input data-f="name" maxlength="40" value="${esc(t.name)}" ${t.archived ? "disabled" : ""}></td>
      <td>${t.kind === "work" ? "Dienst" : "Abwesend"}</td>
      ${t.kind === "work" ? `<td><input type="time" data-f="start_time" value="${t.start_time}" step="300" ${t.archived ? "disabled" : ""}></td>
        <td><input type="time" data-f="end_time" value="${t.end_time}" step="300" ${t.archived ? "disabled" : ""}></td>
        <td><input type="number" data-f="break_minutes" class="w60" min="0" max="240" step="5" value="${t.break_minutes}" ${t.archived ? "disabled" : ""}></td>
        <td>${hm(t.minutes)}</td>` : '<td colspan="4" class="muted small">ganzer Tag, zählt nicht als Arbeitszeit</td>'}
      <td>${t.archived ? '<button class="mini" data-arch="0">Zurückholen</button>' : '<button class="mini" data-arch="1">Archivieren</button>'}</td></tr>`).join("");
  $$("#stTable [data-st]").forEach(tr => {
    const id = tr.dataset.st;
    $$("[data-f]", tr).forEach(inp => inp.onchange = async () => {
      const v = inp.type === "number" ? Number(inp.value) : inp.value;
      try { await api(`shift-types/${id}`, { method: "PATCH", body: { [inp.dataset.f]: v } }); okMsg($("stErr"), "Gespeichert – gilt für neu eingetragene Schichten."); loadShiftAdmin(); }
      catch (e) { $("stErr").textContent = e.message; }
    });
    tr.querySelector("[data-arch]").onclick = async () => {
      try { await api(`shift-types/${id}`, { method: "PATCH", body: { archived: tr.querySelector("[data-arch]").dataset.arch === "1" } }); loadShiftAdmin(); }
      catch (e) { $("stErr").textContent = e.message; }
    };
  });
  if (isTeam()) {
    $("stPeople").innerHTML = state.users.filter(u => u.active).map(u =>
      `<label class="inline"><input type="checkbox" data-inr="${u.id}" ${u.in_roster ? "checked" : ""}> ${esc(u.display_name)}${u.is_admin ? ' <span class="tag boss">Chef</span>' : ""}</label>`).join("");
    $$("#stPeople [data-inr]").forEach(cb => cb.onchange = async () => {
      try { await api(`roster/users/${cb.dataset.inr}`, { method: "PATCH", body: { in_roster: cb.checked } }); okMsg($("stErr"), "Gespeichert.");
            const u = state.users.find(x => x.id === Number(cb.dataset.inr)); if (u) u.in_roster = cb.checked; }
      catch (e) { $("stErr").textContent = e.message; }
    });
  }
}
async function addShiftType() {
  const kind = $("ntKind").value;
  const body = { name: $("ntName").value.trim(), short: $("ntShort").value.trim(), kind, color: $("ntColor").value };
  if (kind === "work") Object.assign(body, { start_time: $("ntFrom").value || null, end_time: $("ntTo").value || null, break_minutes: Number($("ntBreak").value || 0) });
  if (!body.name || !body.short) { $("stErr").textContent = "Bitte Name und Kürzel angeben"; return; }
  try {
    await api("shift-types", { method: "POST", body });
    ["ntName", "ntShort", "ntFrom", "ntTo"].forEach(id => { $(id).value = ""; });
    await loadShiftAdmin(); okMsg($("stErr"), "Schichtart angelegt.");
  } catch (e) { $("stErr").textContent = e.message; }
}

$("rsPrev").onclick = () => rsMove(-1);
$("rsNext").onclick = () => rsMove(1);
$("rsToday").onclick = () => { RS.anchor = new Date(); loadRoster(); };
$("rsCopy").onclick = copyPrev;
$("rsPublish").onclick = publishWeek;
$("rsUnpublish").onclick = unpublishWeek;
$("rsPdf").onclick = () => downloadFile(`api/v1/roster/pdf?week=${cIso(cMonday(RS.anchor))}`, "dienstplan.pdf").catch(e => { $("rsErr").textContent = e.message; });
$("rsModal").addEventListener("click", (e) => { if (e.target.id === "rsModal") closeCell(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && RS.cell) closeCell(); });
$("ntKind").onchange = () => $$(".ntWork").forEach(el => el.classList.toggle("hidden", $("ntKind").value !== "work"));
$("ntAdd").onclick = addShiftType;

window.RS = RS;
window.loadRoster = loadRoster;
window.loadShiftAdmin = loadShiftAdmin;
