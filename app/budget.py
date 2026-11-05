"""Stundenbudget je Projekt: Verbrauch berechnen und den Chef bei 80 % und 100 % per Mail warnen.

Die Warnung kommt je Schwelle nur einmal (projects.budget_warned). Sinkt der Verbrauch wieder (Buchung gelöscht,
Budget erhöht), wird die Schwelle zurückgesetzt und kann erneut ausgelöst werden.
"""
import sqlite3
from typing import Dict, Optional

from .db import get_setting
from .mailer import MailError, send_mail, smtp_config, valid_email

LEVELS = (100, 80)
USED_SQL = ("SELECT COALESCE(SUM(ROUND((julianday(end_utc) - julianday(start_utc)) * 1440)), 0) "
            "FROM time_entries WHERE project_id = ? AND end_utc IS NOT NULL")


def used_minutes(conn: sqlite3.Connection, project_id: int) -> int:
    return int(conn.execute(USED_SQL, (project_id,)).fetchone()[0])


def info(conn: sqlite3.Connection, project) -> Optional[Dict]:
    if not project["budget_minutes"]:
        return None
    used = used_minutes(conn, project["id"])
    pct = round(used * 100 / project["budget_minutes"])
    return {"budget_minutes": project["budget_minutes"], "used_minutes": used, "percent": pct,
            "level": next((lv for lv in LEVELS if pct >= lv), 0)}


def check(conn: sqlite3.Connection, project_id: int) -> Optional[int]:
    """Nach jeder Änderung an Zeiten aufrufen. Liefert die neu erreichte Schwelle (oder None)."""
    p = conn.execute("SELECT p.*, c.name AS customer FROM projects p LEFT JOIN customers c ON c.id = p.customer_id "
                     "WHERE p.id = ?", (project_id,)).fetchone()
    if not p:
        return None
    i = info(conn, p)
    level = i["level"] if i else 0
    if level <= p["budget_warned"]:
        if level < p["budget_warned"]:
            conn.execute("UPDATE projects SET budget_warned = ? WHERE id = ?", (level, project_id))
        return None
    conn.execute("UPDATE projects SET budget_warned = ? WHERE id = ?", (level, project_id))
    _mail(conn, p, i)
    return level


def _hm(m: int) -> str:
    return f"{m // 60}:{m % 60:02d}"


def _mail(conn: sqlite3.Connection, p, i: Dict) -> None:
    if not smtp_config(conn)["host"]:
        return
    name = (f"{p['customer']} · " if p["customer"] else "") + p["name"]
    over = i["level"] >= 100
    subject = f"Budget {'überschritten' if over else 'zu 80 % verbraucht'}: {name}"
    company = get_setting(conn, "company_name")
    for u in conn.execute("SELECT * FROM users WHERE active = 1 AND is_admin = 1 AND email IS NOT NULL"):
        if not valid_email(u["email"]):
            continue
        body = "\n".join([
            f"Hallo {u['display_name']},", "",
            f"im Projekt „{name}“ sind {_hm(i['used_minutes'])} von {_hm(i['budget_minutes'])} Stunden gebucht ({i['percent']} %).",
            "Das Budget ist überschritten." if over else "Damit sind 80 % des Budgets verbraucht.",
            "", "–", "Zeitmanagement" + (f" · {company}" if company else ""),
        ])
        try:
            send_mail(conn, u["email"], subject, body)
        except MailError as e:
            print(f"[zeitmanagement] Budget-Warnung an {u['email']}: {e}", flush=True)
