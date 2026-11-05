"""Helpdesk für Bearbeiter: Tickets ansehen, bearbeiten, beantworten; Formular-Links und Einstellungen.

Rechte wie bei Projekten: der Chef (und im Einzelperson-Modus man selbst) sieht alle Tickets, Mitarbeiter die
Tickets der Projekte, in denen sie Mitglied sind. Löschen und die Helpdesk-Verwaltung nur der Chef.
"""
import re
import secrets
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import helpdesk as hd
from .. import modules
from ..access import get_project, project_filter, sees_all
from .. import mailin
from ..db import audit, get_db, get_setting, is_team, set_setting
from ..mailer import valid_email
from ..security import admin_user, current_user
from ..timeutil import local_iso, now_utc_str
from .events import feed_base
from .times import seconds_of

router = APIRouter(prefix="/api/v1", tags=["Helpdesk"], dependencies=[Depends(modules.require("helpdesk"))])

SELECT = ("SELECT t.*, p.name AS project, p.color AS project_color, c.name AS customer, u.display_name AS assignee, "
          "(SELECT COUNT(*) FROM ticket_files f WHERE f.ticket_id = t.id) AS files, "
          "(SELECT COUNT(*) FROM time_entries e WHERE e.ticket_id = t.id AND e.end_utc IS NULL AND e.user_id = ?) AS mine_running "
          "FROM tickets t JOIN projects p ON p.id = t.project_id LEFT JOIN customers c ON c.id = p.customer_id "
          "LEFT JOIN users u ON u.id = t.assignee_id")


def ticket_out(r, seconds: int = 0) -> dict:
    return {
        "id": r["id"], "number": hd.number(r["year"], r["seq"]), "title": r["title"],
        "status": r["status"], "status_label": hd.STATUSES.get(r["status"], r["status"]),
        "priority": r["priority"], "priority_label": hd.PRIORITIES.get(r["priority"], ""),
        "project_id": r["project_id"], "project": r["project"], "project_color": r["project_color"], "customer": r["customer"],
        "assignee_id": r["assignee_id"], "assignee": r["assignee"],
        "requester_name": r["requester_name"], "requester_email": r["requester_email"], "requester_phone": r["requester_phone"],
        "source": r["source"], "source_label": hd.SOURCES.get(r["source"], r["source"]),
        "unread": bool(r["unread"]), "files": r["files"], "running": bool(r["mine_running"]),
        "created_at": local_iso(r["created_at"]), "updated_at": local_iso(r["updated_at"]), "done_at": local_iso(r["done_at"]),
        "seconds": seconds, "minutes": round(seconds / 60),
    }


def _visible(conn, user):
    cond, args = project_filter(conn, user, "p")
    return cond, args


def _get(conn, user, ticket_id: int):
    cond, args = _visible(conn, user)
    row = conn.execute(SELECT + f" WHERE t.id = ? AND {cond}", [user["id"], ticket_id] + args).fetchone()
    if not row:
        raise HTTPException(404, "Ticket nicht gefunden")
    return row


def _seconds(conn, ticket_ids: List[int]) -> dict:
    if not ticket_ids:
        return {}
    out: dict = {}
    for r in conn.execute(f"SELECT ticket_id, start_utc, end_utc FROM time_entries WHERE ticket_id IN ({','.join('?' * len(ticket_ids))})",
                          ticket_ids):
        out[r["ticket_id"]] = out.get(r["ticket_id"], 0) + seconds_of(r)
    return out


VIEWS = {
    "new": "t.status = 'new'",
    "mine": "t.status != 'done' AND t.assignee_id = ?",
    "open": "t.status != 'done'",
    "wait": "t.status = 'wait'",
    "done": "t.status = 'done'",
    "all": "1 = 1",
}


@router.get("/tickets")
def list_tickets(view: str = Query("open", description="new, mine, open, wait, done, all"),
                 project_id: Optional[int] = None, q: str = Query("", max_length=100),
                 user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    if view not in VIEWS:
        raise HTTPException(400, "Unbekannte Ansicht")
    cond, args = _visible(conn, user)
    where, wargs = [cond, VIEWS[view]], list(args) + ([user["id"]] if view == "mine" else [])
    if project_id:
        where.append("t.project_id = ?")
        wargs.append(project_id)
    q = q.strip()
    if q:
        m = hd.NUMBER_RE.match(q)
        if m:
            where.append("t.year = ? AND t.seq = ?")
            wargs += [int(m.group(1)), int(m.group(2))]
        else:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(t.title LIKE ? ESCAPE '\\' OR t.requester_name LIKE ? ESCAPE '\\' OR t.requester_email LIKE ? ESCAPE '\\')")
            wargs += [like, like, like]
    order = "t.done_at DESC" if view == "done" else "t.priority, t.created_at DESC"
    rows = conn.execute(SELECT + " WHERE " + " AND ".join(where) + f" ORDER BY {order} LIMIT 500", [user["id"]] + wargs).fetchall()
    secs = _seconds(conn, [r["id"] for r in rows])
    return [ticket_out(r, secs.get(r["id"], 0)) for r in rows]


@router.get("/tickets/counts")
def counts(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Zahlen für die Seitenleiste."""
    cond, args = _visible(conn, user)
    r = conn.execute(
        "SELECT SUM(t.status = 'new') AS new, SUM(t.status != 'done' AND t.assignee_id = ?) AS mine, "
        "SUM(t.status != 'done') AS open, SUM(t.status = 'wait') AS wait, SUM(t.unread) AS unread "
        f"FROM tickets t JOIN projects p ON p.id = t.project_id WHERE {cond}", [user["id"]] + args).fetchone()
    per = conn.execute(f"SELECT t.project_id, COUNT(*) AS n, SUM(t.priority = 1) AS urgent FROM tickets t JOIN projects p ON p.id = t.project_id "
                       f"WHERE t.status != 'done' AND {cond} GROUP BY t.project_id", args).fetchall()
    inbox = conn.execute("SELECT COUNT(*) FROM mail_inbox WHERE status = 'open'").fetchone()[0] if user["is_admin"] or not is_team(conn) else 0
    return {**{k: r[k] or 0 for k in ("new", "mine", "open", "wait", "unread")}, "inbox": inbox,
            "projects": {str(x["project_id"]): {"open": x["n"], "urgent": x["urgent"] or 0} for x in per}}


class TicketIn(BaseModel):
    project_id: int
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field("", max_length=20000)
    priority: int = Field(3, ge=1, le=3)
    assignee_id: Optional[int] = None
    requester_name: str = Field("", max_length=100)
    requester_email: str = Field("", max_length=200)
    requester_phone: str = Field("", max_length=50)


def _check_assignee(conn, assignee_id: Optional[int], project_id: int) -> None:
    if assignee_id is None:
        return
    u = conn.execute("SELECT * FROM users WHERE id = ? AND active = 1", (assignee_id,)).fetchone()
    if not u:
        raise HTTPException(400, "Bearbeiter nicht gefunden oder deaktiviert")
    if not u["is_admin"] and not conn.execute("SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?",
                                              (project_id, assignee_id)).fetchone():
        if is_team(conn):
            raise HTTPException(400, f"{u['display_name']} ist nicht Mitglied dieses Projekts")


def _check_project(conn, user, project_id: int):
    p = get_project(conn, user, project_id)
    if p["kind"] != "project":
        raise HTTPException(400, "Tickets gehören zu einem Projekt, nicht zu einer Aufgabenliste")
    if p["archived"]:
        raise HTTPException(400, "Dieses Projekt ist archiviert")
    return p


@router.post("/tickets", status_code=201)
def create(data: TicketIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Ticket von Hand anlegen (z. B. nach einem Anruf)."""
    _check_project(conn, user, data.project_id)
    email = data.requester_email.strip()
    if email and not valid_email(email):
        raise HTTPException(400, "E-Mail-Adresse des Melders ist ungültig")
    _check_assignee(conn, data.assignee_id, data.project_id)
    t = hd.create_ticket(conn, project_id=data.project_id, title=data.title.strip(), body=data.body, priority=data.priority,
                         source="manual", requester_name=data.requester_name.strip(), requester_email=email,
                         requester_phone=data.requester_phone.strip(),
                         assignee_id=data.assignee_id if data.assignee_id is not None else user["id"],
                         created_by=user["id"], author_name=user["display_name"])
    audit(conn, user["id"], "ticket_created", f"ticket:{t['id']}", f"{hd.number(t['year'], t['seq'])} · {t['title']}")
    return ticket_out(_get(conn, user, t["id"]))


def _detail(conn, user, row) -> dict:
    msgs = conn.execute("SELECT m.*, u.display_name AS user_name FROM ticket_messages m LEFT JOIN users u ON u.id = m.author_id "
                        "WHERE m.ticket_id = ? ORDER BY m.id", (row["id"],)).fetchall()
    files = conn.execute("SELECT id, message_id, name, mime, size, uploaded_by, created_at FROM ticket_files WHERE ticket_id = ? ORDER BY id",
                         (row["id"],)).fetchall()
    entries = conn.execute("SELECT e.*, u.display_name AS user_name FROM time_entries e JOIN users u ON u.id = e.user_id "
                           "WHERE e.ticket_id = ? ORDER BY e.start_utc", (row["id"],)).fetchall()
    total = sum(seconds_of(e) for e in entries)
    out = ticket_out(row, total)
    out["messages"] = [{"id": m["id"], "kind": m["kind"], "author": m["user_name"] or m["author_name"] or "",
                        "author_id": m["author_id"], "body": m["body"], "created_at": local_iso(m["created_at"])} for m in msgs]
    out["attachments"] = [{"id": f["id"], "message_id": f["message_id"], "name": f["name"], "mime": f["mime"], "size": f["size"],
                           "from_requester": f["uploaded_by"] is None, "created_at": local_iso(f["created_at"])} for f in files]
    out["times"] = [{"id": e["id"], "user_id": e["user_id"], "user_name": e["user_name"], "start": local_iso(e["start_utc"]),
                     "end": local_iso(e["end_utc"]), "running": e["end_utc"] is None, "seconds": seconds_of(e),
                     "note": e["note"], "on_site": bool(e["on_site"])} for e in entries]
    out["can_delete"] = sees_all(conn, user)
    out["mail_ready"] = hd.mail_ready(conn)
    return out


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _get(conn, user, ticket_id)
    if row["unread"]:
        conn.execute("UPDATE tickets SET unread = 0 WHERE id = ?", (ticket_id,))
        row = _get(conn, user, ticket_id)
    return _detail(conn, user, row)


class TicketUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    status: Optional[str] = Field(None, description="new, open, wait, done")
    priority: Optional[int] = Field(None, ge=1, le=3)
    assignee_id: Optional[int] = Field(None, description="0 = niemand")
    project_id: Optional[int] = None
    requester_name: Optional[str] = Field(None, max_length=100)
    requester_email: Optional[str] = Field(None, max_length=200)
    requester_phone: Optional[str] = Field(None, max_length=50)


def set_status(conn, user, row, status: str, background: Optional[BackgroundTasks]) -> None:
    if status == row["status"]:
        return
    now = now_utc_str()
    conn.execute("UPDATE tickets SET status = ?, done_at = ?, updated_at = ? WHERE id = ?",
                 (status, now if status == "done" else None, now, row["id"]))
    hd.system_message(conn, row["id"], f"{user['display_name']} hat den Status auf „{hd.STATUSES[status]}“ gesetzt", user["id"])
    audit(conn, user["id"], "ticket_status", f"ticket:{row['id']}", f"{hd.number(row['year'], row['seq'])} · {hd.STATUSES[status]}")
    if status == "done" and background is not None:
        background.add_task(hd.mail_done, row["id"])


@router.patch("/tickets/{ticket_id}")
def update(ticket_id: int, data: TicketUpdate, background: BackgroundTasks, user=Depends(current_user),
           conn: sqlite3.Connection = Depends(get_db)):
    row = _get(conn, user, ticket_id)
    ch = data.model_dump(exclude_unset=True)
    nr = hd.number(row["year"], row["seq"])
    now = now_utc_str()
    if ch.get("project_id") is not None and data.project_id != row["project_id"]:
        p = _check_project(conn, user, data.project_id)
        conn.execute("UPDATE tickets SET project_id = ? WHERE id = ?", (data.project_id, ticket_id))
        conn.execute("UPDATE time_entries SET project_id = ? WHERE ticket_id = ? AND billed_at IS NULL", (data.project_id, ticket_id))
        hd.system_message(conn, ticket_id, f"{user['display_name']} hat das Ticket nach „{p['name']}“ verschoben", user["id"])
    if ch.get("title") is not None:
        conn.execute("UPDATE tickets SET title = ? WHERE id = ?", (data.title.strip(), ticket_id))
    if ch.get("priority") is not None and data.priority != row["priority"]:
        conn.execute("UPDATE tickets SET priority = ? WHERE id = ?", (data.priority, ticket_id))
        hd.system_message(conn, ticket_id, f"{user['display_name']} hat die Priorität auf „{hd.PRIORITIES[data.priority]}“ gesetzt", user["id"])
    if "assignee_id" in ch:
        aid = data.assignee_id or None
        if aid != row["assignee_id"]:
            pid = data.project_id or row["project_id"]
            _check_assignee(conn, aid, pid)
            conn.execute("UPDATE tickets SET assignee_id = ? WHERE id = ?", (aid, ticket_id))
            name = conn.execute("SELECT display_name FROM users WHERE id = ?", (aid,)).fetchone() if aid else None
            hd.system_message(conn, ticket_id, f"{user['display_name']} hat das Ticket "
                              + (f"{name[0]} zugewiesen" if name else "freigegeben (kein Bearbeiter)"), user["id"])
    for key in ("requester_name", "requester_phone"):
        if ch.get(key) is not None:
            conn.execute(f"UPDATE tickets SET {key} = ? WHERE id = ?", (ch[key].strip(), ticket_id))
    if ch.get("requester_email") is not None:
        email = data.requester_email.strip()
        if email and not valid_email(email):
            raise HTTPException(400, "E-Mail-Adresse des Melders ist ungültig")
        conn.execute("UPDATE tickets SET requester_email = ? WHERE id = ?", (email, ticket_id))
    if ch.get("status") is not None:
        if data.status not in hd.STATUSES:
            raise HTTPException(400, "Unbekannter Status")
        set_status(conn, user, row, data.status, background)
    if ch:
        conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
        if set(ch) - {"status"}:
            audit(conn, user["id"], "ticket_updated", f"ticket:{ticket_id}", f"{nr} · {sorted(set(ch) - {'status'})}")
    return _detail(conn, user, _get(conn, user, ticket_id))


class MessageIn(BaseModel):
    kind: str = Field("reply", description="reply (Antwort an den Melder, per Mail) oder note (interne Notiz)")
    body: str = Field(..., min_length=1, max_length=20000)
    file_ids: List[int] = Field(default_factory=list, description="vorher hochgeladene Dateien, die zu dieser Nachricht gehören")
    done: bool = Field(False, description="danach auf „erledigt“ setzen")


@router.post("/tickets/{ticket_id}/messages", status_code=201)
def add_message(ticket_id: int, data: MessageIn, background: BackgroundTasks, user=Depends(current_user),
                conn: sqlite3.Connection = Depends(get_db)):
    row = _get(conn, user, ticket_id)
    if data.kind not in ("reply", "note"):
        raise HTTPException(400, "Art muss reply oder note sein")
    if data.kind == "reply" and not valid_email(row["requester_email"]):
        raise HTTPException(400, "Für dieses Ticket ist keine E-Mail-Adresse des Melders hinterlegt – bitte eine interne Notiz schreiben "
                                 "oder die Adresse beim Melder eintragen.")
    now = now_utc_str()
    cur = conn.execute("INSERT INTO ticket_messages(ticket_id, kind, author_id, author_name, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (ticket_id, data.kind, user["id"], user["display_name"], data.body.strip(), now))
    mid = cur.lastrowid
    if data.file_ids:
        ids = list(dict.fromkeys(data.file_ids))[:20]
        conn.execute(f"UPDATE ticket_files SET message_id = ? WHERE ticket_id = ? AND message_id IS NULL AND uploaded_by = ? "
                     f"AND id IN ({','.join('?' * len(ids))})", [mid, ticket_id, user["id"]] + ids)
    conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
    audit(conn, user["id"], "ticket_replied" if data.kind == "reply" else "ticket_note", f"ticket:{ticket_id}",
          hd.number(row["year"], row["seq"]))
    if data.kind == "reply":
        if row["status"] == "new":
            set_status(conn, user, row, "open", None)
            row = _get(conn, user, ticket_id)
        background.add_task(hd.mail_reply, ticket_id, mid)
    if data.done:
        set_status(conn, user, row, "done", background)
    return _detail(conn, user, _get(conn, user, ticket_id))


async def read_limited(request: Request, limit: int) -> bytes:
    """Rohdaten lesen, aber höchstens limit Bytes (sonst 413) – die Datei landet nie komplett im Speicher, wenn sie zu groß ist."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise HTTPException(413, f"Die Datei ist zu groß (höchstens {limit // 1048576} MB)")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(413, f"Die Datei ist zu groß (höchstens {limit // 1048576} MB)")
    return bytes(buf)


@router.post("/tickets/{ticket_id}/files", status_code=201)
async def upload(ticket_id: int, request: Request, name: str = Query("datei", max_length=200),
                 user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Datei als Rohdaten hochladen (Body = Dateiinhalt). Sie hängt am Ticket und wird mit der nächsten Nachricht verschickt."""
    _get(conn, user, ticket_id)
    data = await read_limited(request, hd.MAX_FILE_BYTES)
    try:
        f = hd.store_file(conn, ticket_id, name, data, user["id"])
    except hd.FileRejected as e:
        raise HTTPException(400, str(e))
    return f


@router.delete("/tickets/{ticket_id}/files/{file_id}")
def delete_file(ticket_id: int, file_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Noch nicht verschickte eigene Datei wieder entfernen."""
    _get(conn, user, ticket_id)
    cur = conn.execute("DELETE FROM ticket_files WHERE id = ? AND ticket_id = ? AND uploaded_by = ? AND message_id IS NULL",
                       (file_id, ticket_id, user["id"]))
    if not cur.rowcount:
        raise HTTPException(404, "Datei nicht gefunden oder schon verschickt")
    return {"ok": True}


@router.get("/tickets/{ticket_id}/files/{file_id}")
def download(ticket_id: int, file_id: int, inline: bool = False, user=Depends(current_user),
             conn: sqlite3.Connection = Depends(get_db)):
    _get(conn, user, ticket_id)
    f = conn.execute("SELECT * FROM ticket_files WHERE id = ? AND ticket_id = ?", (file_id, ticket_id)).fetchone()
    if not f:
        raise HTTPException(404, "Datei nicht gefunden")
    disp = "inline" if inline and f["mime"].startswith("image/") else "attachment"
    safe = f["name"].encode("ascii", "replace").decode().replace('"', "")
    return Response(f["data"], media_type=f["mime"], headers={
        "Content-Disposition": f'{disp}; filename="{safe}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, no-store"})


@router.delete("/tickets/{ticket_id}")
def delete(ticket_id: int, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _get(conn, admin, ticket_id)
    nr = hd.number(row["year"], row["seq"])
    conn.execute("UPDATE time_entries SET note = CASE WHEN note = '' THEN ? ELSE ? || ' · ' || note END "
                 "WHERE ticket_id = ? AND instr(note, ?) = 0", (nr, nr, ticket_id, nr))
    conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    audit(conn, admin["id"], "ticket_deleted", f"ticket:{ticket_id}", f"{nr} · {row['title']}")
    return {"ok": True}


# --- Verwaltung: Formular-Links und Einstellungen ---------------------------------

HD_KEYS = ("hd_mail_confirm", "hd_mail_reply", "hd_mail_done", "hd_notify_new", "hd_attachments")


def hd_settings(conn) -> dict:
    out = {k[3:]: get_setting(conn, k) == "1" for k in HD_KEYS}
    out.update({"form_hint": get_setting(conn, "hd_form_hint"), "privacy_url": get_setting(conn, "hd_privacy_url"),
                "retention_months": int(get_setting(conn, "hd_retention_months") or 0)})
    return out


class HdSettingsIn(BaseModel):
    mail_confirm: Optional[bool] = None
    mail_reply: Optional[bool] = None
    mail_done: Optional[bool] = None
    notify_new: Optional[bool] = None
    attachments: Optional[bool] = None
    form_hint: Optional[str] = Field(None, max_length=500)
    privacy_url: Optional[str] = Field(None, max_length=300)
    retention_months: Optional[int] = Field(None, ge=0, le=120, description="0 = nie löschen")


@router.get("/helpdesk/settings")
def get_hd_settings(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return hd_settings(conn)


@router.patch("/helpdesk/settings")
def update_hd_settings(data: HdSettingsIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    ch = data.model_dump(exclude_unset=True)
    for k, v in ch.items():
        if v is None:
            continue
        if isinstance(v, bool):
            set_setting(conn, "hd_" + k, "1" if v else "0")
        elif k == "privacy_url":
            url = v.strip()
            if url and not url.startswith(("https://", "http://")):
                raise HTTPException(400, "Der Link zu den Datenschutzhinweisen muss mit https:// beginnen")
            set_setting(conn, "hd_privacy_url", url)
        else:
            set_setting(conn, "hd_" + k, str(v).strip())
    if ch:
        audit(conn, admin["id"], "helpdesk_settings", "helpdesk", str(ch))
    return hd_settings(conn)


def _form_out(r, base: str, senders: dict) -> dict:
    return {"project_id": r["id"], "project": r["name"], "project_color": r["color"], "customer": r["customer"],
            "active": bool(r["active"]), "token": r["token"], "assignee_id": r["assignee_id"],
            "url": f"{base}/melden/{r['token']}" if r["token"] else None, "open": r["open"] or 0,
            "senders": senders.get(r["id"], [])}


@router.get("/helpdesk/forms")
def list_forms(request: Request, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Alle Projekte mit ihrem Formular-Link (oder ohne)."""
    base = feed_base(request, conn)
    rows = conn.execute(
        "SELECT p.id, p.name, p.color, c.name AS customer, f.token, f.active, f.assignee_id, "
        "(SELECT COUNT(*) FROM tickets t WHERE t.project_id = p.id AND t.status != 'done') AS open "
        "FROM projects p LEFT JOIN customers c ON c.id = p.customer_id LEFT JOIN ticket_forms f ON f.project_id = p.id "
        "WHERE p.kind = 'project' AND p.archived = 0 ORDER BY COALESCE(c.name, 'zzz'), p.name").fetchall()
    configured = bool(get_setting(conn, "public_url"))
    from ..config import settings as app_settings
    senders: dict = {}
    for s in conn.execute("SELECT project_id, pattern FROM ticket_senders ORDER BY pattern"):
        senders.setdefault(s["project_id"], []).append(s["pattern"])
    return {"forms": [_form_out(r, base, senders) for r in rows], "public_url_set": configured or bool(app_settings.public_url)}


class FormIn(BaseModel):
    active: Optional[bool] = None
    assignee_id: Optional[int] = Field(None, description="0 = Chef bekommt den Hinweis")
    regenerate: bool = Field(False, description="neuen Link erzeugen, der alte wird ungültig")


@router.put("/helpdesk/forms/{project_id}")
def save_form(project_id: int, data: FormIn, request: Request, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    p = conn.execute("SELECT * FROM projects WHERE id = ? AND kind = 'project'", (project_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Projekt nicht gefunden")
    f = conn.execute("SELECT * FROM ticket_forms WHERE project_id = ?", (project_id,)).fetchone()
    if not f:
        conn.execute("INSERT INTO ticket_forms(project_id, token, active, created_at) VALUES (?, ?, 1, ?)",
                     (project_id, secrets.token_urlsafe(9), now_utc_str()))
        audit(conn, admin["id"], "helpdesk_form", f"project:{project_id}", f"{p['name']} · Formular aktiviert")
    if data.regenerate and f:
        conn.execute("UPDATE ticket_forms SET token = ? WHERE project_id = ?", (secrets.token_urlsafe(9), project_id))
        audit(conn, admin["id"], "helpdesk_form", f"project:{project_id}", f"{p['name']} · neuer Link")
    if data.active is not None:
        conn.execute("UPDATE ticket_forms SET active = ? WHERE project_id = ?", (int(data.active), project_id))
        if f:
            audit(conn, admin["id"], "helpdesk_form", f"project:{project_id}", f"{p['name']} · " + ("aktiv" if data.active else "abgeschaltet"))
    if data.assignee_id is not None:
        aid = data.assignee_id or None
        _check_assignee(conn, aid, project_id)
        conn.execute("UPDATE ticket_forms SET assignee_id = ? WHERE project_id = ?", (aid, project_id))
    return list_forms(request, admin, conn)


# --- Absender-Domains je Projekt (Zuordnung eingehender Mails) -----------------------

PATTERN_RE = re.compile(r"^([^@\s]+@)?([a-z0-9-]+\.)+[a-z]{2,}$")
FREEMAIL = {"gmail.com", "googlemail.com", "gmx.de", "gmx.net", "web.de", "t-online.de", "outlook.com", "outlook.de", "hotmail.com",
            "hotmail.de", "live.de", "yahoo.com", "yahoo.de", "icloud.com", "me.com", "aol.com", "freenet.de", "posteo.de", "mail.de"}


class SendersIn(BaseModel):
    patterns: List[str] = Field(default_factory=list, max_length=50,
                                description="Domains (physio-henze.de) oder einzelne Adressen (chef@gmx.de)")


def _set_senders(conn, project_id: int, patterns: List[str]) -> List[str]:
    clean = []
    for raw in patterns:
        p = raw.strip().lower().lstrip("@")
        if not p:
            continue
        if not PATTERN_RE.match(p):
            raise HTTPException(400, f"„{raw.strip()}“ ist keine Domain (z. B. physio-henze.de) und keine Mail-Adresse")
        if p in FREEMAIL:
            raise HTTPException(400, f"„{p}“ ist ein Freemail-Anbieter – bitte die einzelne Adresse eintragen (z. B. name@{p})")
        other = conn.execute("SELECT p.name FROM ticket_senders s JOIN projects p ON p.id = s.project_id "
                             "WHERE s.pattern = ? AND s.project_id != ?", (p, project_id)).fetchone()
        if other:
            raise HTTPException(409, f"„{p}“ gehört schon zum Projekt „{other[0]}“")
        if p not in clean:
            clean.append(p)
    conn.execute("DELETE FROM ticket_senders WHERE project_id = ?", (project_id,))
    conn.executemany("INSERT INTO ticket_senders(project_id, pattern) VALUES (?, ?)", [(project_id, p) for p in clean])
    return clean


@router.put("/helpdesk/senders/{project_id}")
def save_senders(project_id: int, data: SendersIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    p = conn.execute("SELECT * FROM projects WHERE id = ? AND kind = 'project'", (project_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Projekt nicht gefunden")
    clean = _set_senders(conn, project_id, data.patterns)
    audit(conn, admin["id"], "helpdesk_senders", f"project:{project_id}", f"{p['name']} · {', '.join(clean) or '–'}")
    return {"project_id": project_id, "senders": clean}


# --- E-Mail-Postfach (IMAP) ----------------------------------------------------------

def imap_out(conn) -> dict:
    c = mailin.config(conn)
    return {"enabled": c["enabled"], "host": c["host"], "port": c["port"], "security": c["security"], "user": c["user"],
            "password_set": bool(c["password"]), "folder": c["folder"], "done_folder": c["done_folder"], "interval": c["interval"],
            "last_run": local_iso(get_setting(conn, "imap_last_run") or None), "last_error": get_setting(conn, "imap_last_error"),
            "last_result": get_setting(conn, "imap_last_result")}


class ImapIn(BaseModel):
    enabled: Optional[bool] = None
    host: Optional[str] = Field(None, max_length=200)
    port: Optional[int] = Field(None, ge=1, le=65535)
    security: Optional[str] = Field(None, description="ssl oder starttls")
    user: Optional[str] = Field(None, max_length=200)
    password: Optional[str] = Field(None, max_length=200, description="leer lassen = unverändert")
    folder: Optional[str] = Field(None, min_length=1, max_length=100)
    done_folder: Optional[str] = Field(None, min_length=1, max_length=100)
    interval: Optional[int] = Field(None, ge=1, le=60, description="Minuten")


@router.get("/helpdesk/imap")
def get_imap(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return imap_out(conn)


@router.patch("/helpdesk/imap")
def update_imap(data: ImapIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    ch = data.model_dump(exclude_unset=True)
    if ch.get("security") is not None and data.security not in ("ssl", "starttls"):
        raise HTTPException(400, "Verschlüsselung muss ssl oder starttls sein – ohne Verschlüsselung geht es nicht")
    for k in ("folder", "done_folder"):
        if ch.get(k) is not None and any(c in ch[k] for c in '"\\\r\n*%'):
            raise HTTPException(400, "Ordnername enthält ungültige Zeichen")
    for k, v in ch.items():
        if v is None or (k == "password" and not v):
            continue
        if isinstance(v, bool):
            set_setting(conn, "imap_" + k, "1" if v else "0")
        else:
            set_setting(conn, "imap_" + k, str(v).strip())
    if data.enabled:
        c = mailin.config(conn)
        if not c["host"] or not c["user"] or not c["password"]:
            set_setting(conn, "imap_enabled", "0")
            raise HTTPException(400, "Zum Einschalten bitte Server, Benutzer und Passwort eintragen")
    if ch:
        audit(conn, admin["id"], "helpdesk_imap", "imap", str({k: ("***" if k == "password" else v) for k, v in ch.items()}))
    return imap_out(conn)


@router.post("/helpdesk/imap/test")
def test_imap(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return {"ok": True, "message": mailin.test_connection(conn)}
    except mailin.ImapError as e:
        raise HTTPException(502, str(e))


@router.post("/helpdesk/imap/run")
def run_imap(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Jetzt abrufen (statt auf den Zeitplan zu warten)."""
    if not mailin.config(conn)["enabled"]:
        raise HTTPException(400, "Der Mail-Abruf ist abgeschaltet")
    r = mailin.fetch(conn, force=True)
    if r is None:
        raise HTTPException(409, "Ein Abruf läuft gerade – bitte gleich noch einmal versuchen")
    if r.get("error"):
        raise HTTPException(502, r["error"])
    return {"ok": True, "result": r, "imap": imap_out(conn)}


# --- Posteingang „unzugeordnet“ -------------------------------------------------------

def _inbox_out(conn, r) -> dict:
    files = conn.execute("SELECT id, name, mime, size FROM mail_inbox_files WHERE inbox_id = ? ORDER BY id", (r["id"],)).fetchall()
    return {"id": r["id"], "from_name": r["from_name"], "from_email": r["from_email"], "domain": mailin.domain_of(r["from_email"]),
            "subject": r["subject"], "body": r["body"], "received_at": local_iso(r["received_at"] or r["created_at"]),
            "reason": r["reason"], "skipped": r["skipped"], "status": r["status"],
            "files": [dict(f) for f in files]}


def _inbox_admin(conn, user):
    if not (user["is_admin"] or not is_team(conn)):
        raise HTTPException(403, "Den Posteingang sieht nur der Chef")


@router.get("/helpdesk/inbox")
def inbox(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    _inbox_admin(conn, user)
    rows = conn.execute("SELECT * FROM mail_inbox WHERE status = 'open' ORDER BY id DESC LIMIT 200").fetchall()
    return [_inbox_out(conn, r) for r in rows]


def _inbox_row(conn, inbox_id: int):
    r = conn.execute("SELECT * FROM mail_inbox WHERE id = ?", (inbox_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Mail nicht gefunden")
    if r["status"] != "open":
        raise HTTPException(409, "Diese Mail ist schon erledigt")
    return r


class AssignIn(BaseModel):
    project_id: int
    remember: bool = Field(False, description="Absender-Domain (bzw. bei Freemail die Adresse) dem Projekt zuordnen")


@router.post("/helpdesk/inbox/{inbox_id}/assign", status_code=201)
def assign(inbox_id: int, data: AssignIn, background: BackgroundTasks, user=Depends(current_user),
           conn: sqlite3.Connection = Depends(get_db)):
    _inbox_admin(conn, user)
    r = _inbox_row(conn, inbox_id)
    _check_project(conn, user, data.project_id)
    if not valid_email(r["from_email"]):
        raise HTTPException(400, "Die Mail hat keine gültige Absenderadresse – bitte von Hand ein Ticket anlegen")
    files = [(f["name"], f["data"], f["mime"]) for f in conn.execute("SELECT name, data, mime FROM mail_inbox_files WHERE inbox_id = ?", (inbox_id,))]
    t = mailin.new_ticket_from_mail(conn, project_id=data.project_id, from_name=r["from_name"], from_email=r["from_email"],
                                    subject=r["subject"], body=r["body"], files=files,
                                    skipped=[s for s in r["skipped"].split("; ") if s])
    conn.execute("UPDATE mail_inbox SET status = 'ticket', ticket_id = ?, done_at = ?, done_by = ? WHERE id = ?",
                 (t["id"], now_utc_str(), user["id"], inbox_id))
    conn.execute("DELETE FROM mail_inbox_files WHERE inbox_id = ?", (inbox_id,))
    remembered = None
    if data.remember:
        dom = mailin.domain_of(r["from_email"])
        pattern = r["from_email"] if dom in FREEMAIL else dom
        current = [s[0] for s in conn.execute("SELECT pattern FROM ticket_senders WHERE project_id = ?", (data.project_id,))]
        if pattern not in current:
            _set_senders(conn, data.project_id, current + [pattern])
        remembered = pattern
    background.add_task(hd.mail_created, t["id"])
    return {"ticket": ticket_out(_get(conn, user, t["id"])), "remembered": remembered}


@router.post("/helpdesk/inbox/{inbox_id}/discard")
def discard(inbox_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    _inbox_admin(conn, user)
    r = _inbox_row(conn, inbox_id)
    conn.execute("UPDATE mail_inbox SET status = 'discarded', done_at = ?, done_by = ?, body = '' WHERE id = ?",
                 (now_utc_str(), user["id"], inbox_id))
    conn.execute("DELETE FROM mail_inbox_files WHERE inbox_id = ?", (inbox_id,))
    audit(conn, user["id"], "inbox_discarded", f"inbox:{inbox_id}", f"{r['from_email']} · {r['subject'][:80]}")
    return {"ok": True}


@router.get("/helpdesk/inbox/{inbox_id}/files/{file_id}")
def inbox_file(inbox_id: int, file_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    _inbox_admin(conn, user)
    f = conn.execute("SELECT * FROM mail_inbox_files WHERE id = ? AND inbox_id = ?", (file_id, inbox_id)).fetchone()
    if not f:
        raise HTTPException(404, "Datei nicht gefunden")
    safe = f["name"].encode("ascii", "replace").decode().replace('"', "")
    return Response(f["data"], media_type=f["mime"], headers={
        "Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox", "Cache-Control": "private, no-store"})
