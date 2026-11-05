"""Kalender: Termine anlegen, ändern, löschen (auch einzelne Termine einer Serie) und das iCal-Abo."""
import hashlib
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import calendar_core as cal
from .. import ical, recur, roster
from ..access import check_assignees, get_project
from ..config import settings
from ..db import audit, get_db, get_setting, is_team
from ..security import current_user
from ..timeutil import day_start_utc, now_utc_str, to_utc_str, today_local, tz

router = APIRouter(prefix="/api/v1", tags=["Kalender"])
feed_router = APIRouter(tags=["Kalender-Abo"])

ALARMS = (0, 5, 10, 15, 30, 60, 120, 1440, 2880)


class EventIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    start: datetime = Field(..., description="Beginn (Ortszeit); bei ganztägig zählt nur das Datum")
    end: datetime = Field(..., description="Ende (Ortszeit); bei ganztägig der letzte Tag")
    all_day: bool = False
    location: str = Field("", max_length=200)
    notes: str = Field("", max_length=5000)
    project_id: Optional[int] = None
    recur: Optional[str] = Field(None, max_length=12)
    recur_until: Optional[date] = None
    alarm_minutes: Optional[int] = Field(None, description="Erinnerung im Handy-Kalender: " + ", ".join(map(str, ALARMS)))
    attendee_ids: Optional[List[int]] = Field(None, description="Team: wer den Termin auch sieht")


class EventUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    all_day: Optional[bool] = None
    location: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = Field(None, max_length=5000)
    project_id: Optional[int] = None
    recur: Optional[str] = Field(None, max_length=12)
    recur_until: Optional[date] = None
    alarm_minutes: Optional[int] = None
    attendee_ids: Optional[List[int]] = None


def _span(start: datetime, end: datetime, all_day: bool):
    """Beginn/Ende als UTC-Text. Ganztägig: lokale Mitternacht bis Mitternacht nach dem letzten Tag."""
    if all_day:
        if end.date() < start.date():
            raise HTTPException(400, "Der letzte Tag liegt vor dem ersten")
        if (end.date() - start.date()).days > 366:
            raise HTTPException(400, "Ein Termin darf höchstens ein Jahr lang sein")
        return to_utc_str(day_start_utc(start.date())), to_utc_str(day_start_utc(end.date() + timedelta(days=1)))
    s, e = start.replace(tzinfo=None), end.replace(tzinfo=None)
    if e <= s:
        raise HTTPException(400, "Das Ende muss nach dem Beginn liegen")
    if e - s > timedelta(days=31):
        raise HTTPException(400, "Ein Termin mit Uhrzeit darf höchstens 31 Tage lang sein")
    return to_utc_str(s.replace(tzinfo=tz())), to_utc_str(e.replace(tzinfo=tz()))


def _check(conn, user, data_recur, alarm, project_id, start_day: date):
    if data_recur:
        if not recur.valid(data_recur):
            raise HTTPException(400, "Unbekannte Wiederholung (erlaubt: d:N, wd, w:N, m:N, y:N)")
        data_recur = recur.with_anchor(data_recur.split("@")[0], start_day)
    if alarm is not None and alarm not in ALARMS:
        raise HTTPException(400, "Erinnerung nur " + ", ".join(map(str, ALARMS)) + " Minuten vorher")
    if project_id is not None:
        get_project(conn, user, project_id)
    return data_recur or None


def _get(conn, user, event_id: int, edit: bool = True):
    cond, args = cal.event_filter(conn, user)
    row = conn.execute(f"SELECT e.* FROM events e WHERE e.id = ? AND {cond}", [event_id] + args).fetchone()
    if not row:
        raise HTTPException(404, "Termin nicht gefunden")
    if edit and not cal.can_edit(conn, user, row):
        raise HTTPException(403, "Ändern darf nur, wer den Termin angelegt hat, oder der Chef")
    return row


@router.get("/events")
def list_events(
    from_: date = Query(..., alias="from"),
    to: date = Query(...),
    with_tasks: bool = True,
    user=Depends(current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Termine im Zeitraum (Serien aufgelöst) und – auf Wunsch – fällige Aufgaben."""
    if to < from_ or (to - from_).days > 62:
        raise HTTPException(400, "Zeitraum ungültig (höchstens zwei Monate)")
    return {"events": cal.load_range(conn, user, from_, to),
            "tasks": cal.tasks_in_range(conn, user, from_, to) if with_tasks else [],
            "shifts": _own_shifts(conn, user, from_, to)}


def _own_shifts(conn, user, d_from: date, d_to: date):
    """Eigene Schichten – im Team nur aus veröffentlichten Wochen, allein immer."""
    return roster.shifts_for_user(conn, user["id"], d_from, d_to, boss=not is_team(conn))


@router.get("/events/{event_id}")
def get_event(event_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    out = cal.load_one(conn, user, event_id)
    if not out:
        raise HTTPException(404, "Termin nicht gefunden")
    return out


@router.post("/events", status_code=201)
def create_event(data: EventIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    s, e = _span(data.start, data.end, data.all_day)
    rule = _check(conn, user, data.recur, data.alarm_minutes, data.project_id, data.start.date())
    if data.recur_until and data.recur_until < data.start.date():
        raise HTTPException(400, "Das Ende der Serie liegt vor dem ersten Termin")
    now = now_utc_str()
    cur = conn.execute(
        "INSERT INTO events(title, start_utc, end_utc, all_day, location, notes, project_id, recur, recur_until, alarm_minutes, "
        "created_by, created_at, updated_at, uid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (data.title.strip(), s, e, int(data.all_day), data.location.strip(), data.notes, data.project_id, rule,
         data.recur_until.isoformat() if (rule and data.recur_until) else None, data.alarm_minutes, user["id"], now, now,
         secrets.token_hex(12)))
    eid = cur.lastrowid
    if is_team(conn) and data.attendee_ids:
        ids = [i for i in check_assignees(conn, data.attendee_ids) if i != user["id"]]
        conn.executemany("INSERT INTO event_attendees(event_id, user_id) VALUES (?, ?)", [(eid, i) for i in ids])
    audit(conn, user["id"], "event_created", f"event:{eid}", f"{data.title.strip()} · {data.start:%d.%m.%Y}"
          + (f" · {recur.label(rule)}" if rule else ""))
    return cal.load_one(conn, user, eid)


@router.patch("/events/{event_id}")
def update_event(event_id: int, data: EventUpdate, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Ändert den Termin bzw. die ganze Serie."""
    row = _get(conn, user, event_id)
    ch = data.model_dump(exclude_unset=True)
    cur_full = cal.load_one(conn, user, event_id)
    all_day = data.all_day if data.all_day is not None else bool(row["all_day"])
    start = data.start or datetime.fromisoformat(cur_full["series_start"])
    if data.end:
        end = data.end
    else:
        end = datetime.fromisoformat(cur_full["series_end"])
        if all_day and row["all_day"]:
            end = end - timedelta(days=1)  # gespeichert ist der Tag danach
    s, e = _span(start, end, all_day)
    rule = data.recur if "recur" in ch else row["recur"]
    rule = _check(conn, user, rule, data.alarm_minutes if "alarm_minutes" in ch else row["alarm_minutes"],
                  data.project_id if "project_id" in ch else None, start.date())
    until = (data.recur_until.isoformat() if data.recur_until else None) if "recur_until" in ch else row["recur_until"]
    fields = {
        "title": data.title.strip() if data.title else row["title"], "start_utc": s, "end_utc": e, "all_day": int(all_day),
        "location": data.location.strip() if data.location is not None else row["location"],
        "notes": data.notes if data.notes is not None else row["notes"],
        "project_id": data.project_id if "project_id" in ch else row["project_id"],
        "recur": rule, "recur_until": until if rule else None,
        "alarm_minutes": data.alarm_minutes if "alarm_minutes" in ch else row["alarm_minutes"],
        "updated_at": now_utc_str(),
    }
    conn.execute(f"UPDATE events SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", list(fields.values()) + [event_id])
    if not rule:
        conn.execute("DELETE FROM event_exceptions WHERE event_id = ?", (event_id,))
    if data.attendee_ids is not None and is_team(conn):
        ids = [i for i in check_assignees(conn, data.attendee_ids) if i != row["created_by"]]
        conn.execute("DELETE FROM event_attendees WHERE event_id = ?", (event_id,))
        conn.executemany("INSERT INTO event_attendees(event_id, user_id) VALUES (?, ?)", [(event_id, i) for i in ids])
    if ch:
        audit(conn, user["id"], "event_updated", f"event:{event_id}", f"{fields['title']} – {', '.join(sorted(ch))}")
    return cal.load_one(conn, user, event_id)


@router.delete("/events/{event_id}")
def delete_event(event_id: int, day: Optional[date] = Query(None, description="nur diesen Termin einer Serie"),
                 user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _get(conn, user, event_id)
    if day and row["recur"]:
        conn.execute("INSERT OR IGNORE INTO event_exceptions(event_id, day) VALUES (?, ?)", (event_id, day.isoformat()))
        conn.execute("UPDATE events SET updated_at = ? WHERE id = ?", (now_utc_str(), event_id))
        audit(conn, user["id"], "event_deleted", f"event:{event_id}", f"{row['title']} – nur am {day:%d.%m.%Y}")
        return {"ok": True, "series": False}
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    audit(conn, user["id"], "event_deleted", f"event:{event_id}", row["title"] + (" – ganze Serie" if row["recur"] else ""))
    return {"ok": True, "series": bool(row["recur"])}


# --- iCal-Abo -----------------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def feed_base(request: Request, conn) -> str:
    configured = get_setting(conn, "public_url") or settings.public_url
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


class FeedIn(BaseModel):
    with_tasks: bool = True


@router.get("/me/calendar-feed")
def feed_status(request: Request, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT * FROM calendar_tokens WHERE user_id = ?", (user["id"],)).fetchone()
    base = feed_base(request, conn)
    return {"active": bool(row), "with_tasks": bool(row["with_tasks"]) if row else True,
            "created_at": row["created_at"] if row else None, "last_used_at": row["last_used_at"] if row else None,
            "public": base.startswith("https://"), "base": base}


@router.post("/me/calendar-feed")
def feed_create(data: FeedIn, request: Request, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Neuen geheimen Abo-Link erzeugen. Ein alter Link hört damit sofort auf zu funktionieren."""
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO calendar_tokens(user_id, token_hash, with_tasks, created_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(user_id) DO UPDATE SET token_hash = excluded.token_hash, with_tasks = excluded.with_tasks, "
                 "created_at = excluded.created_at, last_used_at = NULL",
                 (user["id"], _hash(token), int(data.with_tasks), now_utc_str()))
    audit(conn, user["id"], "calendar_feed_created", f"user:{user['id']}", "Kalender-Abo-Link neu erzeugt")
    base = feed_base(request, conn)
    url = f"{base}/ical/{token}.ics"
    return {"url": url, "webcal": "webcal://" + url.split("://", 1)[1], "public": base.startswith("https://"),
            "hinweis": "Den Link wie ein Passwort behandeln – wer ihn kennt, sieht deine Termine."}


@router.patch("/me/calendar-feed")
def feed_update(data: FeedIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("UPDATE calendar_tokens SET with_tasks = ? WHERE user_id = ?", (int(data.with_tasks), user["id"]))
    return {"ok": True}


@router.delete("/me/calendar-feed")
def feed_delete(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("DELETE FROM calendar_tokens WHERE user_id = ?", (user["id"],))
    audit(conn, user["id"], "calendar_feed_deleted", f"user:{user['id']}", "Kalender-Abo-Link gesperrt")
    return {"ok": True}


@feed_router.get("/ical/{token}.ics", include_in_schema=False)
def feed(token: str, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Das Abo selbst – ohne Login, geschützt durch den geheimen Link. Termine ab 90 Tagen zurück."""
    if len(token) > 100:
        raise HTTPException(404, "Nicht gefunden")
    row = conn.execute("SELECT c.*, u.active FROM calendar_tokens c JOIN users u ON u.id = c.user_id WHERE c.token_hash = ?",
                       (_hash(token),)).fetchone()
    if not row or not row["active"]:
        raise HTTPException(404, "Nicht gefunden")
    user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    conn.execute("UPDATE calendar_tokens SET last_used_at = ? WHERE user_id = ?", (now_utc_str(), user["id"]))
    since = today_local() - timedelta(days=90)
    cond, args = cal.event_filter(conn, user)
    rows = conn.execute(cal.SELECT + f" WHERE {cond} AND (e.recur IS NOT NULL OR e.end_utc > ?) "
                        "AND (e.recur_until IS NULL OR e.recur_until >= ?)",
                        args + [to_utc_str(day_start_utc(since)), since.isoformat()]).fetchall()
    ids = [r["id"] for r in rows]
    exc = cal._exceptions(conn, ids)
    att = cal._attendees(conn, ids)
    names = {r[0]: r[1] for r in conn.execute("SELECT id, display_name FROM users")}
    events = [{**dict(r), "exceptions": exc.get(r["id"], set()), "attendees": [names.get(i, "?") for i in att.get(r["id"], [])]}
              for r in rows]
    tasks = cal.tasks_in_range(conn, user, since, today_local() + timedelta(days=365)) if row["with_tasks"] else []
    company = get_setting(conn, "company_name")
    name = "Zeitmanagement" + (f" · {company}" if company else "")
    shifts = _own_shifts(conn, user, since, today_local() + timedelta(days=365))
    body = ical.build(events, tasks, settings.timezone, name, request.url.hostname or "zeitmanagement", shifts)
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "no-store", "Content-Disposition": 'inline; filename="zeitmanagement.ics"'})
