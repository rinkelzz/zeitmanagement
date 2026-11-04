"""Wer darf was sehen und ändern?

- Einzelperson-Modus: es gibt nur eine Person, sie sieht alles.
- Team-Modus:
  - Chef/Admin sieht und darf alles.
  - Mitarbeiter sehen Projekte und Aufgabenlisten, in denen sie Mitglied sind, dazu Aufgaben, die ihnen
    zugewiesen sind, und Aufgaben ohne Projekt, die sie selbst angelegt haben.
  - Projekte (mit Kunde, später mit Zeiten) legt nur der Chef an. Aufgabenlisten darf jeder anlegen –
    wer eine Liste anlegt, ist automatisch Mitglied und darf sie bearbeiten.
"""
import sqlite3
from typing import List, Optional, Tuple

from fastapi import HTTPException

from .db import is_team


def sees_all(conn: sqlite3.Connection, user) -> bool:
    return bool(user["is_admin"]) or not is_team(conn)


def member_project_ids(conn: sqlite3.Connection, user) -> List[int]:
    return [r[0] for r in conn.execute("SELECT project_id FROM project_members WHERE user_id = ?", (user["id"],))]


def project_filter(conn: sqlite3.Connection, user, alias: str = "p") -> Tuple[str, list]:
    """SQL-Bedingung für sichtbare Projekte/Listen."""
    if sees_all(conn, user):
        return "1 = 1", []
    return (f"{alias}.id IN (SELECT project_id FROM project_members WHERE user_id = ?)", [user["id"]])


def task_filter(conn: sqlite3.Connection, user, alias: str = "t") -> Tuple[str, list]:
    """SQL-Bedingung für sichtbare Aufgaben."""
    if sees_all(conn, user):
        return "1 = 1", []
    uid = user["id"]
    return (f"({alias}.project_id IN (SELECT project_id FROM project_members WHERE user_id = ?)"
            f" OR {alias}.id IN (SELECT task_id FROM task_assignees WHERE user_id = ?)"
            f" OR ({alias}.project_id IS NULL AND {alias}.created_by = ?))", [uid, uid, uid])


def get_project(conn: sqlite3.Connection, user, project_id: int) -> sqlite3.Row:
    """Projekt/Liste, wenn sichtbar – sonst 404 (auch wenn es existiert, damit nichts durchsickert)."""
    cond, args = project_filter(conn, user)
    row = conn.execute(f"SELECT p.* FROM projects p WHERE p.id = ? AND {cond}", [project_id] + args).fetchone()
    if not row:
        raise HTTPException(404, "Projekt oder Liste nicht gefunden")
    return row


def can_manage_project(conn: sqlite3.Connection, user, project: sqlite3.Row) -> bool:
    if sees_all(conn, user):
        return True
    return project["kind"] == "list" and project["created_by"] == user["id"]


def get_task(conn: sqlite3.Connection, user, task_id: int) -> sqlite3.Row:
    cond, args = task_filter(conn, user)
    row = conn.execute(f"SELECT t.* FROM tasks t WHERE t.id = ? AND {cond}", [task_id] + args).fetchone()
    if not row:
        raise HTTPException(404, "Aufgabe nicht gefunden")
    return row


def check_target_project(conn: sqlite3.Connection, user, project_id: Optional[int]) -> None:
    """Darf die Person eine Aufgabe in dieses Projekt legen? (sichtbar und nicht archiviert)"""
    if project_id is None:
        return
    p = get_project(conn, user, project_id)
    if p["archived"]:
        raise HTTPException(400, "Dieses Projekt ist archiviert")


def check_assignees(conn: sqlite3.Connection, ids: List[int]) -> List[int]:
    ids = list(dict.fromkeys(ids or []))
    if not ids:
        return []
    found = {r[0] for r in conn.execute(
        f"SELECT id FROM users WHERE active = 1 AND id IN ({','.join('?' * len(ids))})", ids)}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(400, "Zuständige Person nicht gefunden oder deaktiviert")
    return ids
