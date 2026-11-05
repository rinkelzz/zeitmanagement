"""Auswertung der Projektzeiten: Daten sammeln, gruppieren und als PDF, Excel oder CSV ausgeben.

Gezählt werden nur abgeschlossene Buchungen (eine laufende Stoppuhr erst, wenn sie gestoppt ist).
Ein Mitarbeiter sieht im Team-Modus nur seine eigenen Buchungen; der Chef alle.
"""
import csv
import io
import sqlite3
from collections import OrderedDict
from datetime import date, timedelta
from typing import Dict, Optional

from . import csvsafe, logo
from .access import sees_all
from .db import get_setting, is_team
from .pdf import A4_H, A4_W, PDF, fit
from .timeutil import day_start_utc, now_utc, parse_utc, to_local, to_utc_str

GROUPS = {
    "customer": "Kunde → Projekt",
    "project": "Projekt",
    "task": "Projekt → Aufgabe",
    "user": "Mitarbeiter → Projekt",
    "day": "Tag → Projekt",
}
WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


class ReportError(ValueError):
    pass


def hm(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


def hours_dec(minutes: int) -> str:
    return f"{minutes / 60:.2f}".replace(".", ",")


def build(conn: sqlite3.Connection, user, d_from: date, d_to: date, customer_id: Optional[int] = None,
          project_id: Optional[int] = None, user_id: Optional[int] = None, group: str = "customer") -> Dict:
    if d_to < d_from or (d_to - d_from).days > 1100:
        raise ReportError("Zeitraum ungültig (höchstens drei Jahre)")
    if group not in GROUPS:
        raise ReportError("Unbekannte Gruppierung")
    team = is_team(conn)
    boss = sees_all(conn, user)
    if group == "user" and not (team and boss):
        group = "customer"
    where = ["e.end_utc IS NOT NULL", "e.start_utc >= ?", "e.start_utc < ?"]
    args: list = [to_utc_str(day_start_utc(d_from)), to_utc_str(day_start_utc(d_to + timedelta(days=1)))]
    if not boss:
        user_id = user["id"]  # Mitarbeiter: nur eigene Zeiten
    if user_id:
        where.append("e.user_id = ?")
        args.append(user_id)
    if project_id:
        where.append("e.project_id = ?")
        args.append(project_id)
    if customer_id == 0:
        where.append("p.customer_id IS NULL")
    elif customer_id:
        where.append("p.customer_id = ?")
        args.append(customer_id)
    rows = conn.execute(
        "SELECT e.*, p.name AS project, p.color AS project_color, c.name AS customer, t.title AS task, u.display_name AS user_name "
        "FROM time_entries e JOIN projects p ON p.id = e.project_id LEFT JOIN customers c ON c.id = p.customer_id "
        "LEFT JOIN tasks t ON t.id = e.task_id JOIN users u ON u.id = e.user_id "
        f"WHERE {' AND '.join(where)} ORDER BY e.start_utc", args).fetchall()

    entries = []
    for r in rows:
        s, e = to_local(parse_utc(r["start_utc"])), to_local(parse_utc(r["end_utc"]))
        minutes = round((e - s).total_seconds() / 60)
        entries.append({
            "id": r["id"], "day": s.date().isoformat(), "start": s.strftime("%H:%M"), "end": e.strftime("%H:%M"),
            "minutes": minutes, "user": r["user_name"], "user_id": r["user_id"],
            "customer": r["customer"] or "Ohne Kunde", "project": r["project"], "project_id": r["project_id"],
            "project_color": r["project_color"], "task": r["task"] or "", "note": r["note"], "on_site": bool(r["on_site"]),
        })

    def key1(x):
        return {"customer": x["customer"], "project": x["project"], "task": x["project"],
                "user": x["user"], "day": x["day"]}[group]

    def key2(x):
        return {"customer": x["project"], "project": None, "task": x["task"] or "(ohne Aufgabe)",
                "user": x["project"], "day": x["project"]}[group]

    groups: "OrderedDict[str, Dict]" = OrderedDict()
    for x in sorted(entries, key=lambda x: (key1(x) if group == "day" else key1(x).lower(), x["day"], x["start"])):
        g = groups.setdefault(key1(x), {"label": key1(x), "minutes": 0, "count": 0, "users": set(), "children": OrderedDict()})
        g["minutes"] += x["minutes"]
        g["count"] += 1
        g["users"].add(x["user"])
        k2 = key2(x)
        if k2 is not None:
            c = g["children"].setdefault(k2, {"label": k2, "minutes": 0, "count": 0, "users": set()})
            c["minutes"] += x["minutes"]
            c["count"] += 1
            c["users"].add(x["user"])
    if group != "day":  # größte zuerst – Tage bleiben chronologisch
        groups = OrderedDict(sorted(groups.items(), key=lambda kv: -kv[1]["minutes"]))
    total = sum(x["minutes"] for x in entries)
    out_groups = []
    for g in groups.values():
        kids = sorted(g["children"].values(), key=lambda c: -c["minutes"])
        out_groups.append({
            "label": _day_label(g["label"]) if group == "day" else g["label"], "minutes": g["minutes"], "count": g["count"],
            "users": sorted(g["users"]),
            "children": [{"label": c["label"], "minutes": c["minutes"], "count": c["count"], "users": sorted(c["users"])} for c in kids],
        })

    filters = []
    if customer_id is not None:
        name = "Ohne Kunde" if customer_id == 0 else (conn.execute("SELECT name FROM customers WHERE id = ?", (customer_id,)).fetchone() or ["?"])[0]
        filters.append(f"Kunde: {name}")
    if project_id:
        filters.append(f"Projekt: {(conn.execute('SELECT name FROM projects WHERE id = ?', (project_id,)).fetchone() or ['?'])[0]}")
    if user_id and team:
        filters.append(f"Mitarbeiter: {(conn.execute('SELECT display_name FROM users WHERE id = ?', (user_id,)).fetchone() or ['?'])[0]}")
    return {
        "from": d_from.isoformat(), "to": d_to.isoformat(), "group": group, "group_label": GROUPS[group],
        "team": team, "show_users": team and boss and not user_id,
        "total_minutes": total, "total": hm(total), "count": len(entries),
        "customers": len({x["customer"] for x in entries}), "projects": len({x["project_id"] for x in entries}),
        "people": len({x["user_id"] for x in entries}),
        "groups": out_groups, "entries": entries, "filters": filters,
        "company": get_setting(conn, "company_name"), "created": to_local(now_utc()),
    }


def _day_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{WD[d.weekday()]}, {d:%d.%m.%Y}"


def _de(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


def period_text(data: Dict) -> str:
    return f"{_de(data['from'])} – {_de(data['to'])}"


def file_base(data: Dict) -> str:
    return f"zeitnachweis_{data['from']}_{data['to']}"


# --- CSV ----------------------------------------------------------------------

def to_csv(data: Dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    head = ["Datum", "Von", "Bis", "Dauer (h:mm)", "Stunden"] + (["Mitarbeiter"] if data["team"] else []) + \
           ["Kunde", "Projekt", "Aufgabe", "Notiz", "Vor Ort"]
    w.writerow(head)
    for x in data["entries"]:
        w.writerow(csvsafe.row([_de(x["day"]), x["start"], x["end"], hm(x["minutes"]), hours_dec(x["minutes"])] +
                               ([x["user"]] if data["team"] else []) +
                               [x["customer"], x["project"], x["task"], x["note"], "ja" if x["on_site"] else ""]))
    w.writerow(csvsafe.row(["Summe", "", "", hm(data["total_minutes"]), hours_dec(data["total_minutes"])]))
    return ("﻿" + buf.getvalue()).encode("utf-8")


# --- Excel --------------------------------------------------------------------

def _logo_for(conn) -> Optional[Dict]:
    """Logo fürs PDF und die Rohdaten für Excel – ein kaputtes Bild darf keinen Bericht verhindern."""
    try:
        img = logo.pdf_image(conn)
        got = logo.load(conn)
    except (logo.LogoError, ValueError):
        return None
    if not img or not got:
        return None
    img["raw"], img["format"] = got[0], got[1].get("format", "png")
    return img


def to_xlsx(data: Dict, img: Optional[Dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="E5E7EB")
    wb = Workbook()
    ws = wb.active
    ws.title = "Übersicht"
    ws.append([csvsafe.cell(data["company"]) if data["company"] else "Zeitmanagement"])
    ws["A1"].font = Font(bold=True, size=12, color="404040")
    ws.append(["Zeitnachweis " + period_text(data)])
    ws["A2"].font = Font(bold=True, size=14)
    ws.append([csvsafe.cell("  ·  ".join(data["filters"]))] if data["filters"] else [])
    ws.append([f"Gruppiert nach {data['group_label']}"])
    ws.append([])
    ws.append(["Gruppe", "Unterpunkt", "Buchungen", "Dauer (h:mm)", "Stunden"])
    for c in ws[ws.max_row]:
        c.font, c.fill = bold, head_fill
    for g in data["groups"]:
        ws.append(csvsafe.row([g["label"], "", g["count"], hm(g["minutes"]), round(g["minutes"] / 60, 2)]))
        csvsafe.no_formulas(ws)
        for c in ws[ws.max_row]:
            c.font = bold
        for k in g["children"]:
            ws.append(csvsafe.row(["", k["label"], k["count"], hm(k["minutes"]), round(k["minutes"] / 60, 2)]))
            csvsafe.no_formulas(ws)
    ws.append(["Summe", "", data["count"], data["total"], round(data["total_minutes"] / 60, 2)])
    for c in ws[ws.max_row]:
        c.font = bold
    for col, width in zip("ABCDE", (34, 34, 11, 13, 10)):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=7, min_col=5, max_col=5):
        for c in row:
            c.number_format = "0.00"
    _xlsx_logo(ws, img, "D")

    ws2 = wb.create_sheet("Buchungen")
    head = ["Datum", "Von", "Bis", "Dauer (h:mm)", "Stunden"] + (["Mitarbeiter"] if data["team"] else []) + \
           ["Kunde", "Projekt", "Aufgabe", "Notiz", "Vor Ort"]
    ws2.append(head)
    for c in ws2[1]:
        c.font, c.fill = bold, head_fill
    for x in data["entries"]:
        ws2.append(csvsafe.row([date.fromisoformat(x["day"]), x["start"], x["end"], hm(x["minutes"]), round(x["minutes"] / 60, 2)] +
                               ([x["user"]] if data["team"] else []) +
                               [x["customer"], x["project"], x["task"], x["note"], "ja" if x["on_site"] else ""]))
        csvsafe.no_formulas(ws2)
        ws2.cell(ws2.max_row, 1).number_format = "DD.MM.YYYY"
        ws2.cell(ws2.max_row, 5).number_format = "0.00"
    ws2.append(["Summe", "", "", data["total"], round(data["total_minutes"] / 60, 2)])
    for c in ws2[ws2.max_row]:
        c.font = bold
    widths = [11, 7, 7, 12, 9] + ([20] if data["team"] else []) + [24, 26, 30, 40, 8]
    for i, wdt in enumerate(widths, 1):
        ws2.column_dimensions[ws2.cell(1, i).column_letter].width = wdt
    for row in ws2.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=c.column_letter in ("I", "J", "K"))
    ws2.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsx_logo(ws, img: Optional[Dict], col: str) -> None:
    if not img:
        return
    from openpyxl.drawing.image import Image as XlImage

    class _Pic(XlImage):
        """openpyxl-Bild ohne Pillow – Größe und Format kennen wir schon."""
        def __init__(self, raw: bytes, fmt: str, width: int, height: int):
            self.ref, self._raw = None, raw
            self.format = "jpeg" if fmt == "jpeg" else "png"
            self.width, self.height = width, height

        def _data(self) -> bytes:
            return self._raw

    w, h = logo.fit_box(img["width"], img["height"], 160, 40)
    ws.add_image(_Pic(img["raw"], img["format"], round(w), round(h)), f"{col}1")


# --- PDF ----------------------------------------------------------------------

L, R = 40, A4_W - 40
GREY = (0.42, 0.45, 0.5)
HEAD = (0.9, 0.91, 0.93)
LIGHT = (0.97, 0.97, 0.98)
BOTTOM = A4_H - 50


def _header(pdf: PDF, data: Dict, img: Optional[Dict], first: bool) -> float:
    y = 40
    if img:
        w, h = logo.fit_box(img["width"], img["height"], 140, 30)
        pdf.image(R - w, y - 12, w, h, img)
    pdf.text(L, y, data["company"] or "Zeitmanagement", 10, True, color=(0.25, 0.25, 0.25))
    if first:
        pdf.text(L, y + 26, "Zeitnachweis", 17, True)
        pdf.text(L, y + 44, period_text(data) + ("  ·  " + "  ·  ".join(data["filters"]) if data["filters"] else ""), 9.5)
        pdf.line(L, y + 54, R, y + 54, 1, (0.3, 0.3, 0.3))
        return y + 74
    pdf.text(L, y + 20, f"Zeitnachweis {period_text(data)} (Fortsetzung)", 9, color=GREY)
    return y + 36


def _footer(pdf: PDF, data: Dict, page: int) -> None:
    pdf.text(L, A4_H - 26, f"Erstellt am {data['created']:%d.%m.%Y um %H:%M} Uhr · Zeitmanagement", 7, color=GREY)
    pdf.text(R, A4_H - 26, f"Seite {page}", 7, color=GREY, align="right")


def to_pdf(data: Dict, img: Optional[Dict], with_entries: bool = True) -> bytes:
    pdf = PDF(title=f"Zeitnachweis {period_text(data)}", author=data["company"] or "Zeitmanagement")
    page = 1
    y = _header(pdf, data, img, True)

    # Kennzahlen
    stats = [("Gesamt", data["total"] + " h"), ("Buchungen", str(data["count"])), ("Projekte", str(data["projects"]))]
    if data["show_users"]:
        stats.append(("Personen", str(data["people"])))
    x = L
    for label, val in stats:
        pdf.rect(x, y, 110, 34, LIGHT)
        pdf.text(x + 8, y + 12, label, 7.5, color=GREY)
        pdf.text(x + 8, y + 27, val, 12, True)
        x += 118
    y += 52

    def new_page():
        nonlocal y, page
        _footer(pdf, data, page)
        pdf.new_page()
        page += 1
        y = _header(pdf, data, img, False)

    # Übersicht
    pdf.text(L, y, f"Übersicht nach {data['group_label']}", 10.5, True)
    y += 8
    pdf.rect(L, y, R - L, 15, HEAD)
    pdf.text(L + 4, y + 10.5, "Gruppe", 8, True)
    pdf.text(R - 120, y + 10.5, "Buchungen", 8, True, "right")
    pdf.text(R - 60, y + 10.5, "Dauer", 8, True, "right")
    pdf.text(R - 4, y + 10.5, "Anteil", 8, True, "right")
    y += 15
    total = data["total_minutes"] or 1
    for g in data["groups"]:
        lines = [(g, True)] + [(k, False) for k in g["children"]]
        for item, top in lines:
            if y > BOTTOM:
                new_page()
            label = item["label"] if top else "    " + item["label"]
            pdf.text(L + 4, y + 10, fit(label, R - L - 190, 8.5, top), 8.5, top)
            pdf.text(R - 120, y + 10, str(item["count"]), 8.5, top, "right")
            pdf.text(R - 60, y + 10, hm(item["minutes"]), 8.5, top, "right")
            pdf.text(R - 4, y + 10, f"{round(item['minutes'] * 100 / total)} %", 8.5, top, "right", color=(0, 0, 0) if top else GREY)
            y += 13.5
            pdf.line(L, y, R, y, 0.25, (0.86, 0.86, 0.86))
    if y > BOTTOM:
        new_page()
    pdf.line(L, y + 1, R, y + 1, 0.8, (0.3, 0.3, 0.3))
    pdf.text(L + 4, y + 13, "Summe", 9, True)
    pdf.text(R - 120, y + 13, str(data["count"]), 9, True, "right")
    pdf.text(R - 60, y + 13, data["total"], 9, True, "right")
    y += 30

    if with_entries and data["entries"]:
        cols = [("Datum", L + 4, "left"), ("Zeit", L + 64, "left")]
        x0 = L + 128
        if data["show_users"]:
            cols.append(("Mitarbeiter", x0, "left"))
            x0 += 86
        cols += [("Projekt / Aufgabe / Notiz", x0, "left"), ("Dauer", R - 4, "right")]
        text_w = R - 50 - x0

        def head():
            nonlocal y
            pdf.rect(L, y, R - L, 15, HEAD)
            for title, cx, al in cols:
                pdf.text(cx, y + 10.5, title, 8, True, al)
            y += 15

        if y > BOTTOM - 60:
            new_page()
        pdf.text(L, y, "Buchungen", 10.5, True)
        y += 8
        head()
        for i, x in enumerate(data["entries"]):
            line2 = " · ".join(p for p in (x["task"], x["note"], "vor Ort" if x["on_site"] else "") if p)
            h = 24 if line2 else 14
            if y + h > BOTTOM:
                new_page()
                head()
            if i % 2:
                pdf.rect(L, y, R - L, h, LIGHT)
            pdf.text(L + 4, y + 10, f"{WD[date.fromisoformat(x['day']).weekday()]} {x['day'][8:10]}.{x['day'][5:7]}.", 8)
            pdf.text(L + 64, y + 10, f"{x['start']}–{x['end']}", 8)
            if data["show_users"]:
                pdf.text(L + 128, y + 10, fit(x["user"], 82, 8), 8)
            pdf.text(x0, y + 10, fit((x["customer"] + " · " if x["customer"] != "Ohne Kunde" else "") + x["project"], text_w, 8, True), 8, True)
            if line2:
                pdf.text(x0, y + 20.5, fit(line2, text_w, 7.5), 7.5, color=GREY)
            pdf.text(R - 4, y + 10, hm(x["minutes"]), 8, align="right")
            y += h
        pdf.line(L, y + 1, R, y + 1, 0.8, (0.3, 0.3, 0.3))
        pdf.text(L + 4, y + 13, "Summe", 9, True)
        pdf.text(R - 4, y + 13, data["total"], 9, True, "right")
    elif not data["entries"]:
        pdf.text(L, y, "Im gewählten Zeitraum gibt es keine abgeschlossenen Buchungen.", 9, color=GREY)
    _footer(pdf, data, page)
    return pdf.output()


def render(conn: sqlite3.Connection, data: Dict, fmt: str) -> (bytes, str, str):
    """Liefert (Inhalt, MIME-Typ, Dateiname)."""
    base = file_base(data)
    if fmt == "csv":
        return to_csv(data), "text/csv; charset=utf-8", base + ".csv"
    img = _logo_for(conn)
    if fmt == "xlsx":
        return to_xlsx(data, img), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", base + ".xlsx"
    if fmt == "pdf":
        return to_pdf(data, img), "application/pdf", base + ".pdf"
    raise ReportError("Format muss pdf, xlsx oder csv sein")

