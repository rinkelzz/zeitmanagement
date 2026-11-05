"""Erinnerungs-Mails.

1. Tägliche Sammel-Mail: überfällige und heute fällige Aufgaben – eine Mail pro Person.
2. Erinnerung zur Uhrzeit: Aufgaben mit Uhrzeit (z. B. „um 14:00“) – eine Mail zur Uhrzeit an die Zuständigen
   (ohne Zuständige an die Person, die sie angelegt hat). Ist die Zeit schon mehr als 6 Stunden vorbei
   (NAS war aus), wird sie still übersprungen.

Tägliche Sammel-Mail im Detail:
- Verschickt ab der eingestellten Uhrzeit (Standard 7 Uhr), höchstens einmal am Tag pro Person
  (Tabelle reminder_log). War die NAS um diese Zeit aus, kommt die Mail, sobald sie wieder läuft – aber nur
  am selben Tag.
- Wer bekommt was? Einzelperson: alle offenen fälligen Aufgaben. Team: Aufgaben, für die man zuständig ist,
  und eigene Aufgaben ohne Zuständige.
- Ohne fällige Aufgaben keine Mail. Fehler werden protokolliert und höchstens 3-mal am Tag wiederholt.
"""
import sqlite3
from datetime import datetime, timedelta
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
    if get_setting(conn, "module_tasks") != "1":  # Aufgaben abgeschaltet → keine Erinnerungen
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


LATE_LIMIT = timedelta(hours=6)


def _recipients(conn: sqlite3.Connection, task) -> List[sqlite3.Row]:
    if is_team(conn):
        rows = conn.execute("SELECT u.* FROM task_assignees a JOIN users u ON u.id = a.user_id WHERE a.task_id = ?",
                            (task["id"],)).fetchall()
        if not rows and task["created_by"]:
            rows = conn.execute("SELECT * FROM users WHERE id = ?", (task["created_by"],)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM users WHERE active = 1 AND is_admin = 1 ORDER BY id LIMIT 1").fetchall()
    return [u for u in rows if u["active"] and u["remind"] and u["email"] and valid_email(u["email"])]


def run_timed(conn: sqlite3.Connection, now_local: Optional[datetime] = None) -> List[Dict]:
    """Erinnerungen zur Uhrzeit verschicken (vom Zeitplan jede Minute aufgerufen)."""
    now_local = now_local or to_local(now_utc())
    if get_setting(conn, "reminders_enabled") != "1" or not smtp_config(conn)["host"]:
        return []
    if get_setting(conn, "module_tasks") != "1":  # Aufgaben abgeschaltet → keine Erinnerungen
        return []
    stamp = now_local.strftime("%Y-%m-%d %H:%M")
    rows = conn.execute(
        "SELECT t.*, p.name AS project FROM tasks t LEFT JOIN projects p ON p.id = t.project_id "
        "WHERE t.done = 0 AND t.due_time IS NOT NULL AND t.due_date IS NOT NULL AND t.reminded_at IS NULL "
        "AND (t.due_date || ' ' || t.due_time) <= ?", (stamp,)).fetchall()
    results = []
    company = get_setting(conn, "company_name")
    for t in rows:
        when = datetime.strptime(f"{t['due_date']} {t['due_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=now_local.tzinfo)
        status, sent_to = "late", []
        if now_local - when <= LATE_LIMIT:
            status = "sent"
            for u in _recipients(conn, t):
                body = "\n".join([
                    f"Hallo {u['display_name']},", "",
                    f"Erinnerung: {t['title']}",
                    f"fällig {_de(t['due_date'])} um {t['due_time']} Uhr" + (f" – {t['project']}" if t["project"] else ""),
                    *(["", t["notes"]] if t["notes"] else []),
                    "", "–", "Zeitmanagement" + (f" · {company}" if company else ""),
                ])
                try:
                    send_mail(conn, u["email"], f"Erinnerung {t['due_time']}: {t['title']}"[:150], body)
                    sent_to.append(u["id"])
                except MailError as e:
                    status = "error"
                    print(f"[zeitmanagement] Erinnerung Aufgabe {t['id']} an {u['email']}: {e}", flush=True)
        if status == "error" and not sent_to:
            continue  # beim nächsten Durchlauf noch einmal versuchen (bis 6 Stunden nach der Zeit)
        conn.execute("UPDATE tasks SET reminded_at = ? WHERE id = ?", (now_utc_str(), t["id"]))
        results.append({"task_id": t["id"], "status": status, "to": sent_to})
    return results
