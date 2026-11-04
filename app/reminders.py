"""Tägliche Erinnerungs-Mail: überfällige und heute fällige Aufgaben – eine Sammel-Mail pro Person.

- Verschickt ab der eingestellten Uhrzeit (Standard 7 Uhr), höchstens einmal am Tag pro Person
  (Tabelle reminder_log). War die NAS um diese Zeit aus, kommt die Mail, sobald sie wieder läuft – aber nur
  am selben Tag.
- Wer bekommt was? Einzelperson: alle offenen fälligen Aufgaben. Team: Aufgaben, für die man zuständig ist,
  und eigene Aufgaben ohne Zuständige.
- Ohne fällige Aufgaben keine Mail. Fehler werden protokolliert und höchstens 3-mal am Tag wiederholt.
"""
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from .db import get_setting, is_team
from .mailer import MailError, send_mail, smtp_config, valid_email
from .timeutil import now_utc, now_utc_str, to_local

MAX_TRIES = 3


def due_tasks(conn: sqlite3.Connection, user, today: str) -> List[sqlite3.Row]:
    sql = ("SELECT t.*, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id = t.project_id "
           "WHERE t.done = 0 AND t.due_date IS NOT NULL AND t.due_date <= ?")
    args: list = [today]
    if is_team(conn):
        sql += (" AND (t.id IN (SELECT task_id FROM task_assignees WHERE user_id = ?) OR "
                "(t.created_by = ? AND NOT EXISTS (SELECT 1 FROM task_assignees x WHERE x.task_id = t.id)))")
        args += [user["id"], user["id"]]
    return conn.execute(sql + " ORDER BY t.due_date, t.priority, t.id", args).fetchall()


def _de(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


def compose(conn: sqlite3.Connection, user, tasks: List[sqlite3.Row], today: str) -> (str, str):
    overdue = [t for t in tasks if t["due_date"] < today]
    due = [t for t in tasks if t["due_date"] == today]
    company = get_setting(conn, "company_name")

    def line(t) -> str:
        prio = " [hoch]" if t["priority"] == 1 else " [mittel]" if t["priority"] == 2 else ""
        where = f" – {t['project']}" if t["project"] else ""
        when = f" (fällig {_de(t['due_date'])})" if t["due_date"] < today else ""
        return f"- {t['title']}{prio}{where}{when}"

    parts = [f"Hallo {user['display_name']},", ""]
    if overdue:
        parts += [f"Überfällig ({len(overdue)}):"] + [line(t) for t in overdue] + [""]
    if due:
        parts += [f"Heute fällig ({len(due)}):"] + [line(t) for t in due] + [""]
    parts += ["Viel Erfolg!", "", "–", "Zeitmanagement" + (f" · {company}" if company else ""),
              "Diese Erinnerung lässt sich unter „Mein Zugang“ abschalten."]
    n = len(tasks)
    subject = f"{n} Aufgabe{'n' if n != 1 else ''} fällig" + (f" ({len(overdue)} überfällig)" if overdue else "")
    return subject, "\n".join(parts)


def send_for(conn: sqlite3.Connection, user, today: str) -> Dict:
    """Erinnerung für eine Person bauen und senden. Liefert {status, count, error?}."""
    tasks = due_tasks(conn, user, today)
    if not tasks:
        return {"status": "empty", "count": 0}
    subject, body = compose(conn, user, tasks, today)
    send_mail(conn, user["email"], subject, body)
    return {"status": "sent", "count": len(tasks)}


def _log(conn, user_id: int, day: str, status: str, detail: str = "") -> None:
    conn.execute("INSERT INTO reminder_log(user_id, day, status, detail, ts) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(user_id, day) DO UPDATE SET status = excluded.status, detail = excluded.detail, ts = excluded.ts",
                 (user_id, day, status, detail[:300], now_utc_str()))


def run_due(conn: sqlite3.Connection, now_local: Optional[datetime] = None) -> List[Dict]:
    """Fällige Erinnerungen verschicken (vom Zeitplan alle paar Minuten aufgerufen)."""
    now_local = now_local or to_local(now_utc())
    if get_setting(conn, "reminders_enabled") != "1" or not smtp_config(conn)["host"]:
        return []
    if now_local.hour < int(get_setting(conn, "reminder_hour") or 7):
        return []
    today = now_local.date().isoformat()
    results = []
    users = conn.execute("SELECT * FROM users WHERE active = 1 AND remind = 1 AND email IS NOT NULL AND email != ''").fetchall()
    for u in users:
        if not valid_email(u["email"]):
            continue
        log = conn.execute("SELECT status, detail FROM reminder_log WHERE user_id = ? AND day = ?", (u["id"], today)).fetchone()
        if log and log["status"] in ("sent", "empty"):
            continue
        tries = int((log["detail"] or "0").split("|")[0]) if log and log["status"] == "error" else 0
        if tries >= MAX_TRIES:
            continue
        try:
            r = send_for(conn, u, today)
            _log(conn, u["id"], today, r["status"], str(r["count"]))
        except MailError as e:
            r = {"status": "error", "error": str(e)}
            _log(conn, u["id"], today, "error", f"{tries + 1}|{e}")
        results.append({"user_id": u["id"], **r})
    return results
