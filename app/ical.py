"""iCalendar-Export (RFC 5545) für das Kalender-Abo – z. B. auf dem iPhone unter „Kalenderabo hinzufügen“.

- Serien werden als RRULE übertragen (mit EXDATE für ausgefallene Termine), nicht als Einzeltermine.
- Zeiten mit TZID und passender VTIMEZONE (Mitteleuropa: Sommer-/Winterzeit), damit 09:00 auch nach der
  Zeitumstellung 09:00 bleibt.
- Aufgaben mit Fälligkeit erscheinen auf Wunsch als ganztägige Einträge („☐ Titel“) bzw. mit Uhrzeit als
  15-Minuten-Termin. Kalender-Apps zeigen VTODO in Abos meist nicht an – deshalb als VEVENT.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from .timeutil import now_utc, parse_utc, to_local

PRODID = "-//rinkelzz//Zeitmanagement//DE"
# Mitteleuropa (Europe/Berlin, Wien, Zürich, …): ausreichend für alle Termine ab 1996
CET_ZONES = {"Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/Amsterdam", "Europe/Brussels", "Europe/Paris",
             "Europe/Rome", "Europe/Madrid", "Europe/Luxembourg", "Europe/Copenhagen", "Europe/Oslo", "Europe/Stockholm",
             "Europe/Prague", "Europe/Warsaw", "Europe/Budapest", "Europe/Vaduz"}


def _vtimezone(tzid: str) -> List[str]:
    if tzid not in CET_ZONES:
        return []  # Kalender-Apps kennen die Olson-Namen auch ohne VTIMEZONE
    return ["BEGIN:VTIMEZONE", f"TZID:{tzid}",
            "BEGIN:DAYLIGHT", "TZOFFSETFROM:+0100", "TZOFFSETTO:+0200", "TZNAME:CEST", "DTSTART:19700329T020000",
            "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
            "BEGIN:STANDARD", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "TZNAME:CET", "DTSTART:19701025T030000",
            "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD", "END:VTIMEZONE"]


def esc(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def fold(line: str) -> str:
    """Zeilen über 75 Bytes umbrechen (Folgezeilen beginnen mit einem Leerzeichen)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def rrule(rule: str, until: Optional[str], all_day: bool, start_local: datetime) -> str:
    base = rule.split("@")[0]
    if base == "wd":
        r = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    else:
        unit, n = base.split(":")
        freq = {"d": "DAILY", "w": "WEEKLY", "m": "MONTHLY", "y": "YEARLY"}[unit]
        r = f"FREQ={freq}" + (f";INTERVAL={n}" if n != "1" else "")
        if unit == "m":
            r += f";BYMONTHDAY={rule.split('@')[1] if '@' in rule else start_local.day}"
    if until:
        u = date.fromisoformat(until)
        if all_day:
            r += f";UNTIL={u:%Y%m%d}"
        else:  # UNTIL bei Zeitangaben in UTC: Ende des letzten Tages
            end = datetime(u.year, u.month, u.day, 23, 59, 59, tzinfo=start_local.tzinfo)
            r += f";UNTIL={_utc(end)}"
    return "RRULE:" + r


def build(events: Iterable[Dict], tasks: Iterable[Dict], tzid: str, name: str, host: str,
          shifts: Iterable[Dict] = ()) -> bytes:
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{esc(name)}", f"X-WR-TIMEZONE:{tzid}", "REFRESH-INTERVAL;VALUE=DURATION:PT30M",
             "X-PUBLISHED-TTL:PT30M"] + _vtimezone(tzid)
    for e in events:
        s = to_local(parse_utc(e["start_utc"]))
        en = to_local(parse_utc(e["end_utc"]))
        ev = ["BEGIN:VEVENT", f"UID:{e['uid']}@{host}", f"DTSTAMP:{stamp}", f"SUMMARY:{esc(e['title'])}"]
        if e["all_day"]:
            ev += [f"DTSTART;VALUE=DATE:{s:%Y%m%d}", f"DTEND;VALUE=DATE:{en:%Y%m%d}"]
        else:
            ev += [f"DTSTART;TZID={tzid}:{s:%Y%m%dT%H%M%S}", f"DTEND;TZID={tzid}:{en:%Y%m%dT%H%M%S}"]
        if e["recur"]:
            ev.append(rrule(e["recur"], e["recur_until"], bool(e["all_day"]), s))
            for day in sorted(e.get("exceptions") or []):
                d = date.fromisoformat(day)
                ev.append(f"EXDATE;VALUE=DATE:{d:%Y%m%d}" if e["all_day"] else f"EXDATE;TZID={tzid}:{d:%Y%m%d}T{s:%H%M%S}")
        if e["location"]:
            ev.append(f"LOCATION:{esc(e['location'])}")
        desc = "\n".join(x for x in (e["notes"], f"Projekt: {e['project']}" if e.get("project") else "",
                                     f"Teilnehmer: {', '.join(e['attendees'])}" if e.get("attendees") else "") if x)
        if desc:
            ev.append(f"DESCRIPTION:{esc(desc)}")
        if e.get("updated_at"):
            ev.append(f"LAST-MODIFIED:{parse_utc(e['updated_at']).strftime('%Y%m%dT%H%M%SZ')}")
        if e["alarm_minutes"] is not None:
            ev += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(e['title'])}",
                   f"TRIGGER:-PT{int(e['alarm_minutes'])}M", "END:VALARM"]
        ev.append("END:VEVENT")
        lines += ev
    for t in tasks:
        d = date.fromisoformat(t["day"])
        mark = "☑" if t["done"] else "☐"
        ev = ["BEGIN:VEVENT", f"UID:task-{t['id']}@{host}", f"DTSTAMP:{stamp}",
              f"SUMMARY:{esc(mark + ' ' + t['title'])}", "TRANSP:TRANSPARENT"]
        if t["time"]:
            h, m = map(int, t["time"].split(":"))
            st = datetime(d.year, d.month, d.day, h, m)
            ev += [f"DTSTART;TZID={tzid}:{st:%Y%m%dT%H%M%S}", f"DTEND;TZID={tzid}:{st + timedelta(minutes=15):%Y%m%dT%H%M%S}"]
        else:
            ev += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
        if t.get("project"):
            ev.append(f"DESCRIPTION:{esc('Aufgabe – ' + t['project'])}")
        ev.append("END:VEVENT")
        lines += ev
    for sh in shifts:
        d = date.fromisoformat(sh["day"])
        ev = ["BEGIN:VEVENT", f"UID:shift-{sh['id']}@{host}", f"DTSTAMP:{stamp}", f"SUMMARY:{esc(sh['label'])}",
              "CATEGORIES:Dienstplan"]
        if sh["kind"] == "work":
            h, m = map(int, sh["start_time"].split(":"))
            st = datetime(d.year, d.month, d.day, h, m)
            dur = sh["minutes"] + (sh["break_minutes"] or 0)
            ev += [f"DTSTART;TZID={tzid}:{st:%Y%m%dT%H%M%S}", f"DTEND;TZID={tzid}:{st + timedelta(minutes=dur):%Y%m%dT%H%M%S}"]
        else:
            ev += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}", "TRANSP:TRANSPARENT"]
        if sh.get("note"):
            ev.append(f"DESCRIPTION:{esc(sh['note'])}")
        ev.append("END:VEVENT")
        lines += ev
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold(x) for x in lines) + "\r\n").encode("utf-8")
