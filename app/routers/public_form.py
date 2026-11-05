"""Öffentliches Meldeformular (ohne Anmeldung).

Jedes Projekt kann einen eigenen, geheimen Link bekommen (/melden/<token>). Wer ihn kennt, kann dort ein Problem
melden; das Ticket landet direkt im Projekt.

Schutz:
- Link nur gültig, solange Helpdesk-Modul und Formular aktiv sind (sonst 404 – verrät nichts).
- Höchstens MAX_PER_IP Meldungen pro Absenderadresse und MAX_PER_FORM pro Formular in 15 Minuten.
- Verstecktes Feld gegen Spam-Programme (wird es ausgefüllt, tun wir so, als wäre alles gut).
- Anhänge werden danach einzeln hochgeladen – nur mit dem Schlüssel aus der Antwort, nur 30 Minuten lang,
  höchstens 5 Dateien, Typ wird am Inhalt geprüft.
"""
import sqlite3
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import __version__
from .. import helpdesk as hd
from .. import modules
from ..db import audit, get_db, get_setting
from ..dsm_auth import note_failure, too_many_attempts
from ..mailer import valid_email
from ..timeutil import now_utc_str
from .auth import client_ip
from .tickets import read_limited

router = APIRouter(tags=["Meldeformular (öffentlich)"])

MAX_PER_IP = 5
MAX_PER_FORM = 20
STATIC = Path(__file__).resolve().parent.parent / "static"


def _form(conn, token: str):
    if not token or len(token) > 64 or not modules.enabled(conn, "helpdesk"):
        raise HTTPException(404, "Dieses Formular gibt es nicht (mehr).")
    row = conn.execute(
        "SELECT f.*, p.name AS project, p.archived, c.name AS customer FROM ticket_forms f JOIN projects p ON p.id = f.project_id "
        "LEFT JOIN customers c ON c.id = p.customer_id WHERE f.token = ?", (token,)).fetchone()
    if not row or not row["active"] or row["archived"]:
        raise HTTPException(404, "Dieses Formular gibt es nicht (mehr).")
    return row


@router.get("/melden/{token}", include_in_schema=False)
def page(token: str, conn: sqlite3.Connection = Depends(get_db)):
    try:
        _form(conn, token)
    except HTTPException:
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>Nicht gefunden</title>"
                            "<p style='font:16px system-ui;margin:3em;text-align:center'>Dieses Formular gibt es nicht (mehr).</p>",
                            status_code=404)
    html = (STATIC / "melden.html").read_text(encoding="utf-8").replace("{{v}}", __version__)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


@router.get("/api/v1/public/form/{token}")
def form_info(token: str, conn: sqlite3.Connection = Depends(get_db)):
    f = _form(conn, token)
    return {"company": get_setting(conn, "company_name"), "customer": f["customer"], "project": f["project"],
            "hint": get_setting(conn, "hd_form_hint"), "privacy_url": get_setting(conn, "hd_privacy_url"),
            "attachments": get_setting(conn, "hd_attachments") == "1",
            "max_files": hd.MAX_FILES, "max_mb": hd.MAX_FILE_BYTES // 1048576}


class ReportIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=200)
    phone: str = Field("", max_length=50)
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=20000)
    urgent: bool = False
    privacy: bool = Field(False, description="Datenschutzhinweise gelesen (nur nötig, wenn ein Link eingestellt ist)")
    website: str = Field("", max_length=200, description="verstecktes Feld – muss leer bleiben")


@router.post("/api/v1/public/form/{token}", status_code=201)
def submit(token: str, data: ReportIn, request: Request, background: BackgroundTasks, conn: sqlite3.Connection = Depends(get_db)):
    f = _form(conn, token)
    ip_key, form_key = "ticket-ip:" + client_ip(request), "ticket-form:" + token
    if too_many_attempts(ip_key, MAX_PER_IP) or too_many_attempts(form_key, MAX_PER_FORM):
        raise HTTPException(429, "Zu viele Meldungen in kurzer Zeit – bitte in ein paar Minuten noch einmal versuchen.")
    if data.website.strip():  # Spam-Programm: freundlich tun, nichts speichern
        note_failure(ip_key)
        return {"number": "T-0000-0000", "upload_key": None}
    email = data.email.strip()
    if not valid_email(email):
        raise HTTPException(400, "Bitte eine gültige E-Mail-Adresse angeben.")
    if get_setting(conn, "hd_privacy_url") and not data.privacy:
        raise HTTPException(400, "Bitte bestätige, dass du die Datenschutzhinweise gelesen hast.")
    note_failure(ip_key)
    note_failure(form_key)
    with_files = get_setting(conn, "hd_attachments") == "1"
    t = hd.create_ticket(conn, project_id=f["project_id"], title=data.title.strip(), body=data.body, priority=1 if data.urgent else 3,
                         source="form", requester_name=data.name.strip(), requester_email=email, requester_phone=data.phone.strip(),
                         assignee_id=f["assignee_id"], with_upload_key=with_files)
    nr = hd.number(t["year"], t["seq"])
    audit(conn, None, "ticket_created", f"ticket:{t['id']}", f"{nr} · Formular · {data.name.strip()} <{email}>")
    background.add_task(hd.mail_created, t["id"])
    return {"number": nr, "upload_key": t["upload_key"]}


@router.post("/api/v1/public/upload/{key}", status_code=201)
async def upload(key: str, request: Request, name: str = Query("datei", max_length=200), conn: sqlite3.Connection = Depends(get_db)):
    """Anhang zu einer gerade abgeschickten Meldung (Body = Dateiinhalt)."""
    if not key or len(key) > 64 or not modules.enabled(conn, "helpdesk") or get_setting(conn, "hd_attachments") != "1":
        raise HTTPException(404, "Nicht gefunden")
    t = conn.execute("SELECT * FROM tickets WHERE upload_key = ?", (key,)).fetchone()
    if not t or hd.is_expired(t["upload_until"]):
        raise HTTPException(404, "Das Hochladen ist für diese Meldung nicht mehr möglich")
    data = await read_limited(request, hd.MAX_FILE_BYTES)
    first = conn.execute("SELECT id FROM ticket_messages WHERE ticket_id = ? AND kind = 'requester' ORDER BY id LIMIT 1",
                         (t["id"],)).fetchone()
    try:
        f = hd.store_file(conn, t["id"], name, data, None, message_id=first[0] if first else None)
    except hd.FileRejected as e:
        raise HTTPException(400, str(e))
    conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now_utc_str(), t["id"]))
    return {"name": f["name"], "size": f["size"]}
