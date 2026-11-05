"""Helpdesk: Ticketnummern, Anhänge, Mails an Melder und Bearbeiter, Aufbewahrung.

- Ticketnummern: T-<Jahr>-<laufende Nummer, 4-stellig>, die Zählung beginnt jedes Jahr neu.
- Anhänge: nur PNG, JPG und PDF – geprüft am Dateiinhalt, nicht an der Endung. Höchstens MAX_FILE_BYTES pro
  Datei und MAX_FILES pro Meldung. Sie liegen in der Datenbank und sind damit in jeder Sicherung enthalten.
- Mails gehen über den eigenen SMTP-Server, als „Auto-Submitted“ gekennzeichnet (keine Abwesenheits-Schleifen).
  Schlägt eine Mail fehl, bleibt das Ticket trotzdem gespeichert; der Fehler steht im Verlauf.
"""
import re
import secrets
import sqlite3
from datetime import timedelta
from typing import Dict, List, Optional

from .db import audit, connect, get_setting
from .mailer import MailError, send_mail, smtp_config, valid_email
from .timeutil import now_utc, now_utc_str, parse_utc, to_local, to_utc_str

STATUSES = {"new": "Neu", "open": "In Arbeit", "wait": "Wartet auf Rückmeldung", "done": "Erledigt"}
PRIORITIES = {1: "Dringend", 2: "Hoch", 3: "Normal"}
SOURCES = {"form": "Formular", "manual": "Von Hand", "mail": "E-Mail", "portal": "Portal"}

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 5
UPLOAD_MINUTES = 30
NUMBER_RE = re.compile(r"^T-(\d{4})-(\d{1,6})$", re.I)


def number(year: int, seq: int) -> str:
    return f"T-{year}-{seq:04d}"


def tag(row) -> str:
    """Kennung im Betreff, z. B. [#T-2026-0042] – damit lassen sich Antworten später zuordnen."""
    return f"[#{number(row['year'], row['seq'])}]"


def create_ticket(conn: sqlite3.Connection, *, project_id: int, title: str, body: str, priority: int, source: str,
                  requester_name: str = "", requester_email: str = "", requester_phone: str = "",
                  assignee_id: Optional[int] = None, created_by: Optional[int] = None,
                  author_name: str = "", with_upload_key: bool = False) -> sqlite3.Row:
    """Legt ein Ticket samt erster Nachricht an. Die Nummer wird in einer einzigen Anweisung vergeben
    (kein Doppelvergeben, auch wenn zwei Meldungen gleichzeitig eingehen)."""
    now = now_utc_str()
    year = to_local(now_utc()).year
    key = secrets.token_urlsafe(24) if with_upload_key else None
    until = to_utc_str(now_utc() + timedelta(minutes=UPLOAD_MINUTES)) if with_upload_key else None
    cur = conn.execute(
        "INSERT INTO tickets(year, seq, project_id, title, status, priority, assignee_id, requester_name, requester_email, "
        "requester_phone, source, unread, upload_key, upload_until, created_by, created_at, updated_at) "
        "SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, 'new', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? FROM tickets WHERE year = ?",
        (year, project_id, title, priority, assignee_id, requester_name, requester_email, requester_phone, source,
         0 if created_by else 1, key, until, created_by, now, now, year))
    tid = cur.lastrowid
    kind = "note" if created_by and not requester_name and not requester_email else "requester"
    if body.strip():
        conn.execute("INSERT INTO ticket_messages(ticket_id, kind, author_id, author_name, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (tid, kind, created_by if kind == "note" else None, author_name or requester_name, body.strip(), now))
    return conn.execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()


def system_message(conn: sqlite3.Connection, ticket_id: int, text: str, actor_id: Optional[int] = None) -> None:
    conn.execute("INSERT INTO ticket_messages(ticket_id, kind, author_id, body, created_at) VALUES (?, 'system', ?, ?, ?)",
                 (ticket_id, actor_id, text, now_utc_str()))


# --- Anhänge -------------------------------------------------------------------

def sniff(data: bytes) -> Optional[str]:
    """Dateityp am Inhalt erkennen. Nur PNG, JPG und PDF sind erlaubt (kein SVG/HTML – die könnten Skripte enthalten)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}


def clean_name(name: str, mime: str) -> str:
    """Dateiname ohne Pfad und Steuerzeichen, mit passender Endung."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f\"<>|:*?]", "", name).strip(" .")[:120] or "datei"
    ext = _EXT[mime]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if not name.lower().endswith((ext, ".jpeg") if ext == ".jpg" else ext):
        name = (stem or "datei") + ext
    return name


class FileRejected(Exception):
    pass


def store_file(conn: sqlite3.Connection, ticket_id: int, name: str, data: bytes, uploaded_by: Optional[int],
               message_id: Optional[int] = None) -> Dict:
    if not data:
        raise FileRejected("Die Datei ist leer")
    if len(data) > MAX_FILE_BYTES:
        raise FileRejected(f"Die Datei ist zu groß (höchstens {MAX_FILE_BYTES // 1048576} MB)")
    mime = sniff(data)
    if not mime:
        raise FileRejected("Nur Bilder (PNG, JPG) und PDF-Dateien sind erlaubt")
    count = conn.execute("SELECT COUNT(*) FROM ticket_files WHERE ticket_id = ? AND uploaded_by IS ?",
                         (ticket_id, uploaded_by)).fetchone()[0] if uploaded_by is None else 0
    if uploaded_by is None and count >= MAX_FILES:
        raise FileRejected(f"Höchstens {MAX_FILES} Dateien pro Meldung")
    name = clean_name(name, mime)
    cur = conn.execute("INSERT INTO ticket_files(ticket_id, message_id, name, mime, size, data, uploaded_by, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (ticket_id, message_id, name, mime, len(data), data, uploaded_by, now_utc_str()))
    return {"id": cur.lastrowid, "name": name, "mime": mime, "size": len(data)}


# --- Mails ---------------------------------------------------------------------

def _company(conn) -> str:
    return get_setting(conn, "company_name") or get_setting(conn, "smtp_from_name") or "Zeitmanagement"


def _footer(conn) -> str:
    return f"\n\n-- \n{_company(conn)}\nBitte die Ticketnummer im Betreff stehen lassen, wenn du antwortest."


def _load(conn, ticket_id: int):
    return conn.execute("SELECT t.*, p.name AS project FROM tickets t JOIN projects p ON p.id = t.project_id WHERE t.id = ?",
                        (ticket_id,)).fetchone()


def _send(conn, ticket_id: int, to: str, subject: str, body: str, what: str,
          attachments: Optional[List] = None) -> bool:
    """Mail verschicken und das Ergebnis im Verlauf vermerken."""
    try:
        send_mail(conn, to, subject, body, attachments=attachments,
                  headers={"Auto-Submitted": "auto-generated" if what != "Antwort" else "auto-replied"})
    except MailError as e:
        system_message(conn, ticket_id, f"{what} an {to} konnte nicht verschickt werden: {e}")
        return False
    system_message(conn, ticket_id, f"{what} an {to} verschickt")
    return True


def mail_ready(conn) -> bool:
    return bool(smtp_config(conn)["host"])


def mail_created(ticket_id: int) -> None:
    """Nach einer neuen Meldung (im Hintergrund): Bestätigung an den Melder, Hinweis an den Bearbeiter."""
    conn = connect()
    try:
        t = _load(conn, ticket_id)
        if not t or not mail_ready(conn):
            return
        nr = number(t["year"], t["seq"])
        if get_setting(conn, "hd_mail_confirm") == "1" and valid_email(t["requester_email"]):
            hello = f"Hallo {t['requester_name']},\n\n" if t["requester_name"] else "Hallo,\n\n"
            _send(conn, t["id"], t["requester_email"], f"{tag(t)} Deine Meldung: {t['title']}",
                  hello + f"deine Meldung ist eingegangen und hat die Nummer {nr}.\n"
                  "Wir kümmern uns darum und melden uns per Mail.\n\n"
                  f"Betreff: {t['title']}" + _footer(conn), "Eingangsbestätigung")
        if get_setting(conn, "hd_notify_new") == "1":
            to = _staff_addresses(conn, t)
            prio = " (DRINGEND)" if t["priority"] == 1 else ""
            first = conn.execute("SELECT body FROM ticket_messages WHERE ticket_id = ? AND kind = 'requester' ORDER BY id LIMIT 1",
                                 (t["id"],)).fetchone()
            for addr in to:
                try:
                    send_mail(conn, addr, f"{tag(t)} Neue Meldung{prio}: {t['title']}",
                              f"Neue Meldung im Projekt „{t['project']}“ von {t['requester_name'] or 'unbekannt'}"
                              + (f" <{t['requester_email']}>" if t["requester_email"] else "") + ":\n\n"
                              + (first["body"] if first else "") + "\n\nIm Zeitmanagement unter Tickets → " + nr,
                              headers={"Auto-Submitted": "auto-generated"})
                except MailError as e:
                    system_message(conn, t["id"], f"Hinweis an {addr} konnte nicht verschickt werden: {e}")
    finally:
        conn.close()


def _staff_addresses(conn, t) -> List[str]:
    if t["assignee_id"]:
        rows = conn.execute("SELECT email FROM users WHERE id = ? AND active = 1", (t["assignee_id"],)).fetchall()
    else:
        rows = conn.execute("SELECT email FROM users WHERE is_admin = 1 AND active = 1").fetchall()
    return [r[0] for r in rows if r[0] and valid_email(r[0])]


def mail_reply(ticket_id: int, message_id: int) -> None:
    """Antwort an den Melder (im Hintergrund), mit den Anhängen dieser Antwort."""
    conn = connect()
    try:
        t = _load(conn, ticket_id)
        m = conn.execute("SELECT * FROM ticket_messages WHERE id = ? AND ticket_id = ?", (message_id, ticket_id)).fetchone()
        if not t or not m or not valid_email(t["requester_email"]):
            return
        if not mail_ready(conn) or get_setting(conn, "hd_mail_reply") != "1":
            return
        files = [(f["name"], f["data"], f["mime"]) for f in
                 conn.execute("SELECT name, data, mime FROM ticket_files WHERE message_id = ?", (message_id,))]
        hello = f"Hallo {t['requester_name']},\n\n" if t["requester_name"] else ""
        _send(conn, t["id"], t["requester_email"], f"{tag(t)} Re: {t['title']}",
              hello + m["body"] + _footer(conn), "Antwort", attachments=files)
    finally:
        conn.close()


def mail_done(ticket_id: int) -> None:
    conn = connect()
    try:
        t = _load(conn, ticket_id)
        if not t or t["status"] != "done" or not valid_email(t["requester_email"]):
            return
        if not mail_ready(conn) or get_setting(conn, "hd_mail_done") != "1":
            return
        hello = f"Hallo {t['requester_name']},\n\n" if t["requester_name"] else "Hallo,\n\n"
        _send(conn, t["id"], t["requester_email"], f"{tag(t)} Erledigt: {t['title']}",
              hello + f"deine Meldung {number(t['year'], t['seq'])} ist erledigt.\n"
              "Falls das Problem wieder auftritt, melde dich gern noch einmal." + _footer(conn), "Erledigt-Mail")
    finally:
        conn.close()


# --- Aufbewahrung ----------------------------------------------------------------

def purge_old(conn: sqlite3.Connection) -> int:
    """Erledigte Tickets nach der eingestellten Frist löschen (samt Verlauf und Anhängen).
    Gebuchte Zeiten bleiben; ihre Notiz bekommt die Ticketnummer, damit man sie noch zuordnen kann."""
    months = get_setting(conn, "hd_retention_months") or "0"
    if not months.isdigit() or int(months) <= 0:
        return 0
    limit = to_utc_str(now_utc() - timedelta(days=int(months) * 30))
    rows = conn.execute("SELECT id, year, seq FROM tickets WHERE status = 'done' AND done_at IS NOT NULL AND done_at < ?",
                        (limit,)).fetchall()
    for r in rows:
        nr = number(r["year"], r["seq"])
        conn.execute("UPDATE time_entries SET note = CASE WHEN note = '' THEN ? ELSE ? || ' · ' || note END "
                     "WHERE ticket_id = ? AND instr(note, ?) = 0", (nr, nr, r["id"], nr))
        conn.execute("DELETE FROM tickets WHERE id = ?", (r["id"],))
    if rows:
        audit(conn, None, "tickets_purged", "helpdesk", f"{len(rows)} erledigte Tickets älter als {months} Monate gelöscht")
    return len(rows)


def is_expired(until: Optional[str]) -> bool:
    return not until or parse_utc(until) < now_utc()
