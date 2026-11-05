"""Melder-Portal: Anmelde-Links per Mail, Sitzungen und Statuslinks für einzelne Tickets.

- Anmelden: E-Mail eingeben → Link per Mail (30 Minuten, einmal nutzbar). Einen Link bekommt nur, wer der App
  bekannt ist (Melder eines Tickets oder Absender-Domain eines Projekts). Die Antwort ist immer gleich, damit niemand
  ausprobieren kann, welche Adressen existieren.
- Sitzung: 30 Tage, eigener Cookie, getrennt von den Mitarbeiter-Konten. Gespeichert wird nur der SHA-256-Hash.
- Statuslink: …/portal/t/<id>-<hmac>. Berechnet aus einem geheimen Schlüssel der Installation, der Ticket-ID und einem
  Salz pro Ticket – „Statuslink erneuern“ würfelt das Salz neu, der alte Link ist dann ungültig.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from typing import Dict, List, Optional

from .config import settings
from .db import get_setting, set_setting
from .mailer import MailError, send_mail, valid_email
from .timeutil import now_utc, now_utc_str, to_utc_str

LOGIN_MINUTES = 30
SESSION_DAYS = 30
COOKIE = "zm_portal"
LINK_HEADER = "x-portal-link"
TOKEN_RE = re.compile(r"^(\d{1,10})-([0-9a-f]{32})$")


def enabled(conn) -> bool:
    from . import modules
    return modules.enabled(conn, "helpdesk") and get_setting(conn, "hd_portal") == "1"


def _h(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def _secret(conn) -> bytes:
    s = get_setting(conn, "portal_secret")
    if not s:
        s = secrets.token_hex(32)
        set_setting(conn, "portal_secret", s)
    return s.encode()


def base_url(conn) -> str:
    """Öffentliche Adresse der App (für Links in Mails). Leer, wenn keine eingetragen ist."""
    return (get_setting(conn, "public_url") or settings.public_url or "").rstrip("/")


# --- Statuslinks ---------------------------------------------------------------------

def ticket_token(conn, t) -> str:
    salt = t["link_salt"] if "link_salt" in t.keys() else ""
    mac = hmac.new(_secret(conn), f"{t['id']}:{salt}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{t['id']}-{mac}"


def status_url(conn, t) -> Optional[str]:
    if not enabled(conn):
        return None
    base = base_url(conn)
    return f"{base}/portal/t/{ticket_token(conn, t)}" if base else None


def ticket_from_token(conn, token: str):
    m = TOKEN_RE.match(token or "")
    if not m:
        return None
    t = conn.execute("SELECT * FROM tickets WHERE id = ?", (int(m.group(1)),)).fetchone()
    if not t or not hmac.compare_digest(ticket_token(conn, t), token):
        return None
    return t


def renew_link(conn, ticket_id: int) -> None:
    conn.execute("UPDATE tickets SET link_salt = ? WHERE id = ?", (secrets.token_hex(8), ticket_id))


# --- Wer ist bekannt? ----------------------------------------------------------------------

def known_projects(conn, email: str) -> List[Dict]:
    """Projekte, in denen diese Adresse melden darf: eigene Tickets und Absender-Domains/-Adressen."""
    email = email.lower()
    dom = email.rsplit("@", 1)[-1]
    rows = conn.execute(
        "SELECT DISTINCT p.id, p.name, c.name AS customer FROM projects p LEFT JOIN customers c ON c.id = p.customer_id "
        "WHERE p.kind = 'project' AND p.archived = 0 AND (p.id IN (SELECT project_id FROM tickets WHERE requester_email = ?) "
        "OR p.id IN (SELECT project_id FROM ticket_senders WHERE pattern IN (?, ?))) ORDER BY c.name, p.name",
        (email, email, dom)).fetchall()
    return [dict(r) for r in rows]


# --- Anmeldung ----------------------------------------------------------------------------

def request_login(conn, email: str) -> bool:
    """Anmelde-Link verschicken, wenn die Adresse bekannt ist. Liefert, ob eine Mail rausging (nur für Tests/Protokoll)."""
    email = email.strip().lower()
    if not valid_email(email) or not known_projects(conn, email):
        return False
    base = base_url(conn)
    if not base:
        return False
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM portal_logins WHERE expires_at <= ?", (now_utc_str(),))
    conn.execute("INSERT INTO portal_logins(token_hash, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
                 (_h(token), email, now_utc_str(), to_utc_str(now_utc() + timedelta(minutes=LOGIN_MINUTES))))
    company = get_setting(conn, "company_name") or "Support"
    try:
        send_mail(conn, email, f"Dein Anmelde-Link – {company}",
                  "Hallo,\n\nmit diesem Link meldest du dich im Support-Portal an und siehst deine Tickets:\n\n"
                  f"{base}/portal#login={token}\n\n"
                  f"Der Link gilt {LOGIN_MINUTES} Minuten und funktioniert nur einmal.\n"
                  "Wenn du keinen Link angefordert hast, kannst du diese Mail ignorieren.\n\n-- \n" + company,
                  headers={"Auto-Submitted": "auto-generated"})
    except MailError as e:
        print(f"[zeitmanagement] Portal-Anmeldelink an {email} fehlgeschlagen: {e}", flush=True)
        return False
    return True


def redeem(conn, token: str) -> Optional[str]:
    """Anmelde-Link einlösen → neue Sitzung. Liefert den Sitzungsschlüssel."""
    if not token or len(token) > 100:
        return None
    row = conn.execute("SELECT * FROM portal_logins WHERE token_hash = ?", (_h(token),)).fetchone()
    if not row or row["used_at"] or row["expires_at"] <= now_utc_str():
        return None
    cur = conn.execute("UPDATE portal_logins SET used_at = ? WHERE token_hash = ? AND used_at IS NULL", (now_utc_str(), _h(token)))
    if not cur.rowcount:   # gleichzeitig zweimal eingelöst
        return None
    session = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM portal_sessions WHERE expires_at <= ?", (now_utc_str(),))
    conn.execute("INSERT INTO portal_sessions(token_hash, email, created_at, expires_at, last_seen) VALUES (?, ?, ?, ?, ?)",
                 (_h(session), row["email"], now_utc_str(), to_utc_str(now_utc() + timedelta(days=SESSION_DAYS)), now_utc_str()))
    return session


def session_email(conn, session: str) -> Optional[str]:
    if not session or len(session) > 100:
        return None
    row = conn.execute("SELECT email FROM portal_sessions WHERE token_hash = ? AND expires_at > ?", (_h(session), now_utc_str())).fetchone()
    if row:
        conn.execute("UPDATE portal_sessions SET last_seen = ? WHERE token_hash = ?", (now_utc_str(), _h(session)))
    return row["email"] if row else None


def logout(conn, session: str) -> None:
    conn.execute("DELETE FROM portal_sessions WHERE token_hash = ?", (_h(session or ""),))
