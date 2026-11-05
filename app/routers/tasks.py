"""Aufgaben: Ansichten, Schnelleingabe, anlegen, ändern, erledigen, löschen."""
import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import quickadd, recur
from ..access import check_assignees, check_target_project, get_task, project_filter, sees_all, task_filter
from ..db import audit, get_db, is_team
from ..security import current_user
from ..timeutil import local_iso, now_utc_str, today_local

TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

router = APIRouter(prefix="/api/v1/tasks", tags=["Aufgaben"])

PRIORITY_NAMES = {1: "hoch", 2: "mittel", 3: "normal"}
VIEWS = ("today", "overdue", "upcoming", "mine", "open", "done", "inbox")


class TaskIn(BaseModel):
    text: Optional[str] = Field(None, max_length=500, description="Schnelleingabe, z. B. „Backup prüfen morgen !hoch #praxis“")
    title: Optional[str] = Field(None, max_length=300)
    notes: str = Field("", max_length=10000)
    due_date: Optional[date] = None
    priority: Optional[int] = Field(None, ge=1, le=3)
    project_id: Optional[int] = None
    assignee_ids: Optional[List[int]] = None
    due_time: Optional[str] = Field(None, pattern=TIME_PATTERN, description="HH:MM – Erinnerung zu dieser Uhrzeit")
    recur: Optional[str] = Field(None, max_length=10, description="d:1, wd, w:1, m:1, y:1 (oder :N)")
    items: Optional[List[str]] = Field(None, max_length=100, description="Checkliste (Unteraufgaben)")


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    notes: Optional[str] = Field(None, max_length=10000)
    due_date: Optional[date] = None
    priority: Optional[int] = Field(None, ge=1, le=3)
    project_id: Optional[int] = None
    assignee_ids: Optional[List[int]] = None
    done: Optional[bool] = None
    due_time: Optional[str] = Field(None, pattern=TIME_PATTERN)
    recur: Optional[str] = Field(None, max_length=10)


class ItemIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)


class ItemUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    done: Optional[bool] = None


def _names(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT id, display_name FROM users")}


def task_rows_out(conn, rows, user) -> list:
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    assigned = {}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for a in conn.execute(f"SELECT task_id, user_id FROM task_assignees WHERE task_id IN ({','.join('?' * len(part))})", part):
            assigned.setdefault(a[0], []).append(a[1])
    items = {}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for it in conn.execute(f"SELECT * FROM task_items WHERE task_id IN ({','.join('?' * len(part))}) ORDER BY sort, id", part):
            items.setdefault(it["task_id"], []).append({"id": it["id"], "title": it["title"], "done": bool(it["done"])})
    names = _names(conn)
    boss = sees_all(conn, user)
    out = []
    for r in rows:
        a = sorted(assigned.get(r["id"], []), key=lambda i: names.get(i, ""))
        out.append({
            "id": r["id"], "title": r["title"], "notes": r["notes"], "due_date": r["due_date"],
            "priority": r["priority"], "done": bool(r["done"]),
            "project_id": r["project_id"], "project": r["project"], "project_color": r["project_color"],
            "project_kind": r["project_kind"],
            "assignee_ids": a, "assignees": [names.get(i, "?") for i in a],
            "created_by": r["created_by"], "created_by_name": names.get(r["created_by"]),
            "created_at": local_iso(r["created_at"]),
            "completed_at": local_iso(r["completed_at"]), "completed_by_name": names.get(r["completed_by"]),
            "can_delete": boss or r["created_by"] == user["id"],
            "due_time": r["due_time"], "recur": r["recur"], "recur_label": recur.label(r["recur"]),
            "items": items.get(r["id"], []),
        })
    return out


SELECT = ("SELECT t.*, p.name AS project, p.color AS project_color, p.kind AS project_kind "
          "FROM tasks t LEFT JOIN projects p ON p.id = t.project_id")
# Überfällig zuerst, dann nach Datum (ohne Datum ans Ende), dann Priorität
ORDER_OPEN = " ORDER BY t.due_date IS NULL, t.due_date, t.priority, t.id"


def _mine_sql() -> str:
    return ("(t.id IN (SELECT task_id FROM task_assignees WHERE user_id = ?) OR "
            "(t.created_by = ? AND NOT EXISTS (SELECT 1 FROM task_assignees x WHERE x.task_id = t.id)))")


def load_one(conn, user, task_id: int) -> dict:
    row = conn.execute(SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    return task_rows_out(conn, [row], user)[0]


@router.get("")
def list_tasks(
    view: Optional[str] = Query(None, description=", ".join(VIEWS)),
    project_id: Optional[int] = None,
    include_done: bool = False,
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(500, ge=1, le=2000),
    user=Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Aufgaben einer Ansicht oder eines Projekts. Offene sortiert nach Fälligkeit, erledigte nach Erledigt-Zeitpunkt."""
    cond, args = task_filter(conn, user)
    where, params = [cond], list(args)
    today = today_local().isoformat()
    done_view = view == "done"
    if view and view not in VIEWS:
        raise HTTPException(400, "Unbekannte Ansicht")
    if project_id is not None:
        where.append("t.project_id = ?")
        params.append(project_id)
    if view == "today":
        where.append("t.due_date = ?")
        params.append(today)
    elif view == "overdue":
        where.append("t.due_date < ?")
        params.append(today)
    elif view == "upcoming":
        where.append("t.due_date > ?")
        params.append(today)
    elif view == "mine":
        where.append(_mine_sql())
        params += [user["id"], user["id"]]
    elif view == "inbox":
        where.append("t.project_id IS NULL")
    if q:
        where.append("(t.title LIKE ? OR t.notes LIKE ?)")
        params += [f"%{q.strip()}%"] * 2
    if done_view:
        sql = SELECT + " WHERE " + " AND ".join(where + ["t.done = 1"]) + " ORDER BY t.completed_at DESC, t.id DESC LIMIT ?"
        rows = conn.execute(sql, params + [min(limit, 300)]).fetchall()
    else:
        sql = SELECT + " WHERE " + " AND ".join(where + ["t.done = 0"]) + ORDER_OPEN + " LIMIT ?"
        rows = conn.execute(sql, params + [limit]).fetchall()
        if include_done:
            sql = SELECT + " WHERE " + " AND ".join(where + ["t.done = 1"]) + " ORDER BY t.completed_at DESC, t.id DESC LIMIT 50"
            rows += conn.execute(sql, params).fetchall()
    return task_rows_out(conn, rows, user)


@router.get("/counts")
def counts(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Zahlen für die Seitenleiste."""
    cond, args = task_filter(conn, user)
    today = today_local().isoformat()
    base = f"SELECT COUNT(*) FROM tasks t WHERE t.done = 0 AND {cond}"
    one = lambda extra, p=(): conn.execute(base + extra, list(args) + list(p)).fetchone()[0]  # noqa: E731
    return {
        "today": one(" AND t.due_date = ?", [today]),
        "overdue": one(" AND t.due_date < ?", [today]),
        "upcoming": one(" AND t.due_date > ?", [today]),
        "mine": one(" AND " + _mine_sql(), [user["id"], user["id"]]),
        "open": one(""),
        "inbox": one(" AND t.project_id IS NULL"),
    }


def _parse_context(conn, user):
    cond, args = project_filter(conn, user)
    projects = [dict(r) for r in conn.execute(f"SELECT p.id, p.name FROM projects p WHERE p.archived = 0 AND {cond}", args)]
    users = [dict(r) for r in conn.execute("SELECT id, username, display_name FROM users WHERE active = 1")] if is_team(conn) else []
    return projects, users


@router.get("/parse")
def parse_text(text: str = Query(..., max_length=500), user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Vorschau der Schnelleingabe (was wird als Datum, Priorität, Liste, Zuständige erkannt?)."""
    projects, users = _parse_context(conn, user)
    r = quickadd.parse(text, today_local(), projects, users)
    names = {u["id"]: u["display_name"] for u in users}
    pname = next((p["name"] for p in projects if p["id"] == r["project_id"]), None)
    return {**r, "project": pname, "assignees": [names.get(i) for i in r["assignee_ids"]], "recur_label": recur.label(r["recur"])}


@router.post("", status_code=201)
def create_task(data: TaskIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    fields = data.model_dump(exclude_unset=True)
    title = (data.title or "").strip()
    due = data.due_date.isoformat() if data.due_date else None
    prio, project_id, assignees = data.priority, data.project_id, list(data.assignee_ids or [])
    rec, due_time = data.recur, data.due_time
    if data.text:
        projects, users = _parse_context(conn, user)
        r = quickadd.parse(data.text, today_local(), projects, users)
        # Kürzel im Text gehen vor; die übrigen Felder sind der Rahmen (z. B. die gerade offene Liste)
        title = title or r["title"]
        due = r["due_date"] or due
        prio = r["priority"] or prio
        project_id = r["project_id"] or project_id
        assignees = r["assignee_ids"] or assignees
        rec = r["recur"] or rec
        due_time = r["due_time"] or due_time
    if not title:
        raise HTTPException(400, "Bitte einen Titel eingeben")
    check_target_project(conn, user, project_id)
    assignees = check_assignees(conn, assignees) if is_team(conn) else []
    rec, due = _check_recur(rec, due, due_time)
    now = now_utc_str()
    cur = conn.execute(
        "INSERT INTO tasks(project_id, title, notes, due_date, priority, created_by, created_at, updated_at, due_time, recur) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, title[:300], data.notes or "", due, prio or 3, user["id"], now, now, due_time, rec))
    tid = cur.lastrowid
    conn.executemany("INSERT INTO task_assignees(task_id, user_id) VALUES (?, ?)", [(tid, a) for a in assignees])
    conn.executemany("INSERT INTO task_items(task_id, title, sort) VALUES (?, ?, ?)",
                     [(tid, x.strip()[:300], i) for i, x in enumerate(data.items or []) if x.strip()])
    audit(conn, user["id"], "task_created", f"task:{tid}", title[:200] + (" (Schnelleingabe)" if "text" in fields else ""))
    return load_one(conn, user, tid)


@router.patch("/{task_id}")
def update_task(task_id: int, data: TaskUpdate, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    t = get_task(conn, user, task_id)
    changes = data.model_dump(exclude_unset=True)
    sets, params = [], []
    if changes.get("title"):
        sets.append("title = ?")
        params.append(data.title.strip())
    if "notes" in changes:
        sets.append("notes = ?")
        params.append(data.notes or "")
    if "due_date" in changes or "due_time" in changes or "recur" in changes:
        new_due = (data.due_date.isoformat() if data.due_date else None) if "due_date" in changes else t["due_date"]
        new_time = data.due_time if "due_time" in changes else t["due_time"]
        new_rec = data.recur if "recur" in changes else t["recur"]
        if "recur" in changes or "due_date" in changes:
            new_rec = new_rec.split("@")[0] if new_rec else new_rec  # Monatstag neu aus der Fälligkeit
        new_rec, new_due = _check_recur(new_rec, new_due, new_time)
        sets += ["due_date = ?", "due_time = ?", "recur = ?"]
        params += [new_due, new_time, new_rec]
        if new_due != t["due_date"] or new_time != t["due_time"]:
            sets.append("reminded_at = NULL")  # neue Zeit → neue Erinnerung
    if changes.get("priority") is not None:
        sets.append("priority = ?")
        params.append(data.priority)
    if "project_id" in changes and data.project_id != t["project_id"]:
        check_target_project(conn, user, data.project_id)
        sets.append("project_id = ?")
        params.append(data.project_id)
    if data.done is not None and bool(data.done) != bool(t["done"]):
        sets += ["done = ?", "completed_at = ?", "completed_by = ?"]
        params += [int(data.done), now_utc_str() if data.done else None, user["id"] if data.done else None]
    if data.assignee_ids is not None and is_team(conn):
        ids = check_assignees(conn, data.assignee_ids)
        conn.execute("DELETE FROM task_assignees WHERE task_id = ?", (task_id,))
        conn.executemany("INSERT INTO task_assignees(task_id, user_id) VALUES (?, ?)", [(task_id, a) for a in ids])
    if sets or data.assignee_ids is not None:
        conn.execute(f"UPDATE tasks SET {', '.join(sets + ['updated_at = ?'])} WHERE id = ?", params + [now_utc_str(), task_id])
    if data.done and not t["done"]:
        nxt = _spawn_next(conn, user, task_id)
        if nxt:
            out = load_one(conn, user, task_id)
            audit(conn, user["id"], "task_done", f"task:{task_id}", t["title"][:120] + f" – nächste am {nxt['due_date']}")
            return {**out, "next": nxt}
    if changes:
        action = ("task_done" if data.done else "task_reopened") if set(changes) == {"done"} else "task_updated"
        audit(conn, user["id"], action, f"task:{task_id}",
              t["title"][:120] + ("" if action != "task_updated" else f" – {', '.join(sorted(changes))}"))
    return load_one(conn, user, task_id)


def _check_recur(rule: Optional[str], due: Optional[str], due_time: Optional[str]):
    """Regel prüfen; Wiederholung und Uhrzeit brauchen eine Fälligkeit (Standard: heute)."""
    if rule == "":
        rule = None
    if rule and not recur.valid(rule):
        raise HTTPException(400, "Unbekannte Wiederholung (erlaubt: d:N, wd, w:N, m:N, y:N)")
    if (rule or due_time) and not due:
        due = today_local().isoformat()
    if rule:
        rule = recur.with_anchor(rule, date.fromisoformat(due)) if "@" not in rule else rule
    return rule, due


def _spawn_next(conn, user, task_id: int) -> Optional[dict]:
    """Wiederkehrende Aufgabe erledigt → nächste anlegen (nur einmal)."""
    t = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not t["recur"] or t["recur_next_id"] or not t["due_date"]:
        return None
    due = recur.next_due(date.fromisoformat(t["due_date"]), t["recur"], today_local())
    now = now_utc_str()
    cur = conn.execute(
        "INSERT INTO tasks(project_id, title, notes, due_date, priority, created_by, created_at, updated_at, due_time, recur) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (t["project_id"], t["title"], t["notes"], due.isoformat(), t["priority"], t["created_by"], now, now, t["due_time"], t["recur"]))
    nid = cur.lastrowid
    conn.execute("INSERT INTO task_assignees(task_id, user_id) SELECT ?, user_id FROM task_assignees WHERE task_id = ?", (nid, task_id))
    conn.execute("INSERT INTO task_items(task_id, title, sort) SELECT ?, title, sort FROM task_items WHERE task_id = ?", (nid, task_id))
    conn.execute("UPDATE tasks SET recur_next_id = ? WHERE id = ?", (nid, task_id))
    audit(conn, user["id"], "task_recurred", f"task:{nid}", f"{t['title'][:120]} – {recur.label(t['recur'])}, fällig {due:%d.%m.%Y}")
    return load_one(conn, user, nid)


# --- Checkliste (Unteraufgaben) -------------------------------------------------

@router.post("/{task_id}/items", status_code=201)
def add_item(task_id: int, data: ItemIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    get_task(conn, user, task_id)
    n = conn.execute("SELECT COUNT(*), COALESCE(MAX(sort), -1) FROM task_items WHERE task_id = ?", (task_id,)).fetchone()
    if n[0] >= 100:
        raise HTTPException(400, "Höchstens 100 Punkte pro Checkliste")
    conn.execute("INSERT INTO task_items(task_id, title, sort) VALUES (?, ?, ?)", (task_id, data.title.strip(), n[1] + 1))
    conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now_utc_str(), task_id))
    return load_one(conn, user, task_id)


def _item(conn, task_id: int, item_id: int):
    row = conn.execute("SELECT * FROM task_items WHERE id = ? AND task_id = ?", (item_id, task_id)).fetchone()
    if not row:
        raise HTTPException(404, "Punkt nicht gefunden")
    return row


@router.patch("/{task_id}/items/{item_id}")
def update_item(task_id: int, item_id: int, data: ItemUpdate, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    get_task(conn, user, task_id)
    _item(conn, task_id, item_id)
    if data.title:
        conn.execute("UPDATE task_items SET title = ? WHERE id = ?", (data.title.strip(), item_id))
    if data.done is not None:
        conn.execute("UPDATE task_items SET done = ? WHERE id = ?", (int(data.done), item_id))
    conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now_utc_str(), task_id))
    return load_one(conn, user, task_id)


@router.delete("/{task_id}/items/{item_id}")
def delete_item(task_id: int, item_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    get_task(conn, user, task_id)
    _item(conn, task_id, item_id)
    conn.execute("DELETE FROM task_items WHERE id = ?", (item_id,))
    return load_one(conn, user, task_id)


@router.delete("/{task_id}")
def delete_task(task_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    t = get_task(conn, user, task_id)
    if not (sees_all(conn, user) or t["created_by"] == user["id"]):
        raise HTTPException(403, "Löschen darf nur, wer die Aufgabe angelegt hat, oder der Chef")
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    audit(conn, user["id"], "task_deleted", f"task:{task_id}", t["title"][:200])
    return {"ok": True}


def priority_name(p: int) -> str:
    return PRIORITY_NAMES.get(p, "normal")
