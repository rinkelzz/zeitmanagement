"""Dienstplan: Schichten einer Woche, Stunden, Besetzung, Wochenplan als PDF und Mail bei Veröffentlichung.

- Der Chef plant; Mitarbeiter sehen nur veröffentlichte Wochen (dann aber das ganze Team).
- Eine Schicht übernimmt Name, Kürzel, Farbe und Zeiten der Schichtart zum Zeitpunkt der Planung – spätere
  Änderungen an der Schichtart verändern alte Pläne nicht.
- Dienst über Mitternacht (z. B. 22:00–06:00) gehört zum Tag, an dem er beginnt.
"""
import sqlite3
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from . import logo
from .db import get_setting, is_team
from .mailer import MailError, send_mail, valid_email
from .pdf import PDF, fit
from .timeutil import now_utc, to_local, tz

WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
WD_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def minutes(start: Optional[str], end: Optional[str], brk: int = 0) -> int:
    """Arbeitsminuten einer Schicht (Ende ≤ Beginn = über Mitternacht), abzüglich Pause."""
    if not start or not end:
        return 0
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    m = (eh * 60 + em) - (sh * 60 + sm)
    if m <= 0:
        m += 1440
    return max(0, m - (brk or 0))


def span(day: str, start: str, end: str):
    """Beginn und Ende als Ortszeit (für Überschneidungen und Kalender)."""
    d = date.fromisoformat(day)
    sh, sm = map(int, start.split(":"))
    s = datetime(d.year, d.month, d.day, sh, sm, tzinfo=tz())
    e = s + timedelta(minutes=minutes(start, end))
    return s, e


def type_out(r) -> dict:
    return {"id": r["id"], "name": r["name"], "short": r["short"], "kind": r["kind"], "start_time": r["start_time"],
            "end_time": r["end_time"], "break_minutes": r["break_minutes"], "color": r["color"], "sort": r["sort"],
            "archived": bool(r["archived"]),
            "minutes": minutes(r["start_time"], r["end_time"], r["break_minutes"]) if r["kind"] == "work" else 0}


def shift_out(r) -> dict:
    return {"id": r["id"], "user_id": r["user_id"], "day": r["day"], "shift_type_id": r["shift_type_id"], "kind": r["kind"],
            "start_time": r["start_time"], "end_time": r["end_time"], "break_minutes": r["break_minutes"],
            "label": r["label"], "short": r["short"], "color": r["color"], "note": r["note"],
            "minutes": minutes(r["start_time"], r["end_time"], r["break_minutes"]) if r["kind"] == "work" else 0}


def roster_users(conn: sqlite3.Connection, user) -> List[dict]:
    if not is_team(conn):
        return [{"id": user["id"], "display_name": user["display_name"]}]
    return [{"id": r["id"], "display_name": r["display_name"]} for r in conn.execute(
        "SELECT id, display_name FROM users WHERE active = 1 AND in_roster = 1 ORDER BY display_name COLLATE NOCASE")]


def week_status(conn: sqlite3.Connection, week_start: date) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT w.*, u.display_name AS by_name FROM shift_weeks w LEFT JOIN users u ON u.id = w.published_by "
                        "WHERE w.week_start = ?", (week_start.isoformat(),)).fetchone()


def build_week(conn: sqlite3.Connection, user, week_start: date, boss: bool) -> Dict:
    days = [week_start + timedelta(days=i) for i in range(7)]
    st = week_status(conn, week_start)
    published = bool(st and st["published_at"])
    people = roster_users(conn, user)
    visible = boss or published
    rows = conn.execute("SELECT * FROM shifts WHERE day BETWEEN ? AND ? ORDER BY day, start_time IS NULL, start_time, id",
                        (days[0].isoformat(), days[6].isoformat())).fetchall() if visible else []
    shifts = [shift_out(r) for r in rows]
    ids = {p["id"] for p in people}
    # Personen mit Schichten, die nicht (mehr) im Plan stehen, trotzdem zeigen
    extra = sorted({s["user_id"] for s in shifts} - ids)
    if extra:
        for r in conn.execute(f"SELECT id, display_name FROM users WHERE id IN ({','.join('?' * len(extra))})", extra):
            people.append({"id": r["id"], "display_name": r["display_name"] + " (nicht im Plan)"})
    totals = {p["id"]: sum(s["minutes"] for s in shifts if s["user_id"] == p["id"]) for p in people}
    coverage = []
    for d in days:
        ds = [s for s in shifts if s["day"] == d.isoformat()]
        by: Dict[str, int] = {}
        for s in ds:
            if s["kind"] == "work":
                k = s["short"] or "eigene Zeit"
                by[k] = by.get(k, 0) + 1
        coverage.append({"day": d.isoformat(), "working": len({s["user_id"] for s in ds if s["kind"] == "work"}), "by_type": by})
    return {
        "week_start": week_start.isoformat(), "days": [d.isoformat() for d in days],
        "published": published, "published_at": st["published_at"] if st else None,
        "published_by": st["by_name"] if st else None, "visible": visible,
        "users": people, "shifts": shifts, "totals": totals, "coverage": coverage, "can_edit": boss,
    }


def hm(m: int) -> str:
    return f"{m // 60}:{m % 60:02d}"


def _logo(conn):
    try:
        return logo.pdf_image(conn)
    except (logo.LogoError, ValueError):
        return None


def week_pdf(conn: sqlite3.Connection, data: Dict) -> bytes:
    ws = date.fromisoformat(data["week_start"])
    we = ws + timedelta(days=6)
    kw = ws.isocalendar()[1]
    company = get_setting(conn, "company_name")
    pdf = PDF(title=f"Dienstplan KW {kw}", author=company or "Zeitmanagement", landscape=True)
    W, H = pdf.w, pdf.h
    L, R = 32, W - 32
    img = _logo(conn)
    if img:
        w, h = logo.fit_box(img["width"], img["height"], 130, 28)
        pdf.image(R - w, 22, w, h, img)
    pdf.text(L, 34, company or "Zeitmanagement", 10, True, color=(0.25, 0.25, 0.25))
    pdf.text(L, 58, f"Dienstplan KW {kw}", 17, True)
    pdf.text(L, 74, f"{ws:%d.%m.%Y} – {we:%d.%m.%Y}" + ("" if data["published"] else "   ·   Entwurf (nicht veröffentlicht)"), 9.5,
             color=(0, 0, 0) if data["published"] else (0.7, 0.2, 0.1))
    name_w, total_w = 120, 46
    col_w = (R - L - name_w - total_w) / 7
    y = 90
    pdf.rect(L, y, R - L, 22, (0.9, 0.91, 0.93))
    pdf.text(L + 4, y + 14, "Mitarbeiter", 8.5, True)
    today = to_local(now_utc()).date()
    for i in range(7):
        d = ws + timedelta(days=i)
        x = L + name_w + i * col_w
        if i > 4:
            pdf.rect(x, y, col_w, 22, (0.86, 0.87, 0.9))
        pdf.text(x + col_w / 2, y + 10, WD[i], 8, True, "center")
        pdf.text(x + col_w / 2, y + 19, f"{d:%d.%m.}", 7.5, align="center", color=(0.35, 0.35, 0.35) if d != today else (0.06, 0.46, 0.43))
    pdf.text(R - 4, y + 14, "Std.", 8.5, True, "right")
    y += 22
    by_cell: Dict[tuple, List[Dict]] = {}
    for s in data["shifts"]:
        by_cell.setdefault((s["user_id"], s["day"]), []).append(s)
    users = data["users"]
    avail = H - 70 - y
    row_h = max(22, min(40, avail / max(1, len(users))))
    for n, u in enumerate(users):
        if y + row_h > H - 60:
            pdf.text(L, H - 40, "Fortsetzung auf der nächsten Seite", 7.5, color=(0.4, 0.4, 0.4))
            pdf.new_page()
            y = 40
        if n % 2:
            pdf.rect(L, y, R - L, row_h, (0.975, 0.975, 0.98))
        pdf.text(L + 4, y + row_h / 2 + 3, fit(u["display_name"], name_w - 8, 9), 9)
        for i in range(7):
            d = (ws + timedelta(days=i)).isoformat()
            x = L + name_w + i * col_w
            cells = by_cell.get((u["id"], d), [])
            if not cells:
                continue
            h_each = (row_h - 4) / len(cells)
            for k, s in enumerate(cells):
                cy = y + 2 + k * h_each
                rgb = tuple(int(s["color"][j:j + 2], 16) / 255 for j in (1, 3, 5))
                light = tuple(0.82 + 0.18 * c for c in rgb)
                pdf.rect(x + 2, cy, col_w - 4, h_each - 1, light)
                pdf.rect(x + 2, cy, 2.5, h_each - 1, rgb)
                text = s["label"] if s["kind"] == "off" else f"{s['short'] + ' ' if s['short'] else ''}{s['start_time']}–{s['end_time']}"
                size = 7.5 if h_each >= 16 else 6.5
                pdf.text(x + 7, cy + min(h_each - 3, 10), fit(text, col_w - 12, size, True), size, True)
                if s["note"] and h_each >= 22:
                    pdf.text(x + 7, cy + 19, fit(s["note"], col_w - 12, 6.5), 6.5, color=(0.35, 0.35, 0.35))
        pdf.text(R - 4, y + row_h / 2 + 3, hm(data["totals"].get(u["id"], 0)), 9, True, "right")
        y += row_h
        pdf.line(L, y, R, y, 0.25, (0.85, 0.85, 0.85))
    # Besetzung
    y += 4
    pdf.text(L + 4, y + 10, "Besetzung", 8, True, color=(0.35, 0.35, 0.35))
    for i, c in enumerate(data["coverage"]):
        x = L + name_w + i * col_w
        pdf.text(x + 4, y + 10, f"{c['working']} im Dienst", 7, True, color=(0.35, 0.35, 0.35))
        pdf.text(x + 4, y + 19, fit(", ".join(f"{v}× {k}" for k, v in c["by_type"].items()), col_w - 6, 7), 7, color=(0.35, 0.35, 0.35))
    # Legende
    types = conn.execute("SELECT * FROM shift_types WHERE archived = 0 ORDER BY sort, name").fetchall()
    legend = "   ".join(f"{t['short']} = {t['name']}" + (f" ({t['start_time']}–{t['end_time']})" if t["kind"] == "work" else "") for t in types)
    pdf.text(L, H - 34, fit("Legende: " + legend, R - L, 7), 7, color=(0.4, 0.4, 0.4))
    pdf.text(L, H - 22, f"Erstellt am {to_local(now_utc()):%d.%m.%Y um %H:%M} Uhr · Zeitmanagement", 7, color=(0.4, 0.4, 0.4))
    return pdf.output()


def notify(conn: sqlite3.Connection, data: Dict) -> Dict:
    """Nach dem Veröffentlichen: jede Person mit E-Mail bekommt ihre Schichten der Woche."""
    ws = date.fromisoformat(data["week_start"])
    kw = ws.isocalendar()[1]
    company = get_setting(conn, "company_name")
    sent, failed = 0, []
    for u in data["users"]:
        row = conn.execute("SELECT * FROM users WHERE id = ? AND active = 1", (u["id"],)).fetchone()
        if not row or not row["email"] or not valid_email(row["email"]):
            continue
        mine = [s for s in data["shifts"] if s["user_id"] == u["id"]]
        lines = []
        for s in mine:
            d = date.fromisoformat(s["day"])
            what = s["label"] if s["kind"] == "off" else f"{s['label']} {s['start_time']}–{s['end_time']}"
            lines.append(f"- {WD_LONG[d.weekday()]}, {d:%d.%m.}: {what}" + (f" ({s['note']})" if s["note"] else ""))
        body = "\n".join([f"Hallo {row['display_name']},", "", f"der Dienstplan für KW {kw} ({ws:%d.%m.}–{ws + timedelta(days=6):%d.%m.%Y}) "
                          "ist veröffentlicht.", ""] + (lines or ["Du bist in dieser Woche nicht eingeplant."]) +
                         ["", f"Summe: {hm(data['totals'].get(u['id'], 0))} Stunden", "", "–", "Zeitmanagement" + (f" · {company}" if company else "")])
        try:
            send_mail(conn, row["email"], f"Dienstplan KW {kw}", body)
            sent += 1
        except MailError as e:
            failed.append(f"{row['display_name']}: {e}")
    return {"sent": sent, "failed": failed}


def shifts_for_user(conn: sqlite3.Connection, user_id: int, d_from: date, d_to: date, boss: bool) -> List[dict]:
    """Eigene Schichten im Zeitraum (für Kalender und Abo) – Mitarbeiter nur aus veröffentlichten Wochen."""
    rows = conn.execute("SELECT * FROM shifts WHERE user_id = ? AND day BETWEEN ? AND ? ORDER BY day, start_time",
                        (user_id, d_from.isoformat(), d_to.isoformat())).fetchall()
    if boss:
        return [shift_out(r) for r in rows]
    published = {r[0] for r in conn.execute("SELECT week_start FROM shift_weeks WHERE published_at IS NOT NULL")}
    return [shift_out(r) for r in rows if monday(date.fromisoformat(r["day"])).isoformat() in published]
