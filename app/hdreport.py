"""Helpdesk-Auswertung: Kennzahlen für einen Zeitraum und Ticket-Bericht als PDF.

Kennzahlen (Zeitraum = Eingang bzw. Erledigt-Datum, Ortszeit):
- neu: im Zeitraum eingegangene Tickets; erledigt: im Zeitraum erledigte; offen: heute noch nicht erledigt.
- Ø Reaktionszeit: Eingang → erste Antwort oder Notiz eines Mitarbeiters (nur Formular, Mail, Portal – von Hand
  angelegte Tickets zählen nicht, da sind Eingang und Bearbeitung dasselbe).
- Ø Lösungszeit: Eingang → erledigt (für die im Zeitraum erledigten).
- Ø Zeit pro Ticket: gebuchte Zeit der im Zeitraum eingegangenen Tickets / Anzahl mit Zeit.
"""
import csv
import io
import re
import sqlite3
from collections import OrderedDict
from datetime import date, timedelta
from typing import Dict, List, Optional

from . import csvsafe, helpdesk as hd
from .db import get_setting
from .pdf import A4_H, A4_W, PDF, fit, text_width
from .timeutil import day_start_utc, now_utc, parse_utc, to_local, to_utc_str

MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _avg(values: List[float]) -> Optional[int]:
    return round(sum(values) / len(values)) if values else None


def dur_text(minutes: Optional[int]) -> str:
    """Minuten lesbar: 45 min, 3:20 h, 2,5 Tage."""
    if minutes is None:
        return "–"
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60}:{minutes % 60:02d} h"
    return f"{minutes / 1440:.1f} Tage".replace(".", ",")


def stats(conn: sqlite3.Connection, d_from: date, d_to: date) -> Dict:
    if d_to < d_from or (d_to - d_from).days > 1100:
        raise ValueError("Zeitraum ungültig (höchstens drei Jahre)")
    lo, hi = to_utc_str(day_start_utc(d_from)), to_utc_str(day_start_utc(d_to + timedelta(days=1)))
    base = ("SELECT t.*, p.name AS project, c.name AS customer, k.name AS category, k.color AS category_color "
            "FROM tickets t JOIN projects p ON p.id = t.project_id LEFT JOIN customers c ON c.id = p.customer_id "
            "LEFT JOIN ticket_categories k ON k.id = t.category_id ")
    created = conn.execute(base + "WHERE t.created_at >= ? AND t.created_at < ?", (lo, hi)).fetchall()
    done = conn.execute(base + "WHERE t.done_at >= ? AND t.done_at < ?", (lo, hi)).fetchall()
    open_now = conn.execute("SELECT COUNT(*) FROM tickets WHERE status != 'done'").fetchone()[0]

    def minutes_between(a, b):
        return (parse_utc(b) - parse_utc(a)).total_seconds() / 60

    resp = [minutes_between(t["created_at"], t["first_response_at"]) for t in created
            if t["first_response_at"] and t["source"] != "manual"]
    sol = [minutes_between(t["created_at"], t["done_at"]) for t in done]
    ids = [t["id"] for t in created]
    secs: Dict[int, float] = {}
    if ids:
        for e in conn.execute(f"SELECT ticket_id, start_utc, end_utc FROM time_entries WHERE end_utc IS NOT NULL AND ticket_id IN ({','.join('?' * len(ids))})", ids):
            secs[e["ticket_id"]] = secs.get(e["ticket_id"], 0) + (parse_utc(e["end_utc"]) - parse_utc(e["start_utc"])).total_seconds()
    booked = [s / 60 for s in secs.values()]

    def breakdown(key) -> List[Dict]:
        out: "OrderedDict[str, Dict]" = OrderedDict()
        for t in created:
            k = key(t)
            g = out.setdefault(k, {"label": k, "created": 0, "done": 0, "minutes": 0, "resp": [], "sol": []})
            g["created"] += 1
            g["minutes"] += round(secs.get(t["id"], 0) / 60)
            if t["first_response_at"] and t["source"] != "manual":
                g["resp"].append(minutes_between(t["created_at"], t["first_response_at"]))
        for t in done:
            k = key(t)
            g = out.setdefault(k, {"label": k, "created": 0, "done": 0, "minutes": 0, "resp": [], "sol": []})
            g["done"] += 1
            g["sol"].append(minutes_between(t["created_at"], t["done_at"]))
        rows = [{"label": g["label"], "created": g["created"], "done": g["done"], "minutes": g["minutes"],
                 "avg_response": _avg(g["resp"]), "avg_solution": _avg(g["sol"])} for g in out.values()]
        return sorted(rows, key=lambda r: (-r["created"], -r["done"], r["label"].lower()))

    months: "OrderedDict[str, Dict]" = OrderedDict()
    cur = date(d_from.year, d_from.month, 1)
    while cur <= d_to:
        months[f"{cur.year}-{cur.month:02d}"] = {"label": f"{MONTHS[cur.month - 1]} {cur.year}", "created": 0, "done": 0}
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    for t in created:
        d = to_local(parse_utc(t["created_at"]))
        months.setdefault(f"{d.year}-{d.month:02d}", {"label": f"{MONTHS[d.month - 1]} {d.year}", "created": 0, "done": 0})["created"] += 1
    for t in done:
        d = to_local(parse_utc(t["done_at"]))
        months.setdefault(f"{d.year}-{d.month:02d}", {"label": f"{MONTHS[d.month - 1]} {d.year}", "created": 0, "done": 0})["done"] += 1

    return {
        "from": d_from.isoformat(), "to": d_to.isoformat(),
        "created": len(created), "done": len(done), "open_now": open_now,
        "avg_response": _avg(resp), "avg_solution": _avg(sol), "avg_minutes": _avg(booked),
        "minutes": round(sum(booked)), "with_time": len(booked),
        "by_project": breakdown(lambda t: (t["customer"] + " · " if t["customer"] else "") + t["project"]),
        "by_category": breakdown(lambda t: t["category"] or "ohne Kategorie"),
        "by_source": breakdown(lambda t: hd.SOURCES.get(t["source"], t["source"])),
        "by_month": list(months.values()),
    }


def stats_csv(s: Dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow([f"Helpdesk {s['from']} bis {s['to']}"])
    w.writerow(["Neu", "Erledigt", "Offen (heute)", "Ø Reaktion (min)", "Ø Lösung (min)", "Ø Zeit pro Ticket (min)"])
    w.writerow([s["created"], s["done"], s["open_now"], s["avg_response"] or "", s["avg_solution"] or "", s["avg_minutes"] or ""])
    for key, title in (("by_project", "Kunde/Projekt"), ("by_category", "Kategorie"), ("by_source", "Quelle")):
        w.writerow([])
        w.writerow([title, "Neu", "Erledigt", "Zeit (min)", "Ø Reaktion (min)", "Ø Lösung (min)"])
        for r in s[key]:
            w.writerow(csvsafe.row([r["label"], r["created"], r["done"], r["minutes"], r["avg_response"] or "", r["avg_solution"] or ""]))
    w.writerow([])
    w.writerow(["Monat", "Neu", "Erledigt"])
    for r in s["by_month"]:
        w.writerow(csvsafe.row([r["label"], r["created"], r["done"]]))
    return ("﻿" + buf.getvalue()).encode("utf-8")


# --- Ticket-Bericht als PDF ---------------------------------------------------------

L, R = 40, A4_W - 40
GREY = (0.42, 0.45, 0.5)
LIGHT = (0.97, 0.97, 0.98)
NOTE = (1.0, 0.97, 0.88)
BOTTOM = A4_H - 50


def wrap(text: str, width: float, size: float) -> List[str]:
    lines: List[str] = []
    for para in (text or "").replace("\r", "").split("\n"):
        words = re.split(r"(\s+)", para)
        line = ""
        for w in words:
            if not w:
                continue
            cand = line + w
            if text_width(cand, size) <= width or not line.strip():
                while text_width(cand, size) > width and len(cand) > 1:   # sehr langes Wort (z. B. Link) hart umbrechen
                    cut = len(cand)
                    while cut > 1 and text_width(cand[:cut], size) > width:
                        cut -= 1
                    lines.append(cand[:cut])
                    cand = cand[cut:]
                line = cand
            else:
                lines.append(line.rstrip())
                line = w.lstrip()
        lines.append(line.rstrip())
    return lines


def ticket_pdf(conn: sqlite3.Connection, ticket_id: int, with_notes: bool, img: Optional[Dict]) -> bytes:
    t = conn.execute("SELECT t.*, p.name AS project, c.name AS customer, k.name AS category, u.display_name AS assignee "
                     "FROM tickets t JOIN projects p ON p.id = t.project_id LEFT JOIN customers c ON c.id = p.customer_id "
                     "LEFT JOIN ticket_categories k ON k.id = t.category_id LEFT JOIN users u ON u.id = t.assignee_id "
                     "WHERE t.id = ?", (ticket_id,)).fetchone()
    kinds = ("requester", "reply", "note") if with_notes else ("requester", "reply")
    msgs = conn.execute(f"SELECT m.*, u.display_name AS user_name FROM ticket_messages m LEFT JOIN users u ON u.id = m.author_id "
                        f"WHERE m.ticket_id = ? AND m.kind IN ({','.join('?' * len(kinds))}) ORDER BY m.id", (ticket_id,) + kinds).fetchall()
    files = conn.execute("SELECT message_id, name FROM ticket_files WHERE ticket_id = ?", (ticket_id,)).fetchall()
    entries = conn.execute("SELECT e.*, u.display_name AS user_name FROM time_entries e JOIN users u ON u.id = e.user_id "
                           "WHERE e.ticket_id = ? AND e.end_utc IS NOT NULL ORDER BY e.start_utc", (ticket_id,)).fetchall()
    company = get_setting(conn, "company_name") or "Zeitmanagement"
    nr = hd.number(t["year"], t["seq"])
    created = to_local(now_utc())
    pdf = PDF(title=f"{nr} {t['title']}", author=company)
    state = {"y": 0.0, "page": 1}

    def header(first: bool):
        y = 40
        if img:
            from . import logo
            w, h = logo.fit_box(img["width"], img["height"], 140, 30)
            pdf.image(R - w, y - 12, w, h, img)
        pdf.text(L, y, company, 10, True, color=(0.25, 0.25, 0.25))
        if not first:
            pdf.text(L, y + 20, f"{nr} (Fortsetzung)", 9, color=GREY)
            state["y"] = y + 36
            return
        pdf.text(L, y + 26, f"Ticket {nr}", 17, True)
        y += 44
        for line in wrap(t["title"], R - L, 11)[:3]:
            pdf.text(L, y, line, 11, True)
            y += 14
        pdf.line(L, y - 4, R, y - 4, 1, (0.3, 0.3, 0.3))
        state["y"] = y + 10

    def footer():
        pdf.text(L, A4_H - 26, f"Erstellt am {created:%d.%m.%Y um %H:%M} Uhr · Zeitmanagement", 7, color=GREY)
        pdf.text(R, A4_H - 26, f"Seite {state['page']}", 7, color=GREY, align="right")

    def need(h: float):
        if state["y"] + h > BOTTOM:
            footer()
            pdf.new_page()
            state["page"] += 1
            header(False)

    def fmt(iso):
        return to_local(parse_utc(iso)).strftime("%d.%m.%Y %H:%M") if iso else "–"

    header(True)
    # Eckdaten in zwei Spalten
    facts = [("Kunde / Projekt", (t["customer"] + " · " if t["customer"] else "") + t["project"]),
             ("Melder", t["requester_name"] + (f" <{t['requester_email']}>" if t["requester_email"] else "") or "–"),
             ("Kategorie", t["category"] or "–"), ("Status", hd.STATUSES[t["status"]]),
             ("Eingang", f"{fmt(t['created_at'])} · {hd.SOURCES.get(t['source'], t['source'])}"),
             ("Erledigt", fmt(t["done_at"])), ("Priorität", hd.PRIORITIES.get(t["priority"], "")),
             ("Bearbeiter", t["assignee"] or "–")]
    y = state["y"]
    col_w = (R - L) / 2
    for i, (k, v) in enumerate(facts):
        x = L + (i % 2) * col_w
        pdf.text(x, y, k, 7.5, color=GREY)
        pdf.text(x, y + 11, fit(v, col_w - 12, 9), 9)
        if i % 2:
            y += 26
    state["y"] = y + 8

    # Verlauf
    pdf.text(L, state["y"], "Verlauf", 10.5, True)
    state["y"] += 10
    for m in msgs:
        who = {"requester": t["requester_name"] or m["author_name"] or "Melder", "reply": m["user_name"] or m["author_name"],
               "note": m["user_name"] or m["author_name"]}[m["kind"]] or ""
        label = {"requester": "Meldung", "reply": "Antwort", "note": "Interne Notiz"}[m["kind"]]
        lines = wrap(m["body"], R - L - 16, 8.5)
        att = [f["name"] for f in files if f["message_id"] == m["id"]]
        h = 18 + 11 * len(lines) + (11 if att else 0) + 6
        need(min(h, 120))
        y0 = state["y"]
        bg = NOTE if m["kind"] == "note" else LIGHT
        pdf.rect(L, y0, R - L, min(h, BOTTOM - y0), bg)
        pdf.text(L + 8, y0 + 12, f"{label} · {who}", 8.5, True)
        pdf.text(R - 8, y0 + 12, fmt(m["created_at"]), 7.5, color=GREY, align="right")
        state["y"] = y0 + 25
        for ln in lines:
            if state["y"] + 11 > BOTTOM:
                need(BOTTOM)   # Seitenumbruch mitten in einer langen Nachricht
                state["y"] += 4
            pdf.text(L + 8, state["y"], ln, 8.5)
            state["y"] += 11
        if att:
            pdf.text(L + 8, state["y"], fit("Anhänge: " + ", ".join(att), R - L - 16, 7.5), 7.5, color=GREY)
            state["y"] += 11
        state["y"] += 8

    # Zeiten
    need(60)
    state["y"] += 6
    pdf.text(L, state["y"], "Gebuchte Zeiten", 10.5, True)
    state["y"] += 8
    total = 0
    if not entries:
        pdf.text(L, state["y"] + 10, "Keine Zeiten gebucht.", 9, color=GREY)
        state["y"] += 20
    for i, e in enumerate(entries):
        need(16)
        s, en = to_local(parse_utc(e["start_utc"])), to_local(parse_utc(e["end_utc"]))
        mins = round((en - s).total_seconds() / 60)
        total += mins
        y0 = state["y"]
        if i % 2:
            pdf.rect(L, y0, R - L, 14, LIGHT)
        pdf.text(L + 4, y0 + 10, f"{s:%d.%m.%Y}  {s:%H:%M}–{en:%H:%M}", 8)
        pdf.text(L + 130, y0 + 10, fit(e["user_name"], 90, 8), 8)
        note = " · ".join(x for x in ("vor Ort" if e["on_site"] else "", e["note"]) if x)
        pdf.text(L + 226, y0 + 10, fit(note, R - L - 290, 8), 8, color=GREY)
        pdf.text(R - 4, y0 + 10, f"{mins // 60}:{mins % 60:02d}", 8, align="right")
        state["y"] += 14
    if entries:
        pdf.line(L, state["y"] + 1, R, state["y"] + 1, 0.8, (0.3, 0.3, 0.3))
        pdf.text(L + 4, state["y"] + 13, "Summe", 9, True)
        pdf.text(R - 4, state["y"] + 13, f"{total // 60}:{total % 60:02d} h", 9, True, "right")
    footer()
    return pdf.output()


def ticket_pdf_name(t) -> str:
    return f"ticket_{hd.number(t['year'], t['seq'])}.pdf"

