"""Meldungen per E-Mail-Postfach (IMAP).

Ablauf pro Mail:
1. Schon gesehen (Message-ID in mail_seen)? → überspringen.
2. Automatische Mail (Abwesenheitsnotiz, Rückläufer, Newsletter, eigene Absenderadresse)? → ignorieren.
3. Antwort auf ein Ticket ([#T-2026-0042] im Betreff oder In-Reply-To/References auf unsere letzte Mail)
   und Absender ist der Melder, aus seiner Domain oder aus den Absender-Domains des Projekts? → an das Ticket hängen.
4. Sonst neues Ticket, wenn sich der Absender einem Projekt zuordnen lässt (bekannte Adresse, dann Absender-Domain).
   Höchstens MAX_NEW_PER_HOUR neue Tickets pro Absender und Stunde.
5. Sonst: Posteingang „unzugeordnet“ – der Chef wählt das Projekt.
Danach verschiebt der Abruf die Mail in den Erledigt-Ordner. Mails über MAX_MAIL_BYTES bleiben im Postfach und
erscheinen nur als Hinweis im Posteingang.

Nur Standardbibliothek (imaplib, email). Das Postfach-Passwort steht nicht in den Sicherungen.
"""
import hashlib
import imaplib
import re
import socket
import sqlite3
import ssl
import threading
from datetime import timedelta, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional, Tuple

from . import helpdesk as hd
from .db import audit, connect, get_setting, set_setting
from .mailer import valid_email
from .timeutil import now_utc, now_utc_str, parse_utc, to_utc_str

MAX_MAIL_BYTES = 25 * 1024 * 1024
MAX_NEW_PER_HOUR = 10
MAX_PER_RUN = 50
TAG_RE = re.compile(r"\[#T-(\d{4})-(\d{1,6})\]", re.I)
PREFIX_RE = re.compile(r"^\s*((re|aw|wg|fw|fwd|antw|sv|vs)\s*(\[\d+\])?\s*:\s*)+", re.I)
AUTO_SENDERS = re.compile(r"^(mailer-daemon|postmaster|no-?reply|do-?not-?reply|donotreply|bounce[s]?)([+.\-].*)?$", re.I)
_lock = threading.Lock()


# --- Text aus der Mail ----------------------------------------------------------------

class _Text(HTMLParser):
    """HTML → einfacher Text (Skripte, Styles und Bilder fallen weg)."""
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 – kaputtes HTML: dann eben roh ohne Tags
        return re.sub(r"<[^>]+>", " ", html)
    text = "".join(p.out).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


QUOTE_START = re.compile(
    r"^(\s*(am|on)\s.+(schrieb|wrote)\s*.*:\s*$"          # Am 05.11.2026 um 10:00 schrieb …:
    r"|\s*-{2,}\s*(ursprüngliche nachricht|original message|weitergeleitete nachricht)\s*-{2,}"
    r"|\s*(von|from):\s.+$)", re.I)


def strip_quote(text: str) -> str:
    """Bei Antworten nur den neuen Teil behalten (ohne „Am … schrieb …“ und zitierte Zeilen)."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if QUOTE_START.match(line) and i > 0:
            break
        if line.startswith(">"):
            continue
        out.append(line)
    cleaned = "\n".join(out).strip()
    return cleaned or text.strip()


def clean_subject(subject: str) -> str:
    s = TAG_RE.sub("", subject or "")
    s = PREFIX_RE.sub("", s)
    s = TAG_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()[: hd.MAX_TITLE] or "(ohne Betreff)"


class Parsed:
    def __init__(self):
        self.message_id = ""
        self.from_name = ""
        self.from_email = ""
        self.subject = ""
        self.body = ""
        self.date: Optional[str] = None
        self.refs: List[str] = []
        self.files: List[Tuple[str, bytes, str]] = []
        self.skipped: List[str] = []
        self.auto = ""   # Grund, warum die Mail automatisch ist (leer = echte Mail)


def _msgid_of(msg, raw: bytes) -> str:
    mid = (msg.get("Message-ID") or "").strip()
    return mid[:300] if mid else "sha256:" + hashlib.sha256(raw).hexdigest()


def auto_reason(msg, from_email: str, own: List[str]) -> str:
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return "automatische Mail (Auto-Submitted)"
    if (msg.get("Precedence") or "").strip().lower() in ("bulk", "junk", "list", "auto_reply"):
        return "Massenmail (Precedence)"
    if msg.get("List-Id") or msg.get("List-Unsubscribe"):
        return "Newsletter/Verteiler"
    if msg.get("X-Autoreply") or msg.get("X-Autorespond") or (msg.get("X-Auto-Response-Suppress") or "").lower() == "all":
        return "Abwesenheitsnotiz"
    local = from_email.split("@")[0]
    if AUTO_SENDERS.match(local):
        return "automatischer Absender"
    if from_email and from_email in own:
        return "eigene Absenderadresse"
    ctype = msg.get_content_type()
    if ctype == "multipart/report":
        return "Zustellbericht"
    return ""


def parse(raw: bytes, own: List[str]) -> Parsed:
    msg: EmailMessage = message_from_bytes(raw, policy=policy.default)
    p = Parsed()
    p.message_id = _msgid_of(msg, raw)
    try:
        name, addr = parseaddr(str(msg.get("From") or ""))
    except Exception:  # noqa: BLE001
        name, addr = "", ""
    p.from_name = (name or "").strip()[:100]
    p.from_email = (addr or "").strip().lower()[:200]
    try:
        p.subject = str(msg.get("Subject") or "").strip()[:500]
    except Exception:  # noqa: BLE001
        p.subject = ""
    try:
        d = parsedate_to_datetime(str(msg.get("Date")))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        p.date = to_utc_str(d)
    except Exception:  # noqa: BLE001
        p.date = None
    refs = " ".join(str(msg.get(h) or "") for h in ("In-Reply-To", "References"))
    p.refs = re.findall(r"<[^<>\s]+>", refs)
    p.auto = auto_reason(msg, p.from_email, own)
    # Text
    body = ""
    for pref in ("plain", "html"):   # leerer Textteil (manche Programme) → HTML-Teil nehmen
        try:
            part = msg.get_body(preferencelist=(pref,))
            if part is not None:
                content = part.get_content()
                body = html_to_text(content) if pref == "html" else content
        except Exception:  # noqa: BLE001 – kaputte Kodierung: nächsten Teil versuchen
            body = ""
        if body.strip():
            break
    p.body = (body or "").replace("\r\n", "\n").strip()[: hd.MAX_BODY]
    # Anhänge
    try:
        atts = list(msg.iter_attachments())
    except Exception:  # noqa: BLE001
        atts = []
    for a in atts:
        name = a.get_filename() or "datei"
        try:
            data = a.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            data = b""
        mime = hd.sniff(data) if data else None
        if not mime:
            p.skipped.append(f"{name} (nicht erlaubt – nur PNG, JPG, PDF)")
        elif len(data) > hd.MAX_FILE_BYTES:
            p.skipped.append(f"{name} (größer als {hd.MAX_FILE_BYTES // 1048576} MB)")
        elif len(p.files) >= hd.MAX_FILES:
            p.skipped.append(f"{name} (mehr als {hd.MAX_FILES} Dateien)")
        else:
            p.files.append((hd.clean_name(name, mime), data, mime))
    return p


# --- Zuordnung ------------------------------------------------------------------------

def domain_of(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def own_addresses(conn) -> List[str]:
    return [a.strip().lower() for a in (get_setting(conn, "smtp_from"), get_setting(conn, "imap_user"), get_setting(conn, "smtp_user"))
            if a and "@" in a]


def project_for_sender(conn, addr: str) -> Optional[int]:
    """Bekannte Adresse (letztes Ticket dieses Melders), sonst Absender-Adresse oder -Domain des Projekts."""
    row = conn.execute("SELECT p.id FROM ticket_senders s JOIN projects p ON p.id = s.project_id "
                       "WHERE s.pattern = ? AND p.archived = 0", (addr,)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT t.project_id FROM tickets t JOIN projects p ON p.id = t.project_id "
                       "WHERE t.requester_email = ? AND p.archived = 0 ORDER BY t.id DESC LIMIT 1", (addr,)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT p.id FROM ticket_senders s JOIN projects p ON p.id = s.project_id "
                       "WHERE s.pattern = ? AND p.archived = 0", (domain_of(addr),)).fetchone()
    return row[0] if row else None


def _thread_ticket(conn, p: Parsed):
    m = TAG_RE.search(p.subject)
    if m:
        t = conn.execute("SELECT * FROM tickets WHERE year = ? AND seq = ?", (int(m.group(1)), int(m.group(2)))).fetchone()
        if t:
            return t
    for ref in reversed(p.refs):
        t = conn.execute("SELECT * FROM tickets WHERE last_mail_id = ?", (ref,)).fetchone()
        if t:
            return t
    return None


def may_reply(conn, t, addr: str) -> bool:
    """Darf dieser Absender an das Ticket anhängen? Melder selbst, seine Domain oder eine Absender-Domain des Projekts."""
    if not addr:
        return False
    req = (t["requester_email"] or "").lower()
    if addr == req or (req and domain_of(addr) == domain_of(req)):
        return True
    return bool(conn.execute("SELECT 1 FROM ticket_senders WHERE project_id = ? AND pattern IN (?, ?)",
                             (t["project_id"], addr, domain_of(addr))).fetchone())


def _seen(conn, mid: str, action: str, ticket_id=None, inbox_id=None) -> None:
    conn.execute("INSERT OR IGNORE INTO mail_seen(message_id, action, ticket_id, inbox_id, seen_at) VALUES (?, ?, ?, ?, ?)",
                 (mid, action, ticket_id, inbox_id, now_utc_str()))


def _skipped_note(conn, ticket_id: int, skipped: List[str]) -> None:
    if skipped:
        hd.system_message(conn, ticket_id, "Nicht übernommene Anhänge: " + "; ".join(skipped))


def _to_inbox(conn, p: Parsed, reason: str) -> int:
    cur = conn.execute(
        "INSERT INTO mail_inbox(message_id, from_name, from_email, subject, body, received_at, reason, skipped, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (p.message_id, p.from_name, p.from_email, p.subject, p.body, p.date, reason, "; ".join(p.skipped), now_utc_str()))
    iid = cur.lastrowid
    for name, data, mime in p.files:
        conn.execute("INSERT INTO mail_inbox_files(inbox_id, name, mime, size, data) VALUES (?, ?, ?, ?, ?)",
                     (iid, name, mime, len(data), data))
    return iid


def new_ticket_from_mail(conn, *, project_id: int, from_name: str, from_email: str, subject: str, body: str,
                         files: List[Tuple[str, bytes, str]], skipped: List[str]):
    """Ticket aus einer Mail anlegen (beim Abruf oder aus dem Posteingang) – mit Anhängen."""
    form = conn.execute("SELECT assignee_id FROM ticket_forms WHERE project_id = ?", (project_id,)).fetchone()
    t = hd.create_ticket(conn, project_id=project_id, title=clean_subject(subject), body=body or "(ohne Text)", priority=3,
                         source="mail", requester_name=from_name or from_email.split("@")[0], requester_email=from_email,
                         assignee_id=form["assignee_id"] if form else None)
    first = conn.execute("SELECT id FROM ticket_messages WHERE ticket_id = ? ORDER BY id LIMIT 1", (t["id"],)).fetchone()
    for name, data, mime in files:
        conn.execute("INSERT INTO ticket_files(ticket_id, message_id, name, mime, size, data, uploaded_by, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)", (t["id"], first[0] if first else None, name, mime, len(data), data, now_utc_str()))
    _skipped_note(conn, t["id"], skipped)
    audit(conn, None, "ticket_created", f"ticket:{t['id']}", f"{hd.number(t['year'], t['seq'])} · E-Mail · {from_email}")
    return t


def process(conn: sqlite3.Connection, raw: bytes, send: bool = True) -> Dict:
    """Eine Mail verarbeiten. Liefert {"action": ticket|reply|inbox|ignored|seen, …}."""
    p = parse(raw, own_addresses(conn))
    if conn.execute("SELECT 1 FROM mail_seen WHERE message_id = ?", (p.message_id,)).fetchone():
        return {"action": "seen", "message_id": p.message_id}
    if p.auto:
        _seen(conn, p.message_id, "ignored")
        return {"action": "ignored", "reason": p.auto, "message_id": p.message_id}
    if not valid_email(p.from_email):
        iid = _to_inbox(conn, p, "Absenderadresse fehlt oder ist ungültig")
        _seen(conn, p.message_id, "inbox", inbox_id=iid)
        return {"action": "inbox", "inbox_id": iid}

    t = _thread_ticket(conn, p)
    if t and may_reply(conn, t, p.from_email):
        body = strip_quote(p.body) or "(ohne Text)"
        cur = conn.execute("INSERT INTO ticket_messages(ticket_id, kind, author_name, body, created_at) VALUES (?, 'requester', ?, ?, ?)",
                           (t["id"], p.from_name or p.from_email, body, now_utc_str()))
        for name, data, mime in p.files:
            conn.execute("INSERT INTO ticket_files(ticket_id, message_id, name, mime, size, data, uploaded_by, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)", (t["id"], cur.lastrowid, name, mime, len(data), data, now_utc_str()))
        _skipped_note(conn, t["id"], p.skipped)
        now = now_utc_str()
        if t["status"] in ("wait", "done"):
            conn.execute("UPDATE tickets SET status = 'open', done_at = NULL WHERE id = ?", (t["id"],))
            hd.system_message(conn, t["id"], "Antwort per Mail – Ticket ist wieder „In Arbeit“"
                              + (" (war erledigt)" if t["status"] == "done" else ""))
        conn.execute("UPDATE tickets SET unread = 1, updated_at = ? WHERE id = ?", (now, t["id"]))
        _seen(conn, p.message_id, "reply", ticket_id=t["id"])
        if send and hd.mail_ready(conn) and get_setting(conn, "hd_notify_new") == "1":
            hd.notify_staff(conn, hd.load(conn, t["id"]), "Antwort des Melders", body)
        return {"action": "reply", "ticket_id": t["id"]}

    pid = project_for_sender(conn, p.from_email)
    reason = ""
    if pid is not None:
        since = to_utc_str(now_utc() - timedelta(hours=1))
        n = conn.execute("SELECT COUNT(*) FROM tickets WHERE source = 'mail' AND requester_email = ? AND created_at > ?",
                         (p.from_email, since)).fetchone()[0]
        if n >= MAX_NEW_PER_HOUR:
            reason = f"Mehr als {MAX_NEW_PER_HOUR} Meldungen in einer Stunde von diesem Absender"
            pid = None
    else:
        reason = f"Absender {p.from_email} ist keinem Projekt zugeordnet"
        if t:
            reason = f"Antwort auf {hd.number(t['year'], t['seq'])}, aber nicht vom Melder"
    if pid is None:
        iid = _to_inbox(conn, p, reason)
        _seen(conn, p.message_id, "inbox", inbox_id=iid)
        return {"action": "inbox", "inbox_id": iid}
    nt = new_ticket_from_mail(conn, project_id=pid, from_name=p.from_name, from_email=p.from_email, subject=p.subject,
                              body=p.body, files=p.files, skipped=p.skipped)
    _seen(conn, p.message_id, "ticket", ticket_id=nt["id"])
    if send:
        hd.mail_created(nt["id"])
    return {"action": "ticket", "ticket_id": nt["id"]}


def note_too_big(conn, headers: bytes, size: int) -> Dict:
    """Zu große Mail: bleibt im Postfach, erscheint als Hinweis im Posteingang."""
    p = parse(headers, own_addresses(conn))
    if conn.execute("SELECT 1 FROM mail_seen WHERE message_id = ?", (p.message_id,)).fetchone():
        return {"action": "seen"}
    p.body, p.files = "", []
    iid = _to_inbox(conn, p, f"Mail ist zu groß ({size // 1048576} MB, höchstens {MAX_MAIL_BYTES // 1048576} MB) – bitte im Postfach ansehen")
    _seen(conn, p.message_id, "toobig", inbox_id=iid)
    return {"action": "inbox", "inbox_id": iid}


# --- IMAP-Abruf -------------------------------------------------------------------------

class ImapError(Exception):
    pass


def config(conn) -> Dict:
    port = get_setting(conn, "imap_port") or "993"
    return {"enabled": get_setting(conn, "imap_enabled") == "1", "host": get_setting(conn, "imap_host").strip(),
            "port": int(port) if port.isdigit() else 993, "security": get_setting(conn, "imap_security") or "ssl",
            "user": get_setting(conn, "imap_user"), "password": get_setting(conn, "imap_password"),
            "folder": get_setting(conn, "imap_folder") or "INBOX",
            "done_folder": get_setting(conn, "imap_done_folder") or "Zeitmanagement/Erledigt",
            "interval": max(1, int(get_setting(conn, "imap_interval") or 2))}


def _connect(cfg: Dict):
    ctx = ssl.create_default_context()
    try:
        if cfg["security"] == "ssl":
            try:
                m = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], ssl_context=ctx, timeout=30)
            except TypeError:  # Python 3.8 kennt timeout noch nicht
                m = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], ssl_context=ctx)
        else:
            try:
                m = imaplib.IMAP4(cfg["host"], cfg["port"], timeout=30)
            except TypeError:
                m = imaplib.IMAP4(cfg["host"], cfg["port"])
            m.starttls(ssl_context=ctx)
        m.login(cfg["user"], cfg["password"])
        return m
    except imaplib.IMAP4.error as e:
        raise ImapError("Anmeldung am Postfach fehlgeschlagen – Benutzer/Passwort prüfen" if "auth" in str(e).lower() or "login" in str(e).lower()
                        else f"Postfach meldet: {e}")
    except ssl.SSLError as e:
        raise ImapError(f"Verschlüsselung fehlgeschlagen ({e.reason or e}) – Port und Verschlüsselung prüfen")
    except (OSError, socket.timeout) as e:
        raise ImapError(f"Postfach nicht erreichbar: {e}")


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _delimiter(m) -> str:
    try:
        typ, data = m.list('""', '""')
        if typ == "OK" and data and data[0]:
            mm = re.search(rb'\(.*?\)\s+"(.)"', data[0])
            if mm:
                return mm.group(1).decode()
    except Exception:  # noqa: BLE001
        pass
    return "/"


def _done_folder(m, name: str) -> str:
    folder = name.replace("/", _delimiter(m))
    try:
        m.create(_quote(folder))   # gibt es ihn schon, meldet der Server nur einen Fehler
    except Exception:  # noqa: BLE001
        pass
    try:
        m.subscribe(_quote(folder))
    except Exception:  # noqa: BLE001
        pass
    return folder


def _move(m, uid: bytes, folder: str) -> bool:
    try:
        caps = getattr(m, "capabilities", ()) or ()
        if b"MOVE" in caps or "MOVE" in caps:
            typ, _ = m.uid("MOVE", uid, _quote(folder))
            if typ == "OK":
                return True
        typ, _ = m.uid("COPY", uid, _quote(folder))
        if typ != "OK":
            return False
        m.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        m.expunge()
        return True
    except Exception:  # noqa: BLE001
        return False


def _fetch_part(data) -> Tuple[bytes, int]:
    """Aus einer FETCH-Antwort den Inhalt und RFC822.SIZE holen."""
    payload, size = b"", 0
    for item in data or []:
        if isinstance(item, tuple):
            head, body = item[0], item[1]
            mm = re.search(rb"RFC822\.SIZE (\d+)", head)
            if mm:
                size = int(mm.group(1))
            payload = body or payload
        elif isinstance(item, bytes):
            mm = re.search(rb"RFC822\.SIZE (\d+)", item)
            if mm:
                size = int(mm.group(1))
    return payload, size


def test_connection(conn, cfg: Optional[Dict] = None, factory: Optional[Callable] = None) -> str:
    cfg = cfg or config(conn)
    if not cfg["host"] or not cfg["user"]:
        raise ImapError("Bitte Server und Benutzer eintragen")
    m = (factory or _connect)(cfg)
    try:
        typ, data = m.select(_quote(cfg["folder"]), readonly=True)
        if typ != "OK":
            raise ImapError(f"Ordner „{cfg['folder']}“ gibt es im Postfach nicht")
        count = int(data[0]) if data and data[0] and data[0].isdigit() else 0
        return f"Verbindung klappt – {count} Mail{'s' if count != 1 else ''} im Ordner „{cfg['folder']}“."
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass


def fetch(conn: sqlite3.Connection, factory: Optional[Callable] = None, force: bool = False) -> Optional[Dict]:
    """Postfach abrufen, wenn eingeschaltet und fällig (force: sofort). Liefert eine Zusammenfassung oder None."""
    from . import modules
    cfg = config(conn)
    if not cfg["enabled"] or not cfg["host"] or not modules.enabled(conn, "helpdesk"):
        return None
    last = parse_utc(get_setting(conn, "imap_last_run") or None)
    if not force and last and now_utc() - last < timedelta(minutes=cfg["interval"]) - timedelta(seconds=5):
        return None
    if not _lock.acquire(blocking=False):
        return None
    counts = {"ticket": 0, "reply": 0, "inbox": 0, "ignored": 0, "seen": 0, "errors": 0}
    try:
        set_setting(conn, "imap_last_run", now_utc_str())
        try:
            m = (factory or _connect)(cfg)
        except ImapError as e:
            set_setting(conn, "imap_last_error", str(e))
            return {**counts, "error": str(e)}
        try:
            typ, data = m.select(_quote(cfg["folder"]))
            if typ != "OK":
                raise ImapError(f"Ordner „{cfg['folder']}“ gibt es im Postfach nicht")
            typ, data = m.uid("SEARCH", None, "ALL")
            uids = (data[0] or b"").split() if typ == "OK" and data else []
            done = _done_folder(m, cfg["done_folder"]) if uids else cfg["done_folder"]
            for uid in uids[:MAX_PER_RUN]:
                try:
                    typ, data = m.uid("FETCH", uid, "(RFC822.SIZE BODY.PEEK[HEADER])")
                    headers, size = _fetch_part(data)
                    if size > MAX_MAIL_BYTES:
                        r = note_too_big(conn, headers, size)
                        counts["inbox" if r["action"] == "inbox" else "seen"] += 1
                        continue   # bleibt im Postfach
                    typ, data = m.uid("FETCH", uid, "(BODY.PEEK[])")
                    raw, _ = _fetch_part(data)
                    if not raw:
                        counts["errors"] += 1
                        continue
                    r = process(conn, raw)
                    counts[r["action"]] += 1
                    _move(m, uid, done)
                except Exception as e:  # noqa: BLE001 – eine kaputte Mail hält den Rest nicht auf
                    counts["errors"] += 1
                    print(f"[zeitmanagement] Mail {uid!r} nicht verarbeitet: {e}", flush=True)
            set_setting(conn, "imap_last_error", "")
        except ImapError as e:
            set_setting(conn, "imap_last_error", str(e))
            return {**counts, "error": str(e)}
        except (imaplib.IMAP4.error, OSError) as e:
            set_setting(conn, "imap_last_error", f"Abruf fehlgeschlagen: {e}")
            return {**counts, "error": str(e)}
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass
        summary = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
        set_setting(conn, "imap_last_result", summary)
        return counts
    finally:
        _lock.release()


def run_due() -> Optional[Dict]:
    """Vom Zeitplan jede Minute aufgerufen."""
    conn = connect()
    try:
        return fetch(conn)
    finally:
        conn.close()
