"""Dienstplan: Schichtarten, Schichten, Woche kopieren, veröffentlichen, PDF."""
import sqlite3
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import roster
from ..access import sees_all
from ..db import audit, get_db
from ..security import admin_user, current_user
from ..timeutil import now_utc_str

router = APIRouter(prefix="/api/v1", tags=["Dienstplan"])

TIME = r"^([01]\d|2[0-3]):[0-5]\d$"
COLOR = r"^#[0-9a-fA-F]{6}$"


class TypeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    short: str = Field(..., min_length=1, max_length=3)
    kind: str = Field("work", description="work (Dienst) oder off (Abwesenheit)")
    start_time: Optional[str] = Field(None, pattern=TIME)
    end_time: Optional[str] = Field(None, pattern=TIME)
    break_minutes: int = Field(0, ge=0, le=240)
    color: str = Field("#0f766e", pattern=COLOR)
    sort: int = Field(0, ge=0, le=999)


class TypeUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=40)
    short: Optional[str] = Field(None, min_length=1, max_length=3)
    start_time: Optional[str] = Field(None, pattern=TIME)
    end_time: Optional[str] = Field(None, pattern=TIME)
    break_minutes: Optional[int] = Field(None, ge=0, le=240)
    color: Optional[str] = Field(None, pattern=COLOR)
    sort: Optional[int] = Field(None, ge=0, le=999)
    archived: Optional[bool] = None


class ShiftIn(BaseModel):
    user_id: int
    day: date
    shift_type_id: Optional[int] = Field(None, description="Schichtart – oder eigene Zeiten ohne Schichtart")
    start_time: Optional[str] = Field(None, pattern=TIME)
    end_time: Optional[str] = Field(None, pattern=TIME)
    break_minutes: Optional[int] = Field(None, ge=0, le=240)
    note: str = Field("", max_length=200)
    replace: bool = Field(False, description="vorhandene Schichten dieser Person an diesem Tag ersetzen")


class ShiftUpdate(BaseModel):
    shift_type_id: Optional[int] = None
    start_time: Optional[str] = Field(None, pattern=TIME)
    end_time: Optional[str] = Field(None, pattern=TIME)
    break_minutes: Optional[int] = Field(None, ge=0, le=240)
    note: Optional[str] = Field(None, max_length=200)


class CopyIn(BaseModel):
    from_week: date
    to_week: date
    replace: bool = Field(True, description="Zielwoche vorher leeren")


class PublishIn(BaseModel):
    week: date
    notify: bool = Field(True, description="Mitarbeiter per Mail informieren")


# --- Schichtarten ----------------------------------------------------------------

def _type(conn, type_id: int):
    r = conn.execute("SELECT * FROM shift_types WHERE id = ?", (type_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Schichtart nicht gefunden")
    return r


@router.get("/shift-types")
def list_types(include_archived: bool = False, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute("SELECT * FROM shift_types" + ("" if include_archived else " WHERE archived = 0") +
                        " ORDER BY archived, sort, name").fetchall()
    return [roster.type_out(r) for r in rows]


@router.post("/shift-types", status_code=201)
def create_type(data: TypeIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    if data.kind not in ("work", "off"):
        raise HTTPException(400, "Art muss work oder off sein")
    if data.kind == "work" and not (data.start_time and data.end_time):
        raise HTTPException(400, "Ein Dienst braucht Beginn und Ende")
    cur = conn.execute(
        "INSERT INTO shift_types(name, short, kind, start_time, end_time, break_minutes, color, sort, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (data.name.strip(), data.short.strip(), data.kind, data.start_time if data.kind == "work" else None,
         data.end_time if data.kind == "work" else None, data.break_minutes if data.kind == "work" else 0,
         data.color.lower(), data.sort, now_utc_str()))
    audit(conn, admin["id"], "shift_type_saved", f"shift_type:{cur.lastrowid}", f"{data.name.strip()} angelegt")
    return roster.type_out(_type(conn, cur.lastrowid))


@router.patch("/shift-types/{type_id}")
def update_type(type_id: int, data: TypeUpdate, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    t = _type(conn, type_id)
    ch = data.model_dump(exclude_unset=True)
    if t["kind"] == "off":
        for k in ("start_time", "end_time", "break_minutes"):
            ch.pop(k, None)
    if "archived" in ch:
        ch["archived"] = int(ch["archived"])
    if "color" in ch and ch["color"]:
        ch["color"] = ch["color"].lower()
    ch = {k: (v.strip() if isinstance(v, str) else v) for k, v in ch.items() if v is not None}
    if ch:
        conn.execute(f"UPDATE shift_types SET {', '.join(k + ' = ?' for k in ch)} WHERE id = ?", list(ch.values()) + [type_id])
        audit(conn, admin["id"], "shift_type_saved", f"shift_type:{type_id}", f"{t['name']} – {', '.join(sorted(ch))}")
    return roster.type_out(_type(conn, type_id))


# --- Woche ------------------------------------------------------------------------

def _monday(d: date) -> date:
    return roster.monday(d)


@router.get("/roster")
def get_week(week: date = Query(..., description="ein beliebiger Tag der Woche"), user=Depends(current_user),
             conn: sqlite3.Connection = Depends(get_db)):
    data = roster.build_week(conn, user, _monday(week), sees_all(conn, user))
    data["types"] = list_types(False, user, conn)
    return data


def _check_user(conn, user_id: int):
    if not conn.execute("SELECT 1 FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone():
        raise HTTPException(400, "Mitarbeiter nicht gefunden oder deaktiviert")


def _values(conn, type_id, start, end, brk):
    """Werte einer Schicht aus Schichtart und/oder eigenen Zeiten."""
    if type_id is not None:
        t = _type(conn, type_id)
        if t["archived"]:
            raise HTTPException(400, "Diese Schichtart ist archiviert")
        if t["kind"] == "off":
            return {"shift_type_id": t["id"], "kind": "off", "start_time": None, "end_time": None, "break_minutes": 0,
                    "label": t["name"], "short": t["short"], "color": t["color"]}
        return {"shift_type_id": t["id"], "kind": "work", "start_time": start or t["start_time"], "end_time": end or t["end_time"],
                "break_minutes": t["break_minutes"] if brk is None else brk, "label": t["name"], "short": t["short"], "color": t["color"]}
    if not (start and end):
        raise HTTPException(400, "Bitte eine Schichtart wählen oder Beginn und Ende angeben")
    return {"shift_type_id": None, "kind": "work", "start_time": start, "end_time": end, "break_minutes": brk or 0,
            "label": f"{start}–{end}", "short": "", "color": "#64748b"}


def _overlap(conn, user_id: int, day: str, v: dict, exclude: Optional[int] = None) -> None:
    if v["kind"] != "work":
        return
    s, e = roster.span(day, v["start_time"], v["end_time"])
    d = date.fromisoformat(day)
    rows = conn.execute("SELECT * FROM shifts WHERE user_id = ? AND kind = 'work' AND day BETWEEN ? AND ? AND id != ?",
                        (user_id, (d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat(), exclude or 0)).fetchall()
    for r in rows:
        rs, re_ = roster.span(r["day"], r["start_time"], r["end_time"])
        if rs < e and re_ > s:
            raise HTTPException(409, f"Überschneidet sich mit {r['label']} am {date.fromisoformat(r['day']):%d.%m.} "
                                     f"({r['start_time']}–{r['end_time']})")


@router.post("/shifts", status_code=201)
def create_shift(data: ShiftIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    _check_user(conn, data.user_id)
    v = _values(conn, data.shift_type_id, data.start_time, data.end_time, data.break_minutes)
    day = data.day.isoformat()
    if data.replace:
        conn.execute("DELETE FROM shifts WHERE user_id = ? AND day = ?", (data.user_id, day))
    elif v["kind"] == "off" or conn.execute("SELECT 1 FROM shifts WHERE user_id = ? AND day = ? AND kind = 'off'",
                                            (data.user_id, day)).fetchone():
        if conn.execute("SELECT 1 FROM shifts WHERE user_id = ? AND day = ?", (data.user_id, day)).fetchone():
            raise HTTPException(409, "An diesem Tag ist schon etwas eingetragen – Abwesenheit und Dienst schließen sich aus")
    _overlap(conn, data.user_id, day, v)
    cur = conn.execute(
        "INSERT INTO shifts(user_id, day, shift_type_id, kind, start_time, end_time, break_minutes, label, short, color, note, "
        "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (data.user_id, day, v["shift_type_id"], v["kind"], v["start_time"], v["end_time"], v["break_minutes"], v["label"],
         v["short"], v["color"], data.note.strip(), admin["id"], now_utc_str(), now_utc_str()))
    audit(conn, admin["id"], "shift_saved", f"user:{data.user_id}", f"{v['label']} am {data.day:%d.%m.%Y}")
    return roster.shift_out(conn.execute("SELECT * FROM shifts WHERE id = ?", (cur.lastrowid,)).fetchone())


def _shift(conn, shift_id: int):
    r = conn.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Schicht nicht gefunden")
    return r


@router.patch("/shifts/{shift_id}")
def update_shift(shift_id: int, data: ShiftUpdate, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    r = _shift(conn, shift_id)
    ch = data.model_dump(exclude_unset=True)
    type_id = data.shift_type_id if "shift_type_id" in ch else r["shift_type_id"]
    if type_id is not None and "shift_type_id" not in ch and not conn.execute("SELECT 1 FROM shift_types WHERE id = ?", (type_id,)).fetchone():
        type_id = None
    start = data.start_time if "start_time" in ch else (None if "shift_type_id" in ch else r["start_time"])
    end = data.end_time if "end_time" in ch else (None if "shift_type_id" in ch else r["end_time"])
    brk = data.break_minutes if "break_minutes" in ch else (None if "shift_type_id" in ch else r["break_minutes"])
    v = _values(conn, type_id, start, end, brk)
    if type_id is not None and "shift_type_id" not in ch:  # Bezeichnung der Schicht so lassen
        v.update(label=r["label"], short=r["short"], color=r["color"])
    _overlap(conn, r["user_id"], r["day"], v, exclude=shift_id)
    conn.execute("UPDATE shifts SET shift_type_id = ?, kind = ?, start_time = ?, end_time = ?, break_minutes = ?, label = ?, "
                 "short = ?, color = ?, note = ?, updated_at = ? WHERE id = ?",
                 (v["shift_type_id"], v["kind"], v["start_time"], v["end_time"], v["break_minutes"], v["label"], v["short"],
                  v["color"], (data.note.strip() if data.note is not None else r["note"]), now_utc_str(), shift_id))
    audit(conn, admin["id"], "shift_saved", f"user:{r['user_id']}", f"{v['label']} am {date.fromisoformat(r['day']):%d.%m.%Y} geändert")
    return roster.shift_out(_shift(conn, shift_id))


@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: int, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    r = _shift(conn, shift_id)
    conn.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))
    audit(conn, admin["id"], "shift_deleted", f"user:{r['user_id']}", f"{r['label']} am {date.fromisoformat(r['day']):%d.%m.%Y}")
    return {"ok": True}


@router.post("/roster/copy")
def copy_week(data: CopyIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Alle Schichten einer Woche in eine andere übernehmen (z. B. „Vorwoche übernehmen“)."""
    src, dst = _monday(data.from_week), _monday(data.to_week)
    if src == dst:
        raise HTTPException(400, "Quelle und Ziel sind dieselbe Woche")
    if data.replace:
        conn.execute("DELETE FROM shifts WHERE day BETWEEN ? AND ?", (dst.isoformat(), (dst + timedelta(days=6)).isoformat()))
    rows = conn.execute("SELECT s.* FROM shifts s JOIN users u ON u.id = s.user_id WHERE u.active = 1 AND s.day BETWEEN ? AND ?",
                        (src.isoformat(), (src + timedelta(days=6)).isoformat())).fetchall()
    shift = (dst - src).days
    n = skipped = 0
    for r in rows:
        day = (date.fromisoformat(r["day"]) + timedelta(days=shift)).isoformat()
        v = dict(r)
        try:
            _overlap(conn, r["user_id"], day, v)
        except HTTPException:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO shifts(user_id, day, shift_type_id, kind, start_time, end_time, break_minutes, label, short, color, note, "
            "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (r["user_id"], day, r["shift_type_id"], r["kind"], r["start_time"], r["end_time"], r["break_minutes"], r["label"],
             r["short"], r["color"], r["note"], admin["id"], now_utc_str(), now_utc_str()))
        n += 1
    audit(conn, admin["id"], "roster_copied", "roster", f"KW {src.isocalendar()[1]} → KW {dst.isocalendar()[1]}: {n} Schichten")
    return {"copied": n, "skipped": skipped}


@router.post("/roster/publish")
def publish(data: PublishIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    ws = _monday(data.week)
    conn.execute("INSERT INTO shift_weeks(week_start, published_at, published_by) VALUES (?, ?, ?) "
                 "ON CONFLICT(week_start) DO UPDATE SET published_at = excluded.published_at, published_by = excluded.published_by",
                 (ws.isoformat(), now_utc_str(), admin["id"]))
    result = {"sent": 0, "failed": []}
    if data.notify:
        result = roster.notify(conn, roster.build_week(conn, admin, ws, True))
    audit(conn, admin["id"], "roster_published", "roster", f"KW {ws.isocalendar()[1]}" + (f", {result['sent']} Mails" if data.notify else ""))
    return {"ok": True, **result}


@router.post("/roster/unpublish")
def unpublish(data: PublishIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    ws = _monday(data.week)
    conn.execute("UPDATE shift_weeks SET published_at = NULL, published_by = NULL WHERE week_start = ?", (ws.isoformat(),))
    audit(conn, admin["id"], "roster_unpublished", "roster", f"KW {ws.isocalendar()[1]}")
    return {"ok": True}


@router.get("/roster/pdf")
def week_pdf(week: date, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    ws = _monday(week)
    data = roster.build_week(conn, user, ws, sees_all(conn, user))
    if not data["visible"]:
        raise HTTPException(404, "Diese Woche ist noch nicht veröffentlicht")
    body = roster.week_pdf(conn, data)
    return Response(body, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="dienstplan_kw{ws.isocalendar()[1]:02d}_{ws.year}.pdf"',
                             "Cache-Control": "no-store"})


class RosterUserIn(BaseModel):
    in_roster: bool


@router.patch("/roster/users/{user_id}")
def set_in_roster(user_id: int, data: RosterUserIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Person im Dienstplan anzeigen oder ausblenden (z. B. der Chef selbst)."""
    if not conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
        raise HTTPException(404, "Benutzer nicht gefunden")
    conn.execute("UPDATE users SET in_roster = ? WHERE id = ?", (int(data.in_roster), user_id))
    audit(conn, admin["id"], "user_updated", f"user:{user_id}", f"Dienstplan: {'anzeigen' if data.in_roster else 'ausblenden'}")
    return {"ok": True}
