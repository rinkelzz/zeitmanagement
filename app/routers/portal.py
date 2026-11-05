"""Melder-Portal (ohne Mitarbeiter-Konto).

Zwei Arten, sich auszuweisen:
- Sitzung (Cookie zm_portal) nach Anmelde-Link per Mail → alle eigenen Tickets (requester_email = eigene Adresse),
  neue Tickets in den bekannten Projekten.
- Statuslink (Header X-Portal-Link) → genau dieses eine Ticket: lesen, antworten, Dateien anhängen, bestätigen.
Der Melder sieht nur seine Meldungen und die Antworten an ihn – keine internen Notizen, keine Systemeinträge, keine Zeiten.
Ist das Portal oder der Helpdesk aus, antwortet alles mit 404.
"""
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import __version__
from .. import helpdesk as hd
from .. import portal
from ..config import settings
from ..db import audit, get_db, get_setting
from ..dsm_auth import note_failure, too_many_attempts
from ..timeutil import local_iso, now_utc, now_utc_str, parse_utc
from .auth import client_ip
from .tickets import read_limited

router = APIRouter(tags=["Melder-Portal"])
STATIC = Path(__file__).resolve().parent.parent / "static"
REPLY_UPLOAD_MINUTES = 30


def _on(conn) -> None:
    if not portal.enabled(conn):
        raise HTTPException(404, "Nicht gefunden")


def _page(conn, prefix: str = "") -> HTMLResponse:
    if not portal.enabled(conn):
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>Nicht gefunden</title>"
                            "<p style='font:16px system-ui;margin:3em;text-align:center'>Das Portal ist nicht eingeschaltet.</p>",
                            status_code=404)
    html = (STATIC / "portal.html").read_text(encoding="utf-8").replace("{{v}}", __version__).replace("{{p}}", prefix)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


@router.get("/portal", include_in_schema=False)
def page(conn: sqlite3.Connection = Depends(get_db)):
    return _page(conn)


@router.get("/portal/t/{token}", include_in_schema=False)
def page_link(token: str, conn: sqlite3.Connection = Depends(get_db)):
    return _page(conn, "../../")


class Actor:
    def __init__(self, email: str, ticket_id: Optional[int] = None):
        self.email = email
        self.ticket_id = ticket_id   # gesetzt = nur über Statuslink, nur dieses Ticket


def actor(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Actor:
    _on(conn)
    link = request.headers.get(portal.LINK_HEADER)
    if link:
        t = portal.ticket_from_token(conn, link)
        if not t:
            raise HTTPException(401, "Dieser Link ist ungültig oder wurde erneuert – bitte den Link aus der neuesten Mail nehmen.")
        return Actor(t["requester_email"], t["id"])
    email = portal.session_email(conn, request.cookies.get(portal.COOKIE, ""))
    if not email:
        raise HTTPException(401, "Nicht angemeldet")
    return Actor(email)


def _ticket(conn, a: Actor, ticket_id: int):
    t = conn.execute("SELECT t.*, p.name AS project, c.name AS customer FROM tickets t JOIN projects p ON p.id = t.project_id "
                     "LEFT JOIN customers c ON c.id = p.customer_id WHERE t.id = ?", (ticket_id,)).fetchone()
    if (not t or (a.ticket_id is not None and a.ticket_id != ticket_id)
            or not t["requester_email"] or t["requester_email"].lower() != a.email.lower()):
        raise HTTPException(404, "Ticket nicht gefunden")
    return t


def _brief(t) -> dict:
    return {"id": t["id"], "number": hd.number(t["year"], t["seq"]), "title": t["title"], "status": t["status"],
            "status_label": hd.STATUSES[t["status"]], "project": t["project"], "customer": t["customer"],
            "created_at": local_iso(t["created_at"]), "updated_at": local_iso(t["updated_at"]), "done_at": local_iso(t["done_at"])}


def _detail(conn, t) -> dict:
    msgs = conn.execute("SELECT m.id, m.kind, m.author_name, m.body, m.created_at, u.display_name AS user_name FROM ticket_messages m "
                        "LEFT JOIN users u ON u.id = m.author_id WHERE m.ticket_id = ? AND m.kind IN ('requester', 'reply') ORDER BY m.id",
                        (t["id"],)).fetchall()
    ids = [m["id"] for m in msgs]
    files = conn.execute(f"SELECT id, message_id, name, mime, size FROM ticket_files WHERE ticket_id = ? AND message_id IN ({','.join('?' * len(ids))})",
                         [t["id"]] + ids).fetchall() if ids else []
    out = _brief(t)
    out["messages"] = [{"id": m["id"], "from_me": m["kind"] == "requester", "author": m["author_name"] if m["kind"] == "requester" else (m["user_name"] or m["author_name"]),
                        "body": m["body"], "created_at": local_iso(m["created_at"]),
                        "files": [{"id": f["id"], "name": f["name"], "mime": f["mime"], "size": f["size"]} for f in files if f["message_id"] == m["id"]]}
                       for m in msgs]
    out["attachments_allowed"] = get_setting(conn, "hd_attachments") == "1"
    out["max_files"], out["max_mb"] = hd.MAX_FILES, hd.MAX_FILE_BYTES // 1048576
    return out


# --- Anmeldung ------------------------------------------------------------------------

class LoginIn(BaseModel):
    email: str = Field(..., max_length=200)


@router.post("/api/v1/portal/login")
def login(data: LoginIn, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Anmelde-Link anfordern. Antwortet immer gleich – egal ob die Adresse bekannt ist."""
    _on(conn)
    email = data.email.strip().lower()
    ip_key, mail_key = "portal-ip:" + client_ip(request), "portal-mail:" + email
    if too_many_attempts(ip_key, 10) or too_many_attempts(mail_key, 3):
        raise HTTPException(429, "Zu viele Anfragen – bitte in ein paar Minuten noch einmal versuchen.")
    note_failure(ip_key)
    note_failure(mail_key)
    portal.request_login(conn, email)
    return {"ok": True, "message": "Wenn diese Adresse bei uns bekannt ist, kommt gleich eine Mail mit dem Anmelde-Link."}


class VerifyIn(BaseModel):
    token: str = Field(..., max_length=100)


@router.post("/api/v1/portal/verify")
def verify(data: VerifyIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    _on(conn)
    ip_key = "portal-verify:" + client_ip(request)
    if too_many_attempts(ip_key, 10):
        raise HTTPException(429, "Zu viele Versuche – bitte in ein paar Minuten noch einmal versuchen.")
    session = portal.redeem(conn, data.token)
    if not session:
        note_failure(ip_key)
        raise HTTPException(400, "Der Anmelde-Link ist abgelaufen oder wurde schon benutzt – bitte einen neuen anfordern.")
    https = settings.cookie_secure or request.url.scheme == "https"
    response.set_cookie(portal.COOKIE, session, httponly=True, samesite="lax", secure=https,
                        max_age=portal.SESSION_DAYS * 86400, path=settings.root_path or "/")
    email = portal.session_email(conn, session)
    audit(conn, None, "portal_login", "portal", email or "")
    return {"email": email}


@router.post("/api/v1/portal/logout")
def logout(request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    portal.logout(conn, request.cookies.get(portal.COOKIE, ""))
    response.delete_cookie(portal.COOKIE, path=settings.root_path or "/")
    return {"ok": True}


@router.get("/api/v1/portal/me")
def me(a: Actor = Depends(actor), conn: sqlite3.Connection = Depends(get_db)):
    return {"email": a.email, "link_only": a.ticket_id is not None, "ticket_id": a.ticket_id,
            "company": get_setting(conn, "company_name"),
            "projects": [] if a.ticket_id else portal.known_projects(conn, a.email),
            "hint": get_setting(conn, "hd_form_hint"), "attachments": get_setting(conn, "hd_attachments") == "1",
            "max_files": hd.MAX_FILES, "max_mb": hd.MAX_FILE_BYTES // 1048576}


# --- Tickets ---------------------------------------------------------------------------

def _session_only(a: Actor) -> None:
    if a.ticket_id is not None:
        raise HTTPException(403, "Mit dem Statuslink geht nur dieses eine Ticket – für alle Tickets bitte im Portal anmelden.")


@router.get("/api/v1/portal/tickets")
def my_tickets(a: Actor = Depends(actor), conn: sqlite3.Connection = Depends(get_db)):
    _session_only(a)
    rows = conn.execute("SELECT t.*, p.name AS project, c.name AS customer FROM tickets t JOIN projects p ON p.id = t.project_id "
                        "LEFT JOIN customers c ON c.id = p.customer_id WHERE t.requester_email = ? "
                        "ORDER BY t.status = 'done', t.updated_at DESC LIMIT 200", (a.email.lower(),)).fetchall()
    return [_brief(t) for t in rows]


@router.get("/api/v1/portal/tickets/{ticket_id}")
def my_ticket(ticket_id: int, a: Actor = Depends(actor), conn: sqlite3.Connection = Depends(get_db)):
    return _detail(conn, _ticket(conn, a, ticket_id))


class NewIn(BaseModel):
    project_id: Optional[int] = Field(None, description="nur nötig, wenn die Adresse in mehreren Projekten bekannt ist")
    name: str = Field("", max_length=100)
    phone: str = Field("", max_length=50)
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=20000)
    urgent: bool = False


def _upload_window(mid: int) -> dict:
    return {"message_id": mid, "upload_until": REPLY_UPLOAD_MINUTES}


@router.post("/api/v1/portal/tickets", status_code=201)
def new_ticket(data: NewIn, request: Request, background: BackgroundTasks, a: Actor = Depends(actor),
               conn: sqlite3.Connection = Depends(get_db)):
    _session_only(a)
    key = "portal-new:" + a.email
    if too_many_attempts(key, 5):
        raise HTTPException(429, "Zu viele Meldungen in kurzer Zeit – bitte in ein paar Minuten noch einmal versuchen.")
    projects = portal.known_projects(conn, a.email)
    if not projects:
        raise HTTPException(403, "Für diese Adresse ist kein Projekt hinterlegt")
    if data.project_id is None:
        if len(projects) > 1:
            raise HTTPException(400, "Bitte auswählen, wofür die Meldung ist")
        pid = projects[0]["id"]
    elif data.project_id in [p["id"] for p in projects]:
        pid = data.project_id
    else:
        raise HTTPException(400, "Für dieses Projekt darfst du nicht melden")
    note_failure(key)
    last = conn.execute("SELECT requester_name, requester_phone FROM tickets WHERE requester_email = ? ORDER BY id DESC LIMIT 1",
                        (a.email,)).fetchone()
    form = conn.execute("SELECT assignee_id FROM ticket_forms WHERE project_id = ?", (pid,)).fetchone()
    t = hd.create_ticket(conn, project_id=pid, title=data.title.strip(), body=data.body, priority=1 if data.urgent else 3,
                         source="portal", requester_name=data.name.strip() or (last["requester_name"] if last else "") or a.email.split("@")[0],
                         requester_email=a.email, requester_phone=data.phone.strip() or (last["requester_phone"] if last else ""),
                         assignee_id=form["assignee_id"] if form else None)
    nr = hd.number(t["year"], t["seq"])
    audit(conn, None, "ticket_created", f"ticket:{t['id']}", f"{nr} · Portal · {a.email}")
    first = conn.execute("SELECT id FROM ticket_messages WHERE ticket_id = ? ORDER BY id LIMIT 1", (t["id"],)).fetchone()
    background.add_task(hd.mail_created, t["id"])
    return {"id": t["id"], "number": nr, **_upload_window(first[0])}


class ReplyIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=20000)


@router.post("/api/v1/portal/tickets/{ticket_id}/messages", status_code=201)
def reply(ticket_id: int, data: ReplyIn, a: Actor = Depends(actor), conn: sqlite3.Connection = Depends(get_db)):
    t = _ticket(conn, a, ticket_id)
    key = "portal-reply:" + a.email
    if too_many_attempts(key, 20):
        raise HTTPException(429, "Zu viele Nachrichten in kurzer Zeit – bitte kurz warten.")
    note_failure(key)
    mid = hd.requester_reply(conn, t, data.body.strip(), t["requester_name"] or a.email, "im Portal")
    return {**_detail(conn, _ticket(conn, a, ticket_id)), **_upload_window(mid)}


@router.post("/api/v1/portal/tickets/{ticket_id}/messages/{message_id}/files", status_code=201)
async def upload(ticket_id: int, message_id: int, request: Request, name: str = Query("datei", max_length=200),
                 a: Actor = Depends(actor), conn: sqlite3.Connection = Depends(get_db)):
    """Datei zur eigenen, gerade geschriebenen Nachricht (Body = Dateiinhalt) – 30 Minuten lang, höchstens 5."""
    _ticket(conn, a, ticket_id)
    if get_setting(conn, "hd_attachments") != "1":
        raise HTTPException(403, "Anhänge sind abgeschaltet")
    m = conn.execute("SELECT * FROM ticket_messages WHERE id = ? AND ticket_id = ? AND kind = 'requester' AND author_id IS NULL",
                     (message_id, ticket_id)).fetchone()
    if not m or now_utc() - parse_utc(m["created_at"]) > timedelta(minutes=REPLY_UPLOAD_MINUTES):
        raise HTTPException(404, "Zu dieser Nachricht lassen sich keine Dateien mehr hochladen")
    if conn.execute("SELECT COUNT(*) FROM ticket_files WHERE message_id = ?", (message_id,)).fetchone()[0] >= hd.MAX_FILES:
        raise HTTPException(400, f"Höchstens {hd.MAX_FILES} Dateien pro Nachricht")
    data = await read_limited(request, hd.MAX_FILE_BYTES)
    mime = hd.sniff(data) if data else None
    if not data or not mime:
        raise HTTPException(400, "Nur Bilder (PNG, JPG) und PDF-Dateien sind erlaubt")
    clean = hd.clean_name(name, mime)
    conn.execute("INSERT INTO ticket_files(ticket_id, message_id, name, mime, size, data, uploaded_by, created_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                 (ticket_id, message_id, clean, mime, len(data), data, now_utc_str()))
    return {"name": clean, "size": len(data)}


@router.get("/api/v1/portal/tickets/{ticket_id}/files/{file_id}")
def download(ticket_id: int, file_id: int, a: Actor = Depends(actor), conn: sqlite3.Connection = Depends(get_db)):
    _ticket(conn, a, ticket_id)
    f = conn.execute("SELECT f.* FROM ticket_files f JOIN ticket_messages m ON m.id = f.message_id "
                     "WHERE f.id = ? AND f.ticket_id = ? AND m.kind IN ('requester', 'reply')", (file_id, ticket_id)).fetchone()
    if not f:
        raise HTTPException(404, "Datei nicht gefunden")
    safe = f["name"].encode("ascii", "replace").decode().replace('"', "")
    return Response(f["data"], media_type=f["mime"], headers={
        "Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox", "Cache-Control": "private, no-store"})


@router.post("/api/v1/portal/tickets/{ticket_id}/resolve")
def resolve(ticket_id: int, a: Actor = Depends(actor), conn: sqlite3.Connection = Depends(get_db)):
    """„Problem gelöst“ – der Melder bestätigt, das Ticket ist erledigt."""
    t = _ticket(conn, a, ticket_id)
    if t["status"] != "done":
        now = now_utc_str()
        conn.execute("UPDATE tickets SET status = 'done', done_at = ?, updated_at = ?, unread = 1 WHERE id = ?", (now, now, ticket_id))
        hd.system_message(conn, ticket_id, "Der Melder hat im Portal bestätigt: Problem gelöst – Ticket erledigt")
    return _detail(conn, _ticket(conn, a, ticket_id))


@router.post("/api/v1/portal/tickets/{ticket_id}/reopen")
def reopen(ticket_id: int, a: Actor = Depends(actor), conn: sqlite3.Connection = Depends(get_db)):
    """„Besteht noch“ – das Ticket ist wieder in Arbeit, der Bearbeiter bekommt einen Hinweis."""
    t = _ticket(conn, a, ticket_id)
    if t["status"] in ("done", "wait"):
        now = now_utc_str()
        conn.execute("UPDATE tickets SET status = 'open', done_at = NULL, updated_at = ?, unread = 1 WHERE id = ?", (now, ticket_id))
        hd.system_message(conn, ticket_id, "Der Melder hat im Portal gemeldet: Problem besteht noch – wieder „In Arbeit“")
        if hd.mail_ready(conn) and get_setting(conn, "hd_notify_new") == "1":
            hd.notify_staff(conn, hd.load(conn, ticket_id), "Problem besteht noch", "Der Melder hat das Ticket im Portal wieder geöffnet.")
    return _detail(conn, _ticket(conn, a, ticket_id))
