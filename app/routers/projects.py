"""Kunden, Projekte und Aufgabenlisten."""
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..access import can_manage_project, get_project, project_filter, sees_all
from ..db import audit, get_db, is_team
from ..security import admin_user, current_user
from ..timeutil import now_utc_str, today_local

router = APIRouter(prefix="/api/v1", tags=["Projekte & Listen"])

COLOR_RE = r"^#[0-9a-fA-F]{6}$"
KINDS = {"project": "Projekt", "list": "Aufgabenliste"}


# --- Kunden (nur Chef) ----------------------------------------------------------

class CustomerIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class CustomerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    archived: Optional[bool] = None


def _customer_out(r) -> dict:
    return {"id": r["id"], "name": r["name"], "archived": bool(r["archived"])}


@router.get("/customers")
def list_customers(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Alle Kunden (für Auswahllisten). Mitarbeiter sehen nur Kunden ihrer Projekte."""
    if sees_all(conn, user):
        rows = conn.execute("SELECT * FROM customers ORDER BY archived, name COLLATE NOCASE").fetchall()
    else:
        cond, args = project_filter(conn, user)
        rows = conn.execute(f"SELECT DISTINCT c.* FROM customers c JOIN projects p ON p.customer_id = c.id WHERE {cond} "
                            "ORDER BY c.archived, c.name COLLATE NOCASE", args).fetchall()
    return [_customer_out(r) for r in rows]


@router.post("/customers", status_code=201)
def create_customer(data: CustomerIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    name = data.name.strip()
    if conn.execute("SELECT 1 FROM customers WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        raise HTTPException(409, "Diesen Kunden gibt es schon")
    cur = conn.execute("INSERT INTO customers(name, created_at) VALUES (?, ?)", (name, now_utc_str()))
    audit(conn, admin["id"], "customer_created", f"customer:{cur.lastrowid}", name)
    return _customer_out(conn.execute("SELECT * FROM customers WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.patch("/customers/{customer_id}")
def update_customer(customer_id: int, data: CustomerUpdate, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    if not conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
        raise HTTPException(404, "Kunde nicht gefunden")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name"):
        conn.execute("UPDATE customers SET name = ? WHERE id = ?", (data.name.strip(), customer_id))
    if data.archived is not None:
        conn.execute("UPDATE customers SET archived = ? WHERE id = ?", (int(data.archived), customer_id))
    if changes:
        audit(conn, admin["id"], "customer_updated", f"customer:{customer_id}", str(changes))
    return _customer_out(conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone())


# --- Projekte und Listen --------------------------------------------------------

class ProjectIn(BaseModel):
    kind: str = Field("list", description="project (mit Kunde, später für Zeiten) oder list (Aufgabenliste)")
    name: str = Field(..., min_length=1, max_length=100)
    customer_id: Optional[int] = None
    color: str = Field("#0f766e", pattern=COLOR_RE)
    member_ids: Optional[List[int]] = Field(None, description="Team-Modus: wer das Projekt sieht")


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    customer_id: Optional[int] = None
    color: Optional[str] = Field(None, pattern=COLOR_RE)
    archived: Optional[bool] = None
    member_ids: Optional[List[int]] = None


def _members(conn, project_id: int) -> List[int]:
    return [r[0] for r in conn.execute("SELECT user_id FROM project_members WHERE project_id = ? ORDER BY user_id", (project_id,))]


def project_out(conn, r, user) -> dict:
    return {
        "id": r["id"], "kind": r["kind"], "name": r["name"], "color": r["color"], "archived": bool(r["archived"]),
        "customer_id": r["customer_id"], "customer": r["customer"] if "customer" in r.keys() else None,
        "member_ids": _members(conn, r["id"]) if is_team(conn) else [],
        "can_manage": can_manage_project(conn, user, r),
        "open": r["open"] if "open" in r.keys() else None,
        "overdue": r["overdue"] if "overdue" in r.keys() else None,
    }


def _set_members(conn, project_id: int, ids: List[int]) -> None:
    ids = list(dict.fromkeys(ids))
    if ids:
        found = {r[0] for r in conn.execute(f"SELECT id FROM users WHERE id IN ({','.join('?' * len(ids))})", ids)}
        if len(found) != len(ids):
            raise HTTPException(400, "Unbekannter Benutzer in der Mitgliederliste")
    conn.execute("DELETE FROM project_members WHERE project_id = ?", (project_id,))
    conn.executemany("INSERT INTO project_members(project_id, user_id) VALUES (?, ?)", [(project_id, i) for i in ids])


def _check_customer(conn, customer_id: Optional[int]) -> None:
    if customer_id is not None and not conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
        raise HTTPException(400, "Kunde nicht gefunden")


def _load(conn, user, project_id: int):
    return conn.execute(
        "SELECT p.*, c.name AS customer FROM projects p LEFT JOIN customers c ON c.id = p.customer_id WHERE p.id = ?",
        (project_id,)).fetchone()


@router.get("/projects")
def list_projects(include_archived: bool = False, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Sichtbare Projekte und Listen mit Zahl offener und überfälliger Aufgaben."""
    from ..access import task_filter
    cond, args = project_filter(conn, user)
    tcond, targs = task_filter(conn, user)
    today = today_local().isoformat()
    rows = conn.execute(
        "SELECT p.*, c.name AS customer,"
        f" (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.done = 0 AND {tcond}) AS open,"
        f" (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.done = 0 AND t.due_date < ? AND {tcond}) AS overdue"
        " FROM projects p LEFT JOIN customers c ON c.id = p.customer_id"
        f" WHERE {cond}" + ("" if include_archived else " AND p.archived = 0") +
        " ORDER BY p.archived, p.kind, c.name IS NULL, c.name COLLATE NOCASE, p.name COLLATE NOCASE",
        targs + [today] + targs + args).fetchall()
    return [project_out(conn, r, user) for r in rows]


@router.post("/projects", status_code=201)
def create_project(data: ProjectIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    if data.kind not in KINDS:
        raise HTTPException(400, "Art muss project oder list sein")
    boss = sees_all(conn, user)
    if data.kind == "project" and not boss:
        raise HTTPException(403, "Projekte legt der Chef an – du kannst eine Aufgabenliste anlegen.")
    _check_customer(conn, data.customer_id)
    cur = conn.execute(
        "INSERT INTO projects(kind, name, customer_id, color, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (data.kind, data.name.strip(), data.customer_id if data.kind == "project" else None, data.color.lower(),
         user["id"], now_utc_str()))
    pid = cur.lastrowid
    members = list(data.member_ids or []) if boss else []
    if user["id"] not in members:
        members.append(user["id"])  # wer anlegt, ist dabei
    _set_members(conn, pid, members)
    audit(conn, user["id"], "project_created", f"project:{pid}", f"{KINDS[data.kind]} „{data.name.strip()}“")
    return project_out(conn, _load(conn, user, pid), user)


@router.patch("/projects/{project_id}")
def update_project(project_id: int, data: ProjectUpdate, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    p = get_project(conn, user, project_id)
    if not can_manage_project(conn, user, p):
        raise HTTPException(403, "Nur der Chef oder wer die Liste angelegt hat, darf sie ändern")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name"):
        conn.execute("UPDATE projects SET name = ? WHERE id = ?", (data.name.strip(), project_id))
    if "customer_id" in changes and p["kind"] == "project":
        _check_customer(conn, data.customer_id)
        conn.execute("UPDATE projects SET customer_id = ? WHERE id = ?", (data.customer_id, project_id))
    if data.color:
        conn.execute("UPDATE projects SET color = ? WHERE id = ?", (data.color.lower(), project_id))
    if data.archived is not None:
        conn.execute("UPDATE projects SET archived = ? WHERE id = ?", (int(data.archived), project_id))
    if data.member_ids is not None:
        ids = list(data.member_ids)
        if not sees_all(conn, user) and user["id"] not in ids:
            ids.append(user["id"])  # sich selbst nicht aus der eigenen Liste aussperren
        _set_members(conn, project_id, ids)
    if changes:
        audit(conn, user["id"], "project_updated", f"project:{project_id}", str(changes))
    return project_out(conn, _load(conn, user, project_id), user)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Löschen geht nur, wenn keine Aufgaben mehr darin sind – sonst archivieren."""
    p = get_project(conn, user, project_id)
    if not can_manage_project(conn, user, p):
        raise HTTPException(403, "Nur der Chef oder wer die Liste angelegt hat, darf sie löschen")
    n = conn.execute("SELECT COUNT(*) FROM tasks WHERE project_id = ?", (project_id,)).fetchone()[0]
    if n:
        raise HTTPException(400, f"Darin stehen noch {n} Aufgabe{'n' if n != 1 else ''} – bitte verschieben oder "
                                 "das Projekt archivieren.")
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    audit(conn, user["id"], "project_deleted", f"project:{project_id}", p["name"])
    return {"ok": True}


# --- Personen für Auswahllisten (Zuständige, Mitglieder) ---------------------------

@router.get("/people")
def people(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Aktive Benutzer mit Namen – für Zuständige und Mitglieder (nur Team-Modus, sonst nur man selbst)."""
    if not is_team(conn):
        return [{"id": user["id"], "username": user["username"], "display_name": user["display_name"]}]
    return [{"id": r["id"], "username": r["username"], "display_name": r["display_name"]}
            for r in conn.execute("SELECT id, username, display_name FROM users WHERE active = 1 ORDER BY display_name COLLATE NOCASE")]

