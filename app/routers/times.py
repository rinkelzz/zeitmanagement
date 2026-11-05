"""Projektzeiten: Stoppuhr (Start/Stopp), Nachtragen, Ändern, Löschen.

- Gebucht wird immer auf ein Projekt (nicht auf reine Aufgabenlisten), optional auf eine Aufgabe darin.
- Jede Person hat höchstens eine laufende Stoppuhr. Wer eine neue startet, beendet die laufende automatisch.
- Buchungen derselben Person dürfen sich nicht überschneiden.
- Eigene Buchungen darf jeder ändern; der Chef (und im Einzelperson-Modus man selbst) alle.
- Abgerechnete Buchungen sind gesperrt. Nur der Chef kann die Abrechnung zurücknehmen (Auswertung).
"""
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import budget
from ..access import get_project, get_task, sees_all
from ..db import audit, get_db
from ..security import current_user
from ..timeutil import day_start_utc, local_iso, now_utc, now_utc_str, parse_utc, to_local, to_utc_str

router = APIRouter(prefix="/api/v1/times", tags=["Projektzeiten"])

MAX_HOURS = 24


class StartIn(BaseModel):
    project_id: Optional[int] = Field(None, description="Projekt; ohne Angabe das Projekt der Aufgabe bzw. des Tickets")
    task_id: Optional[int] = None
    ticket_id: Optional[int] = Field(None, description="Helpdesk-Ticket; die Zeit läuft auf dessen Projekt")
    note: str = Field("", max_length=500)
    on_site: bool = False


class StopIn(BaseModel):
    note: Optional[str] = Field(None, max_length=500)


class EntryIn(BaseModel):
    project_id: Optional[int] = None
    task_id: Optional[int] = None
    ticket_id: Optional[int] = None
    start: datetime = Field(..., description="Beginn (Ortszeit, z. B. 2026-11-05T09:00)")
    end: datetime = Field(..., description="Ende (Ortszeit)")
    note: str = Field("", max_length=500)
    on_site: bool = False
    user_id: Optional[int] = Field(None, description="nur Chef: für jemand anderen nachtragen")


class EntryUpdate(BaseModel):
    project_id: Optional[int] = None
    task_id: Optional[int] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    note: Optional[str] = Field(None, max_length=500)
    on_site: Optional[bool] = None


SELECT = ("SELECT e.*, p.name AS project, p.color AS project_color, c.name AS customer, c.id AS customer_id, "
          "t.title AS task, u.display_name AS user_name, k.year AS ticket_year, k.seq AS ticket_seq, k.title AS ticket_title "
          "FROM time_entries e "
          "JOIN projects p ON p.id = e.project_id LEFT JOIN customers c ON c.id = p.customer_id "
          "LEFT JOIN tasks t ON t.id = e.task_id JOIN users u ON u.id = e.user_id LEFT JOIN tickets k ON k.id = e.ticket_id")


def seconds_of(row, now: Optional[datetime] = None) -> int:
    end = parse_utc(row["end_utc"]) if row["end_utc"] else (now or now_utc())
    return max(0, int((end - parse_utc(row["start_utc"])).total_seconds()))


def entry_out(r) -> dict:
    s = seconds_of(r)
    return {
        "id": r["id"], "user_id": r["user_id"], "user_name": r["user_name"],
        "project_id": r["project_id"], "project": r["project"], "project_color": r["project_color"],
        "customer_id": r["customer_id"], "customer": r["customer"],
        "task_id": r["task_id"], "task": r["task"],
        "ticket_id": r["ticket_id"], "ticket": f"T-{r['ticket_year']}-{r['ticket_seq']:04d}" if r["ticket_id"] else None,
        "ticket_title": r["ticket_title"],
        "start": local_iso(r["start_utc"]), "end": local_iso(r["end_utc"]), "running": r["end_utc"] is None,
        "day": to_local(parse_utc(r["start_utc"])).date().isoformat(),
        "seconds": s, "minutes": round(s / 60), "note": r["note"], "on_site": bool(r["on_site"]),
        "billed": bool(r["billed_at"]), "billed_at": local_iso(r["billed_at"]), "billed_ref": r["billed_ref"],
    }


def _load(conn, entry_id: int):
    return conn.execute(SELECT + " WHERE e.id = ?", (entry_id,)).fetchone()


def _ticket(conn, user, ticket_id: int):
    """Ticket prüfen (Helpdesk an und Ticket sichtbar)."""
    from .. import modules
    from .tickets import _get as get_ticket
    if not modules.enabled(conn, "helpdesk"):
        raise HTTPException(400, "Das Modul „Helpdesk“ ist abgeschaltet")
    return get_ticket(conn, user, ticket_id)


def _target(conn, user, project_id: Optional[int], task_id: Optional[int], ticket_id: Optional[int] = None):
    """Projekt und Aufgabe prüfen. Liefert (project_id, task_id)."""
    if ticket_id is not None:
        k = _ticket(conn, user, ticket_id)
        if project_id is not None and project_id != k["project_id"]:
            raise HTTPException(400, "Das Ticket gehört nicht zu diesem Projekt")
        project_id, task_id = k["project_id"], None
    if task_id is not None:
        t = get_task(conn, user, task_id)
        if project_id is None:
            project_id = t["project_id"]
        if t["project_id"] != project_id:
            raise HTTPException(400, "Die Aufgabe gehört nicht zu diesem Projekt")
    if project_id is None:
        raise HTTPException(400, "Bitte ein Projekt wählen – Zeiten werden immer auf ein Projekt gebucht. "
                                 "Diese Aufgabe steht in keinem Projekt.")
    p = get_project(conn, user, project_id)
    if p["kind"] != "project":
        raise HTTPException(400, f"„{p['name']}“ ist eine Aufgabenliste – Zeiten bitte auf ein Projekt buchen.")
    if p["archived"]:
        raise HTTPException(400, "Dieses Projekt ist archiviert")
    return project_id, task_id


def _get_entry(conn, user, entry_id: int, for_change: bool = True):
    row = _load(conn, entry_id)
    if not row or (row["user_id"] != user["id"] and not sees_all(conn, user)):
        raise HTTPException(404, "Buchung nicht gefunden")
    if for_change and row["billed_at"]:
        raise HTTPException(409, "Diese Zeit ist schon abgerechnet und deshalb gesperrt"
                                 + (f" ({row['billed_ref']})" if row["billed_ref"] else "")
                                 + ". Der Chef kann die Abrechnung in der Auswertung zurücknehmen.")
    return row


def _check_span(conn, user_id: int, start_utc: str, end_utc: Optional[str], exclude_id: Optional[int] = None) -> None:
    start = parse_utc(start_utc)
    end = parse_utc(end_utc) if end_utc else now_utc()
    if end_utc and end <= start:
        raise HTTPException(400, "Das Ende muss nach dem Beginn liegen")
    if end - start > timedelta(hours=MAX_HOURS):
        raise HTTPException(400, f"Eine Buchung darf höchstens {MAX_HOURS} Stunden lang sein")
    rows = conn.execute(
        "SELECT e.id, e.start_utc, e.end_utc, p.name FROM time_entries e JOIN projects p ON p.id = e.project_id "
        "WHERE e.user_id = ? AND e.id != ? AND e.start_utc < ? AND (e.end_utc IS NULL OR e.end_utc > ?)",
        (user_id, exclude_id or 0, to_utc_str(end), start_utc)).fetchall()
    for r in rows:
        if r["end_utc"] is None and end_utc is None:
            continue  # die laufende wird beim Start beendet
        s, e = to_local(parse_utc(r["start_utc"])), to_local(parse_utc(r["end_utc"])) if r["end_utc"] else None
        raise HTTPException(409, f"Überschneidet sich mit der Buchung „{r['name']}“ am {s:%d.%m.} "
                                 f"{s:%H:%M}–{e:%H:%M}" if e else f"Überschneidet sich mit der laufenden Stoppuhr ({r['name']}, seit {s:%H:%M})")


def _running(conn, user_id: int):
    return conn.execute(SELECT + " WHERE e.user_id = ? AND e.end_utc IS NULL", (user_id,)).fetchone()


def _stop(conn, user, row, note: Optional[str] = None) -> dict:
    now = now_utc_str()
    if parse_utc(now) <= parse_utc(row["start_utc"]):  # in derselben Sekunde gestoppt
        now = to_utc_str(parse_utc(row["start_utc"]) + timedelta(seconds=1))
    if parse_utc(now) - parse_utc(row["start_utc"]) > timedelta(hours=MAX_HOURS):
        # vergessen: auf die Höchstdauer kürzen, Hinweis in der Notiz
        now = to_utc_str(parse_utc(row["start_utc"]) + timedelta(hours=MAX_HOURS))
        note = ((note if note is not None else row["note"]) + " [automatisch auf 24 h gekürzt]").strip()
    conn.execute("UPDATE time_entries SET end_utc = ?, note = COALESCE(?, note), updated_at = ?, updated_by = ? WHERE id = ?",
                 (now, note, now_utc_str(), user["id"], row["id"]))
    out = entry_out(_load(conn, row["id"]))
    audit(conn, user["id"], "time_stopped", f"time:{row['id']}", f"{row['project']} · {out['minutes']} min")
    out["budget_warning"] = budget.check(conn, row["project_id"])
    return out


@router.get("/running")
def running(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Die eigene laufende Stoppuhr (oder null)."""
    r = _running(conn, user["id"])
    return {"running": entry_out(r) if r else None}


@router.post("/start")
def start(data: StartIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Stoppuhr starten. Läuft schon eine, wird sie vorher beendet."""
    project_id, task_id = _target(conn, user, data.project_id, data.task_id, data.ticket_id)
    stopped = None
    r = _running(conn, user["id"])
    if r:
        stopped = _stop(conn, user, r)
    now = now_utc_str()
    cur = conn.execute(
        "INSERT INTO time_entries(user_id, project_id, task_id, ticket_id, start_utc, note, on_site, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user["id"], project_id, task_id, data.ticket_id, now, data.note.strip(), int(data.on_site), now))
    row = _load(conn, cur.lastrowid)
    if data.ticket_id is not None:
        _ticket_started(conn, user, data.ticket_id)
    audit(conn, user["id"], "time_started", f"time:{row['id']}", row["project"] + (f" · {row['task']}" if row["task"] else "")
          + (f" · T-{row['ticket_year']}-{row['ticket_seq']:04d}" if row["ticket_id"] else ""))
    return {"running": entry_out(row), "stopped": stopped}


def _ticket_started(conn, user, ticket_id: int) -> None:
    """Stoppuhr am neuen Ticket: es ist jetzt „in Arbeit“ und gehört dieser Person, wenn es noch niemandem gehört."""
    from .. import helpdesk as hd
    k = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if k["assignee_id"] is None:
        conn.execute("UPDATE tickets SET assignee_id = ? WHERE id = ?", (user["id"], ticket_id))
    if k["status"] in ("new", "wait"):
        conn.execute("UPDATE tickets SET status = 'open', updated_at = ? WHERE id = ?", (now_utc_str(), ticket_id))
        hd.system_message(conn, ticket_id, f"{user['display_name']} arbeitet daran (Stoppuhr gestartet)", user["id"])


@router.post("/stop")
def stop(data: StopIn = StopIn(), user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    r = _running(conn, user["id"])
    if not r:
        raise HTTPException(409, "Es läuft keine Stoppuhr")
    return {"stopped": _stop(conn, user, r, data.note.strip() if data.note is not None else None), "running": None}


@router.get("")
def list_entries(
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    user_id: Optional[int] = Query(None, description="nur Chef: Buchungen dieser Person; ohne Angabe die eigenen"),
    all_users: bool = Query(False, description="nur Chef: alle Personen"),
    user=Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Buchungen im Zeitraum (nach Beginn, Ortszeit), neueste zuerst."""
    if to < from_ or (to - from_).days > 400:
        raise HTTPException(400, "Zeitraum ungültig (höchstens etwa ein Jahr)")
    where, args = ["e.start_utc >= ?", "e.start_utc < ?"], [to_utc_str(day_start_utc(from_)), to_utc_str(day_start_utc(to + timedelta(days=1)))]
    boss = sees_all(conn, user)
    if all_users and boss:
        pass
    else:
        uid = user_id if (user_id and boss) else user["id"]
        where.append("e.user_id = ?")
        args.append(uid)
    rows = conn.execute(SELECT + " WHERE " + " AND ".join(where) + " ORDER BY e.start_utc DESC", args).fetchall()
    return [entry_out(r) for r in rows]


@router.post("", status_code=201)
def create_entry(data: EntryIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Zeit nachtragen (Beginn und Ende)."""
    uid = user["id"]
    if data.user_id and data.user_id != user["id"]:
        if not sees_all(conn, user):
            raise HTTPException(403, "Nur der Chef darf für andere nachtragen")
        if not conn.execute("SELECT 1 FROM users WHERE id = ? AND active = 1", (data.user_id,)).fetchone():
            raise HTTPException(400, "Benutzer nicht gefunden")
        uid = data.user_id
    project_id, task_id = _target(conn, user, data.project_id, data.task_id, data.ticket_id)
    s, e = to_utc_str(data.start), to_utc_str(data.end)
    _check_span(conn, uid, s, e)
    now = now_utc_str()
    cur = conn.execute(
        "INSERT INTO time_entries(user_id, project_id, task_id, ticket_id, start_utc, end_utc, note, on_site, created_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, project_id, task_id, data.ticket_id, s, e, data.note.strip(), int(data.on_site), now, user["id"]))
    row = _load(conn, cur.lastrowid)
    out = entry_out(row)
    audit(conn, user["id"], "time_added", f"time:{row['id']}",
          f"{row['project']} · {to_local(parse_utc(s)):%d.%m.%Y %H:%M}–{to_local(parse_utc(e)):%H:%M}"
          + (f" · für user:{uid}" if uid != user["id"] else ""))
    out["budget_warning"] = budget.check(conn, project_id)
    return out


@router.patch("/{entry_id}")
def update_entry(entry_id: int, data: EntryUpdate, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _get_entry(conn, user, entry_id)
    changes = data.model_dump(exclude_unset=True)
    if "project_id" in changes or "task_id" in changes:
        pid = data.project_id if "project_id" in changes else row["project_id"]
        tid = data.task_id if "task_id" in changes else (row["task_id"] if pid == row["project_id"] else None)
        pid, tid = _target(conn, user, pid, tid)
        conn.execute("UPDATE time_entries SET project_id = ?, task_id = ? WHERE id = ?", (pid, tid, entry_id))
        # Ticket aus einem anderen Projekt passt nicht mehr → Verknüpfung lösen
        conn.execute("UPDATE time_entries SET ticket_id = NULL WHERE id = ? AND ticket_id IS NOT NULL "
                     "AND ticket_id NOT IN (SELECT id FROM tickets WHERE project_id = ?)", (entry_id, pid))
    if "start" in changes or "end" in changes:
        s = to_utc_str(data.start) if data.start else row["start_utc"]
        if row["end_utc"] is None and data.end is None:
            e = None  # laufende Stoppuhr: nur der Beginn lässt sich korrigieren
        else:
            e = to_utc_str(data.end) if data.end else row["end_utc"]
        if e is None and parse_utc(s) > now_utc():
            raise HTTPException(400, "Der Beginn einer laufenden Stoppuhr kann nicht in der Zukunft liegen")
        _check_span(conn, row["user_id"], s, e, exclude_id=entry_id)
        conn.execute("UPDATE time_entries SET start_utc = ?, end_utc = ? WHERE id = ?", (s, e, entry_id))
    if data.note is not None:
        conn.execute("UPDATE time_entries SET note = ? WHERE id = ?", (data.note.strip(), entry_id))
    if data.on_site is not None:
        conn.execute("UPDATE time_entries SET on_site = ? WHERE id = ?", (int(data.on_site), entry_id))
    if changes:
        conn.execute("UPDATE time_entries SET updated_at = ?, updated_by = ? WHERE id = ?", (now_utc_str(), user["id"], entry_id))
        logged = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in changes.items()}
        audit(conn, user["id"], "time_updated", f"time:{entry_id}", f"{row['project']} · {logged}")
    new = _load(conn, entry_id)
    for pid in {row["project_id"], new["project_id"]}:
        budget.check(conn, pid)
    return entry_out(new)


@router.delete("/{entry_id}")
def delete_entry(entry_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _get_entry(conn, user, entry_id)
    out = entry_out(row)
    conn.execute("DELETE FROM time_entries WHERE id = ?", (entry_id,))
    audit(conn, user["id"], "time_deleted", f"time:{entry_id}",
          f"{row['project']} · {out['day'][8:10]}.{out['day'][5:7]}.{out['day'][:4]} · {out['minutes']} min"
          + (f" · von user:{row['user_id']}" if row["user_id"] != user["id"] else ""))
    budget.check(conn, row["project_id"])
    return {"ok": True}
