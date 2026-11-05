"use strict";
// Melder-Portal: Anmelden per Link, Meine Tickets, Ticket mit Antworten, neues Problem melden.
// Statuslink (…/portal/t/<token>): nur dieses eine Ticket, ohne Anmeldung.
(function () {
  const $ = (id) => document.getElementById(id);
  const P = window.ZM_P || "";
  const API = P + "api/v1/portal/";
  const linkMatch = location.pathname.match(/\/portal\/t\/([0-9]+-[0-9a-f]{32})\/?$/);
  const LINK = linkMatch ? linkMatch[1] : null;
  const S = { me: null, ticket: null, files: { n: [], r: [] } };
  const STATUS = { new: "Neu", open: "In Arbeit", wait: "Wartet auf dich", done: "Erledigt" };

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDT = (iso) => iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "";
  const fmtSize = (b) => b > 1048576 ? (b / 1048576).toFixed(1).replace(".", ",") + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
  const initials = (n) => (n || "S").split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join("").toUpperCase();

  async function call(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (LINK) headers["X-Portal-Link"] = LINK;
    if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
    const res = await fetch(API + path, { method: opts.method || "GET", headers, body: opts.body, credentials: "same-origin", cache: "no-store" });
    let data = null;
    try { data = await res.json(); } catch (e) { /* leer */ }
    if (!res.ok) {
      const d = data && data.detail;
      const err = new Error(typeof d === "string" ? d : Array.isArray(d) ? "Bitte alle Pflichtfelder ausfüllen." : "Fehler " + res.status);
      err.status = res.status; throw err;
    }
    return data;
  }

  function show(id) {
    ["vLogin", "vList", "vNew", "vTicket", "vMsg"].forEach(v => $(v).classList.toggle("hidden", v !== id));
    window.scrollTo(0, 0);
  }
  function message(icon, title, text, btn) {
    $("mIcon").textContent = icon; $("mTitle").textContent = title; $("mText").textContent = text;
    $("mBtn").classList.toggle("hidden", !btn);
    if (btn) { $("mBtn").textContent = btn[0]; $("mBtn").onclick = btn[1]; }
    show("vMsg");
  }

  async function branding() {
    try {
      const b = await (await fetch(P + "api/v1/branding", { cache: "no-store" })).json();
      const company = b.company_name || "Support";
      $("pCompany").textContent = company; $("pLogoTxt").textContent = initials(company); $("pFoot").textContent = company;
      document.title = "Support-Portal – " + company;
      if (b.logo) { $("pLogo").src = P + "api/v1/branding/logo?v=" + b.logo.version; $("pLogo").classList.remove("hidden"); $("pLogoTxt").classList.add("hidden"); }
    } catch (e) { $("pLogoTxt").textContent = "S"; }
  }

  // ---------- Dateien ----------
  function renderFiles(kind) {
    const box = document.querySelector(`[data-files="${kind}"]`);
    if (!S.me || !S.me.attachments) { box.innerHTML = ""; return; }
    box.innerHTML = `<label class="btn mini" style="cursor:pointer;display:inline-block">📎 Bildschirmfoto oder PDF anhängen
      <input type="file" multiple accept="image/png,image/jpeg,application/pdf" class="hidden"></label>
      <div class="flist">${S.files[kind].map((f, i) => `<div class="file"><span class="th ${/pdf/i.test(f.type) ? "pdf" : ""}">${/pdf/i.test(f.type) ? "PDF" : "🖼"}</span>
        <span>${esc(f.name)}<small>${fmtSize(f.size)}</small></span><button type="button" data-i="${i}">✕</button></div>`).join("")}</div>`;
    box.querySelector("input").onchange = (ev) => {
      for (const f of ev.target.files) {
        if (S.files[kind].length >= S.me.max_files) break;
        if (f.size > S.me.max_mb * 1048576) { alert(`„${f.name}“ ist zu groß (höchstens ${S.me.max_mb} MB).`); continue; }
        S.files[kind].push(f);
      }
      renderFiles(kind);
    };
    box.querySelectorAll("[data-i]").forEach(b => b.onclick = () => { S.files[kind].splice(+b.dataset.i, 1); renderFiles(kind); });
  }
  async function uploadFiles(kind, ticketId, messageId) {
    const errs = [];
    for (const f of S.files[kind]) {
      try { await call(`tickets/${ticketId}/messages/${messageId}/files?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f, headers: { "Content-Type": "application/octet-stream" } }); }
      catch (e) { errs.push(`${f.name}: ${e.message}`); }
    }
    S.files[kind] = [];
    return errs;
  }

  // ---------- Liste ----------
  async function showList() {
    if (LINK) return openTicket(S.me.ticket_id);
    $("listErr").textContent = "";
    show("vList");
    try {
      const list = await call("tickets");
      $("lList").innerHTML = list.length ? list.map(t => `<a class="pticket" href="#t${t.id}" data-open="${t.id}">
        <div><b>${esc(t.title)}</b><div class="meta"><span class="tnr">${esc(t.number)}</span><span>${esc(t.project)}</span><span>aktualisiert ${fmtDT(t.updated_at)}</span></div></div>
        <span class="st ${t.status}">${STATUS[t.status]}</span></a>`).join("") :
        '<div class="emptylist">Noch keine Tickets.</div>';
      document.querySelectorAll("[data-open]").forEach(a => a.onclick = (ev) => { ev.preventDefault(); openTicket(+a.dataset.open); });
    } catch (e) { $("listErr").textContent = e.message; }
  }

  // ---------- Ticket ----------
  async function openTicket(id) {
    $("tErr").textContent = "";
    try { S.ticket = await call("tickets/" + id); }
    catch (e) { return message("⚠️", "Ticket nicht gefunden", e.message, LINK ? null : ["Zu meinen Tickets", showList]); }
    renderTicket(); show("vTicket");
    if (!LINK) history.replaceState(null, "", "#t" + id);
  }
  function renderTicket() {
    const t = S.ticket;
    $("tBack").classList.toggle("hidden", !!LINK);
    $("tNr").textContent = t.number; $("tSt").textContent = STATUS[t.status]; $("tSt").className = "st " + t.status;
    $("tTitle").textContent = t.title;
    $("tMsgs").innerHTML = t.messages.map(m => `<div class="pmsg ${m.from_me ? "me" : "them"}">
      <div class="who"><b>${m.from_me ? "Du" : esc(m.author || "Support")}</b><span>${fmtDT(m.created_at)}</span></div>
      <p>${esc(m.body)}</p>
      ${m.files.length ? `<div class="files">${m.files.map(f => `<a class="file" href="#" data-f="${f.id}" data-n="${esc(f.name)}"><span class="th ${f.mime === "application/pdf" ? "pdf" : ""}">${f.mime === "application/pdf" ? "PDF" : "🖼"}</span>
        <span>${esc(f.name)}<small>${fmtSize(f.size)}</small></span></a>`).join("")}</div>` : ""}</div>`).join("");
    $("tActions").innerHTML = t.status === "done"
      ? `<span class="muted small">Erledigt am ${fmtDT(t.done_at)}.</span><button id="aReopen">Problem besteht noch</button>`
      : `<button id="aResolve">✓ Problem gelöst</button><span class="muted small">Damit schließt du das Ticket.</span>`;
    if ($("aReopen")) $("aReopen").onclick = () => act("reopen");
    if ($("aResolve")) $("aResolve").onclick = () => { if (confirm("Ist das Problem gelöst? Das Ticket wird dann geschlossen.")) act("resolve"); };
    document.querySelectorAll("[data-f]").forEach(a => a.onclick = (ev) => { ev.preventDefault(); download(a.dataset.f, a.dataset.n); });
    renderFiles("r");
  }
  async function act(what) {
    try { S.ticket = await call(`tickets/${S.ticket.id}/${what}`, { method: "POST" }); renderTicket(); }
    catch (e) { $("tErr").textContent = e.message; }
  }
  async function download(fid, name) {
    try {
      const res = await fetch(API + `tickets/${S.ticket.id}/files/${fid}`, { headers: LINK ? { "X-Portal-Link": LINK } : {}, credentials: "same-origin" });
      if (!res.ok) throw new Error("Download fehlgeschlagen");
      const u = URL.createObjectURL(await res.blob());
      const a = document.createElement("a"); a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(u), 4000);
    } catch (e) { $("tErr").textContent = e.message; }
  }
  $("rSend").onclick = async () => {
    const body = $("rText").value.trim();
    $("tErr").textContent = "";
    if (!body) { $("tErr").textContent = "Bitte eine Nachricht schreiben."; return; }
    $("rSend").disabled = true;
    try {
      const r = await call(`tickets/${S.ticket.id}/messages`, { method: "POST", json: { body } });
      const errs = await uploadFiles("r", S.ticket.id, r.message_id);
      $("rText").value = "";
      S.ticket = await call("tickets/" + S.ticket.id); renderTicket();
      if (errs.length) $("tErr").textContent = "Nicht angehängt – " + errs.join(" · ");
    } catch (e) { $("tErr").textContent = e.message; }
    $("rSend").disabled = false;
  };

  // ---------- Neu ----------
  function showNew() {
    const pr = S.me.projects;
    $("nProjWrap").classList.toggle("hidden", pr.length < 2);
    $("nProj").innerHTML = pr.map(p => `<option value="${p.id}">${esc((p.customer ? p.customer + " · " : "") + p.name)}</option>`).join("");
    const cats = S.me.categories || [];
    $("nCatWrap").classList.toggle("hidden", !cats.length);
    $("nCat").innerHTML = '<option value="">Weiß ich nicht</option>' + cats.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
    if (S.me.hint) { $("nHintText").textContent = S.me.hint; $("nHint").classList.remove("hidden"); }
    $("nErr").textContent = ""; renderFiles("n"); show("vNew"); $("nTitle").focus();
  }
  document.querySelectorAll('input[name="nurg"]').forEach(r => r.onchange = () =>
    document.querySelectorAll("#vNew .modeopt").forEach(l => l.classList.toggle("on", l.querySelector("input").checked)));
  $("nSend").onclick = async () => {
    const body = { title: $("nTitle").value.trim(), body: $("nBody").value.trim(),
                   urgent: document.querySelector('input[name="nurg"]:checked').value === "1" };
    if (S.me.projects.length > 1) body.project_id = Number($("nProj").value);
    if ($("nCat").value) body.category_id = Number($("nCat").value);
    if (!body.title || !body.body) { $("nErr").textContent = "Bitte Betreff und Beschreibung ausfüllen."; return; }
    $("nSend").disabled = true;
    try {
      const r = await call("tickets", { method: "POST", json: body });
      const errs = await uploadFiles("n", r.id, r.message_id);
      $("nTitle").value = ""; $("nBody").value = "";
      await openTicket(r.id);
      if (errs.length) $("tErr").textContent = "Nicht angehängt – " + errs.join(" · ");
    } catch (e) { $("nErr").textContent = e.message; }
    $("nSend").disabled = false;
  };

  // ---------- Anmeldung ----------
  $("lSend").onclick = async () => {
    const email = $("lMail").value.trim();
    $("lErr").textContent = "";
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { $("lErr").textContent = "Bitte eine gültige E-Mail-Adresse eingeben."; return; }
    $("lSend").disabled = true;
    try { const r = await call("login", { method: "POST", json: { email } }); message("✉️", "Schau in dein Postfach", r.message + " Der Link gilt 30 Minuten."); }
    catch (e) { $("lErr").textContent = e.message; }
    $("lSend").disabled = false;
  };
  $("lMail").addEventListener("keydown", (e) => { if (e.key === "Enter") $("lSend").click(); });
  $("pLogout").onclick = async () => { await call("logout", { method: "POST" }).catch(() => {}); location.hash = ""; location.reload(); };
  $("nBtn").onclick = showNew;
  document.querySelectorAll("[data-back]").forEach(a => a.onclick = (ev) => { ev.preventDefault(); history.replaceState(null, "", location.pathname); showList(); });

  async function start() {
    await branding();
    const m = location.hash.match(/^#login=([\w-]+)$/);
    if (m && !LINK) {
      history.replaceState(null, "", location.pathname);   // Schlüssel nicht im Verlauf lassen
      try { await call("verify", { method: "POST", json: { token: m[1] } }); }
      catch (e) { $("lErr").textContent = e.message; show("vLogin"); return; }
    }
    try { S.me = await call("me"); }
    catch (e) {
      if (LINK) return message("🔗", "Link ungültig", e.message);
      show("vLogin"); setTimeout(() => $("lMail").focus(), 0); return;
    }
    if (!LINK) { $("pWho").textContent = S.me.email; $("pLogout").classList.remove("hidden"); }
    const t = location.hash.match(/^#t(\d+)$/);
    if (LINK) openTicket(S.me.ticket_id); else if (t) openTicket(+t[1]); else showList();
  }
  start();
})();
