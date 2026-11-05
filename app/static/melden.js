"use strict";
// Öffentliches Meldeformular – ohne Anmeldung. Erst die Meldung schicken, dann die Dateien einzeln hochladen.
(function () {
  const $ = (id) => document.getElementById(id);
  const token = decodeURIComponent(location.pathname.replace(/\/+$/, "").split("/").pop() || "");
  const API = "../api/v1/public/";
  let info = null, files = [];

  const fmtSize = (b) => b > 1048576 ? (b / 1048576).toFixed(1).replace(".", ",") + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function call(path, opts) {
    const res = await fetch(path, { cache: "no-store", credentials: "omit", ...opts });
    let data = null;
    try { data = await res.json(); } catch (e) { /* leer */ }
    if (!res.ok) {
      const d = data && data.detail;
      const err = new Error(typeof d === "string" ? d : Array.isArray(d) ? "Bitte alle Pflichtfelder ausfüllen." : "Fehler " + res.status);
      err.status = res.status; throw err;
    }
    return data;
  }

  function initials(name) { return (name || "IT").split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join("").toUpperCase(); }

  async function load() {
    try { info = await call(API + "form/" + encodeURIComponent(token)); }
    catch (e) { $("pForm").classList.add("hidden"); $("pGone").classList.remove("hidden"); return; }
    const company = info.company || "Support";
    $("pCompany").textContent = company;
    $("pLogoTxt").textContent = initials(company);
    $("pFor").textContent = "Meldung für " + (info.customer ? info.customer + " · " : "") + info.project;
    $("pFoot").textContent = company;
    document.title = "Problem melden – " + company;
    try {
      const b = await (await fetch("../api/v1/branding", { cache: "no-store" })).json();
      if (b && b.logo) { $("pLogo").src = "../api/v1/branding/logo?v=" + b.logo.version; $("pLogo").classList.remove("hidden"); $("pLogoTxt").classList.add("hidden"); }
    } catch (e) { /* ohne Logo */ }
    if (info.categories && info.categories.length) {
      $("fCat").innerHTML = '<option value="">Weiß ich nicht</option>' + info.categories.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
      $("fCatWrap").classList.remove("hidden");
    }
    if (info.hint) { $("fHintText").textContent = info.hint; $("fHint").classList.remove("hidden"); }
    if (info.privacy_url) { $("fPrivacyLink").href = info.privacy_url; $("fPrivacyWrap").classList.remove("hidden"); }
    if (info.attachments) {
      $("fFilesBox").classList.remove("hidden");
      $("fFilesInfo").textContent = `PNG, JPG oder PDF · bis ${info.max_mb} MB pro Datei · höchstens ${info.max_files}`;
    }
  }

  function addFiles(list) {
    $("fErr").textContent = "";
    for (const f of list) {
      if (files.length >= info.max_files) { $("fErr").textContent = `Höchstens ${info.max_files} Dateien.`; break; }
      if (!/^(image\/png|image\/jpeg|application\/pdf)$/.test(f.type) && !/\.(png|jpe?g|pdf)$/i.test(f.name)) {
        $("fErr").textContent = `„${f.name}“: nur PNG, JPG oder PDF.`; continue;
      }
      if (f.size > info.max_mb * 1048576) { $("fErr").textContent = `„${f.name}“ ist zu groß (höchstens ${info.max_mb} MB).`; continue; }
      files.push(f);
    }
    renderFiles();
  }
  function renderFiles() {
    $("fList").innerHTML = files.map((f, i) => `<div class="file"><span class="th ${/pdf$/i.test(f.type) || /\.pdf$/i.test(f.name) ? "pdf" : "img"}">${/pdf/i.test(f.type) ? "PDF" : "🖼"}</span>
      <span>${esc(f.name)}<small>${fmtSize(f.size)}</small></span><button type="button" data-i="${i}" aria-label="entfernen">✕</button></div>`).join("");
    $("fList").querySelectorAll("button").forEach(b => b.onclick = () => { files.splice(+b.dataset.i, 1); renderFiles(); });
  }

  async function send(ev) {
    ev.preventDefault();
    $("fErr").textContent = "";
    const body = {
      name: $("fName").value.trim(), email: $("fMail").value.trim(), phone: $("fPhone").value.trim(),
      title: $("fTitle").value.trim(), body: $("fBody").value.trim(),
      urgent: document.querySelector('input[name="urg"]:checked').value === "1",
      privacy: $("fPrivacy").checked, website: $("fWebsite").value,
      category_id: $("fCat").value ? Number($("fCat").value) : null,
    };
    if (!body.name || !body.email || !body.title || !body.body) { $("fErr").textContent = "Bitte alle Felder mit * ausfüllen."; return; }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(body.email)) { $("fErr").textContent = "Bitte eine gültige E-Mail-Adresse angeben."; return; }
    if (info.privacy_url && !body.privacy) { $("fErr").textContent = "Bitte bestätige die Datenschutzhinweise."; return; }
    $("fSend").disabled = true; $("fSend").textContent = "Wird gesendet …";
    try {
      const r = await call(API + "form/" + encodeURIComponent(token), {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const errs = [];
      if (r.upload_key) {
        for (let i = 0; i < files.length; i++) {
          $("fSend").textContent = `Datei ${i + 1} von ${files.length} …`;
          try {
            await call(API + "upload/" + encodeURIComponent(r.upload_key) + "?name=" + encodeURIComponent(files[i].name), {
              method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: files[i] });
          } catch (e) { errs.push(`${files[i].name}: ${e.message}`); }
        }
      }
      $("pNr").textContent = r.number;
      $("pUpErr").textContent = errs.length ? "Nicht angehängt – " + errs.join(" · ") : "";
      $("pForm").classList.add("hidden"); $("pDone").classList.remove("hidden");
      window.scrollTo(0, 0);
    } catch (e) {
      $("fErr").textContent = e.message;
    } finally {
      $("fSend").disabled = false; $("fSend").textContent = "Meldung absenden";
    }
  }

  document.querySelectorAll('input[name="urg"]').forEach(r => r.onchange = () =>
    document.querySelectorAll(".urg .modeopt").forEach(l => l.classList.toggle("on", l.querySelector("input").checked)));
  $("fFiles").onchange = () => { addFiles(Array.from($("fFiles").files)); $("fFiles").value = ""; };
  const drop = $("fDrop");
  ["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => addFiles(Array.from(e.dataTransfer.files || [])));
  $("pForm").addEventListener("submit", send);
  $("pAgain").onclick = () => {
    ["fTitle", "fBody"].forEach(id => { $(id).value = ""; });
    files = []; renderFiles();
    $("pDone").classList.add("hidden"); $("pForm").classList.remove("hidden");
  };
  load();
})();
