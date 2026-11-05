"""Kalender: Termine laden, Serien in einzelne Termine auflösen, Sichtbarkeit.

Sichtbar ist ein Termin für die Person, die ihn angelegt hat, für alle Teilnehmer und für den Chef
(im Einzelperson-Modus sowieso für alle). Ändern dürfen nur die anlegende Person und der Chef.

Serien nutzen dieselben Regeln wie wiederkehrende Aufgaben (recur.py). Uhrzeiten bleiben über die
Sommer-/Winterzeit hinweg gleich (09:00 bleibt 09:00), weil in Ortszeit gerechnet wird.
"""
import sqlite3
from datetime import date, datetime, timedelta
from typing import Dict, Iterator, List, Optional, Tuple

from . import recur
from .access import sees_all, task_filter
from .timeutil import day_start_utc, parse_utc, to_local, to_utc_str, tz

MAX_STEPS = 20000


def event_filter(conn: sqlite3.Connection, user, alias: str = "e") -> Tuple[str, list]:
    if sees_all(conn, user):
        return "1 = 1", []
    return (f"({alias}.created_by = ? OR {alias}.id IN (SELECT event_id FROM event_attendees WHERE user_id = ?))",
            [user["id"], user["id"]])


def can_edit(conn: sqlite3.Connection, user, row) -> bool:
    return sees_all(conn, user) or row["created_by"] == user["id"]


SELECT = ("SELECT e.*, p.name AS project, p.color AS project_color, u.display_name AS owner "
          "FROM events e LEFT JOIN projects p ON p.id = e.project_id LEFT JOIN users u ON u.id = e.created_by")


def occurrences(row, d_from: date, d_to: date, exceptions=frozenset()) -> Iterator[Tuple[datetime, datetime]]:
    """Alle Vorkommen eines Termins, die den Zeitraum [d_from, d_to] berühren (lokale Zeiten)."""
    s = to_local(parse_utc(row["start_utc"]))
    e = to_local(parse_utc(row["end_utc"]))
    win_start = datetime(d_from.year, d_from.month, d_from.day, tzinfo=tz())
    win_end = datetime(d_to.year, d_to.month, d_to.day, tzinfo=tz()) + timedelta(days=1)
    if not row["recur"]:
        if s < win_end and e > win_start:
            yield s, e
        return
    days = (e.date() - s.date()).days
    dur = e - s
    until = date.fromisoformat(row["recur_until"]) if row["recur_until"] else None
    d = s.date()
    anchor = s.day
    for _ in range(MAX_STEPS):
        if d > d_to or (until and d > until):
            return
        if d.isoformat() not in exceptions:
            if row["all_day"]:
                os_ = datetime(d.year, d.month, d.day, tzinfo=tz())
                oe = os_ + timedelta(days=days)
                oe = datetime(oe.year, oe.month, oe.day, tzinfo=tz())
            else:
                os_ = datetime(d.year, d.month, d.day, s.hour, s.minute, tzinfo=tz())
                oe = os_ + dur
            if os_ < win_end and oe > win_start:
                yield os_, oe
        d = recur.step(d, row["recur"], anchor)


def _attendees(conn, ids: List[int]) -> Dict[int, List[int]]:
    out: Dict[int, List[int]] = {}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for r in conn.execute(f"SELECT event_id, user_id FROM event_attendees WHERE event_id IN ({','.join('?' * len(part))})", part):
            out.setdefault(r[0], []).append(r[1])
    return out


def _exceptions(conn, ids: List[int]) -> Dict[int, set]:
    out: Dict[int, set] = {}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for r in conn.execute(f"SELECT event_id, day FROM event_exceptions WHERE event_id IN ({','.join('?' * len(part))})", part):
            out.setdefault(r[0], set()).add(r[1])
    return out


def event_out(conn, user, row, names: Dict[int, str], att: List[int], occ: Optional[Tuple[datetime, datetime]] = None) -> dict:
    s, e = occ or (to_local(parse_utc(row["start_utc"])), to_local(parse_utc(row["end_utc"])))
    last_day = (e - timedelta(days=1)).date() if row["all_day"] else e.date()
    return {
        "id": row["id"], "title": row["title"], "all_day": bool(row["all_day"]),
        "start": s.isoformat(), "end": e.isoformat(), "day": s.date().isoformat(), "last_day": last_day.isoformat(),
        "series_start": to_local(parse_utc(row["start_utc"])).isoformat(),
        "series_end": to_local(parse_utc(row["end_utc"])).isoformat(),
        "location": row["location"], "notes": row["notes"],
        "project_id": row["project_id"], "project": row["project"], "color": row["project_color"] or "#2563eb",
        "recur": row["recur"], "recur_label": recur.label(row["recur"]), "recur_until": row["recur_until"],
        "alarm_minutes": row["alarm_minutes"],
        "attendee_ids": sorted(att, key=lambda i: names.get(i, "")), "attendees": [names.get(i, "?") for i in sorted(att, key=lambda i: names.get(i, ""))],
        "created_by": row["created_by"], "owner": row["owner"],
        "can_edit": can_edit(conn, user, row), "uid": row["uid"],
    }


def load_range(conn: sqlite3.Connection, user, d_from: date, d_to: date) -> List[dict]:
    cond, args = event_filter(conn, user)
    win_start = to_utc_str(day_start_utc(d_from))
    win_end = to_utc_str(day_start_utc(d_to + timedelta(days=1)))
    rows = conn.execute(SELECT + f" WHERE {cond} AND e.start_utc < ? AND (e.recur IS NOT NULL OR e.end_utc > ?)",
                        args + [win_end, win_start]).fetchall()
    ids = [r["id"] for r in rows]
    att, exc = _attendees(conn, ids), _exceptions(conn, ids)
    names = {r[0]: r[1] for r in conn.execute("SELECT id, display_name FROM users")}
    out = []
    for r in rows:
        for occ in occurrences(r, d_from, d_to, exc.get(r["id"], set())):
            out.append(event_out(conn, user, r, names, att.get(r["id"], []), occ))
    out.sort(key=lambda x: (not x["all_day"], x["start"], x["title"].lower()))
    return out


def load_one(conn: sqlite3.Connection, user, event_id: int) -> Optional[dict]:
    cond, args = event_filter(conn, user)
    r = conn.execute(SELECT + f" WHERE e.id = ? AND {cond}", [event_id] + args).fetchone()
    if not r:
        return None
    names = {x[0]: x[1] for x in conn.execute("SELECT id, display_name FROM users")}
    return event_out(conn, user, r, names, _attendees(conn, [r["id"]]).get(r["id"], []))


def tasks_in_range(conn: sqlite3.Connection, user, d_from: date, d_to: date) -> List[dict]:
    """Aufgaben mit Fälligkeit im Zeitraum (für die Kalenderansicht)."""
    cond, args = task_filter(conn, user)
    sql = ("SELECT t.id, t.title, t.due_date, t.due_time, t.done, t.priority, p.name AS project, p.color AS color "
           f"FROM tasks t LEFT JOIN projects p ON p.id = t.project_id WHERE {cond} AND t.due_date BETWEEN ? AND ?")
    params = list(args) + [d_from.isoformat(), d_to.isoformat()]
    rows = conn.execute(sql + " ORDER BY t.due_date, t.due_time IS NULL, t.due_time, t.priority", params).fetchall()
    return [{"id": r["id"], "title": r["title"], "day": r["due_date"], "time": r["due_time"], "done": bool(r["done"]),
             "priority": r["priority"], "project": r["project"], "color": r["color"] or "#64748b"} for r in rows]
