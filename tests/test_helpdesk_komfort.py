"""Helpdesk 1.0: Kategorien, Textbausteine, Reaktionszeit, Kennzahlen, Zeit pro Ticket in der Auswertung, Ticket-PDF."""
import io
import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import dsm_auth, helpdesk
from app.db import MIGRATIONS, connect
from app.main import app
from app.timeutil import now_utc, to_utc_str, today_local

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
Y = (today_local() - timedelta(days=1)).isoformat()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(helpdesk, "send_mail", lambda *a, **k: "<x@y>")
    dsm_auth._fails.clear()
    with fresh_db(tmp_path, mode="team", company="Rinkel Digital"):
        c = TestClient(app)
        c.__enter__()
        c.post("/api/v1/auth/login", json={"username": "admin", "password": PW})
        c.patch("/api/v1/settings", json={"modules": {"helpdesk": True}})
        cust = c.post("/api/v1/customers", json={"name": "Physio Henze"}).json()["id"]
        p = c.post("/api/v1/projects", json={"kind": "project", "name": "IT-Support Praxis", "customer_id": cust}).json()["id"]
        token = next(f["token"] for f in c.put(f"/api/v1/helpdesk/forms/{p}", json={}).json()["forms"] if f["project_id"] == p)
        yield {"c": c, "p": p, "token": token}


def cats(c):
    return {x["name"]: x["id"] for x in c.get("/api/v1/helpdesk/categories").json()}


def test_categories_seeded_crud_and_filter(env):
    c = env["c"]
    k = cats(c)
    assert list(k)[:3] == ["Drucker", "Netzwerk/WLAN", "E-Mail"] and "Sonstiges" in k
    new = c.post("/api/v1/helpdesk/categories", json={"name": "Theorg", "color": "#0f766e"}).json()
    assert new["name"] == "Theorg"
    assert c.post("/api/v1/helpdesk/categories", json={"name": "x", "color": "rot"}).status_code == 422
    # Formular zeigt Kategorien, Melder wählt optional
    info = TestClient(app).get(f"/api/v1/public/form/{env['token']}").json()
    assert "Theorg" in [x["name"] for x in info["categories"]]
    r = TestClient(app).post(f"/api/v1/public/form/{env['token']}", json={"name": "S", "email": "s@physio-henze.de",
                                                                        "title": "Theorg hängt", "body": "x", "category_id": new["id"]})
    assert r.status_code == 201
    t = c.get("/api/v1/tickets").json()[0]
    assert t["category"] == "Theorg" and t["category_color"] == "#0f766e"
    # ungültige Kategorie im Formular → einfach ohne
    TestClient(app).post(f"/api/v1/public/form/{env['token']}", json={"name": "S", "email": "s@physio-henze.de",
                                                                    "title": "Ohne", "body": "x", "category_id": 9999})
    assert c.get("/api/v1/tickets", params={"category_id": 0}).json()[0]["title"] == "Ohne"
    assert [x["title"] for x in c.get("/api/v1/tickets", params={"category_id": new["id"]}).json()] == ["Theorg hängt"]
    counts = c.get("/api/v1/tickets/counts").json()["categories"]
    assert counts[str(new["id"])] == 1 and counts["0"] == 1
    # Kategorie am Ticket ändern → Verlauf
    d = c.patch(f"/api/v1/tickets/{t['id']}", json={"category_id": k["Drucker"]}).json()
    assert d["category"] == "Drucker" and any("Kategorie auf „Drucker“" in m["body"] for m in d["messages"])
    assert c.patch(f"/api/v1/tickets/{t['id']}", json={"category_id": 0}).json()["category"] is None
    assert c.patch(f"/api/v1/tickets/{t['id']}", json={"category_id": 9999}).status_code == 400
    # archivieren → nicht mehr wählbar
    c.patch(f"/api/v1/helpdesk/categories/{new['id']}", json={"archived": True})
    assert "Theorg" not in cats(c)
    assert c.patch(f"/api/v1/tickets/{t['id']}", json={"category_id": new["id"]}).status_code == 400
    assert "Theorg" in [x["name"] for x in c.get("/api/v1/helpdesk/categories", params={"include_archived": True}).json()]


def test_snippets_shared_and_personal(env):
    c = env["c"]
    s = c.post("/api/v1/snippets", json={"title": "Neustart", "body": "Hallo {name},\nbitte neu starten. {bearbeiter}", "shared": True}).json()
    assert s["shared"] and s["can_edit"]
    c.post("/api/v1/users", json={"username": "mia", "display_name": "Mia", "password": "geheim123"})
    mia = TestClient(app)
    mia.__enter__()
    mia.post("/api/v1/auth/login", json={"username": "mia", "password": "geheim123"})
    assert mia.post("/api/v1/snippets", json={"title": "x", "body": "y", "shared": True}).status_code == 403
    own = mia.post("/api/v1/snippets", json={"title": "Meins", "body": "Gruß, Mia"}).json()
    lst = mia.get("/api/v1/snippets").json()
    assert [x["title"] for x in lst] == ["Neustart", "Meins"] and not lst[0]["can_edit"] and lst[1]["can_edit"]
    assert mia.patch(f"/api/v1/snippets/{s['id']}", json={"title": "kaputt"}).status_code == 403
    assert [x["title"] for x in c.get("/api/v1/snippets").json()] == ["Neustart"]   # fremde persönliche sieht keiner
    assert c.delete(f"/api/v1/snippets/{own['id']}").status_code == 404
    assert mia.patch(f"/api/v1/snippets/{own['id']}", json={"body": "Viele Grüße, Mia"}).json()["body"] == "Viele Grüße, Mia"
    assert mia.delete(f"/api/v1/snippets/{own['id']}").json()["ok"]


def test_first_response_and_stats(env):
    c = env["c"]
    k = cats(c)
    anon = TestClient(app)
    for title, cat in (("A", k["Drucker"]), ("B", k["Drucker"]), ("C", None)):
        anon.post(f"/api/v1/public/form/{env['token']}", json={"name": "S", "email": "s@physio-henze.de", "title": title, "body": "x",
                                                               "category_id": cat})
    c.post("/api/v1/tickets", json={"project_id": env["p"], "title": "Intern", "body": "notiz"})
    ids = {t["title"]: t["id"] for t in c.get("/api/v1/tickets", params={"view": "all"}).json()}
    conn = connect()
    old = to_utc_str(now_utc() - timedelta(hours=2))
    conn.execute("UPDATE tickets SET created_at = ?", (old,))
    conn.close()
    c.post(f"/api/v1/tickets/{ids['A']}/messages", json={"kind": "note", "body": "schaue"})
    c.post(f"/api/v1/tickets/{ids['A']}/messages", json={"kind": "reply", "body": "erledigt", "done": True})
    c.post(f"/api/v1/tickets/{ids['Intern']}/messages", json={"kind": "note", "body": "x"})
    first = c.get(f"/api/v1/tickets/{ids['A']}").json()["first_response_at"]
    assert first
    c.post(f"/api/v1/tickets/{ids['A']}/messages", json={"kind": "note", "body": "später"})
    assert c.get(f"/api/v1/tickets/{ids['A']}").json()["first_response_at"] == first   # bleibt die erste
    c.post("/api/v1/times", json={"ticket_id": ids["A"], "start": Y + "T09:00", "end": Y + "T09:30"})
    c.post("/api/v1/times", json={"ticket_id": ids["B"], "start": Y + "T10:00", "end": Y + "T11:00"})
    today = today_local().isoformat()
    s = c.get("/api/v1/helpdesk/stats", params={"from": (today_local() - timedelta(days=30)).isoformat(), "to": today}).json()
    assert s["created"] == 4 and s["done"] == 1 and s["open_now"] == 3
    assert 115 <= s["avg_response"] <= 125          # 2 h, nur A (manuelle Tickets zählen nicht)
    assert 115 <= s["avg_solution"] <= 125
    assert s["avg_minutes"] == 45 and s["minutes"] == 90
    bycat = {r["label"]: r for r in s["by_category"]}
    assert bycat["Drucker"]["created"] == 2 and bycat["Drucker"]["done"] == 1 and bycat["Drucker"]["minutes"] == 90
    assert bycat["ohne Kategorie"]["created"] == 2
    assert {r["label"] for r in s["by_source"]} == {"Formular", "Von Hand"}
    assert s["by_project"][0]["label"] == "Physio Henze · IT-Support Praxis"
    assert sum(m["created"] for m in s["by_month"]) == 4
    csv_ = c.get("/api/v1/helpdesk/stats.csv", params={"from": today, "to": today})
    assert csv_.status_code == 200 and "Kategorie;Neu;Erledigt" in csv_.content.decode("utf-8-sig")
    assert c.get("/api/v1/helpdesk/stats", params={"from": today, "to": "2020-01-01"}).status_code == 400
    # Mitarbeiter dürfen die Kennzahlen nicht sehen
    c.post("/api/v1/users", json={"username": "mia", "display_name": "Mia", "password": "geheim123"})
    mia = TestClient(app)
    mia.__enter__()
    mia.post("/api/v1/auth/login", json={"username": "mia", "password": "geheim123"})
    assert mia.get("/api/v1/helpdesk/stats", params={"from": today, "to": today}).status_code == 403


def test_report_group_ticket_and_category_filter(env):
    c = env["c"]
    k = cats(c)
    t1 = c.post("/api/v1/tickets", json={"project_id": env["p"], "title": "Drucker", "category_id": k["Drucker"]}).json()
    t2 = c.post("/api/v1/tickets", json={"project_id": env["p"], "title": "WLAN", "category_id": k["Netzwerk/WLAN"]}).json()
    c.post("/api/v1/times", json={"ticket_id": t1["id"], "start": Y + "T09:00", "end": Y + "T10:00"})
    c.post("/api/v1/times", json={"ticket_id": t2["id"], "start": Y + "T11:00", "end": Y + "T11:30"})
    c.post("/api/v1/times", json={"project_id": env["p"], "start": Y + "T12:00", "end": Y + "T12:15"})
    r = c.get("/api/v1/reports/times", params={"from": Y, "to": Y, "group": "ticket"}).json()
    labels = {g["label"]: g["minutes"] for g in r["groups"]}
    assert labels[f"{t1['number']} Drucker"] == 60 and labels["(ohne Ticket)"] == 15 and "ticket" in r["group_options"]
    r = c.get("/api/v1/reports/times", params={"from": Y, "to": Y, "category_id": k["Drucker"]}).json()
    assert r["total_minutes"] == 60 and "Kategorie: Drucker" in r["filters"]
    csv_ = c.get("/api/v1/reports/times/export", params={"from": Y, "to": Y, "format": "csv"}).content.decode("utf-8-sig")
    assert ";Ticket;" in csv_.splitlines()[0] and f"{t1['number']} Drucker" in csv_
    assert c.get("/api/v1/reports/times/export", params={"from": Y, "to": Y, "format": "pdf", "group": "ticket"}).content.startswith(b"%PDF")
    # Helpdesk aus → keine Gruppierung „Ticket“
    c.patch("/api/v1/settings", json={"modules": {"helpdesk": False}})
    r = c.get("/api/v1/reports/times", params={"from": Y, "to": Y, "group": "ticket"}).json()
    assert r["group"] == "customer" and "ticket" not in r["group_options"]


def test_ticket_pdf(env):
    c = env["c"]
    t = c.post("/api/v1/tickets", json={"project_id": env["p"], "title": "Drucker am Empfang druckt leere Seiten " * 3,
                                        "requester_name": "Marco", "requester_email": "m@physio-henze.de",
                                        "body": "Seit heute Mittag nur leere Seiten.\n" * 40}).json()
    c.post(f"/api/v1/tickets/{t['id']}/messages", json={"kind": "note", "body": "GEHEIMNOTIZ"})
    c.post(f"/api/v1/tickets/{t['id']}/messages", json={"kind": "reply", "body": "Treiber getauscht. " + "x" * 300})
    c.post("/api/v1/times", json={"ticket_id": t["id"], "start": Y + "T09:00", "end": Y + "T09:45", "on_site": True, "note": "vor Ort"})
    r = c.get(f"/api/v1/tickets/{t['id']}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF-1.4") and t["number"] in r.headers["content-disposition"]
    import zlib, re as _re
    def text_of(pdf):
        return b"".join(zlib.decompress(m) for m in _re.findall(rb"stream\n(.*?)\nendstream", pdf, _re.S) if m[:1] == b"x")
    plain = text_of(r.content)
    assert b"GEHEIMNOTIZ" not in plain and b"Treiber getauscht" in plain and b"0:45" in plain
    assert plain.count(b"/F1") > 10 and r.content.count(b"/Type /Page ") >= 2   # lange Meldung → mehrere Seiten
    assert b"GEHEIMNOTIZ" in text_of(c.get(f"/api/v1/tickets/{t['id']}/pdf", params={"notes": True}).content)


def test_wrap():
    from app.hdreport import dur_text, wrap
    lines = wrap("kurz\n" + "wort " * 60 + "\nhttps://" + "a" * 200, 200, 9)
    assert lines[0] == "kurz" and len(lines) > 5 and all(len(x) < 120 for x in lines)
    assert dur_text(None) == "–" and dur_text(45) == "45 min" and dur_text(200) == "3:20 h" and dur_text(3600) == "2,5 Tage"


def test_migration_backfills_first_response(tmp_path):
    import sqlite3
    c = sqlite3.connect(str(tmp_path / "m.db"), isolation_level=None)
    c.row_factory = sqlite3.Row
    for v in range(1, 10):
        MIGRATIONS[v](c)
    c.execute("INSERT INTO users(username, display_name, is_admin, created_at) VALUES ('a', 'A', 1, '2026-01-01T00:00:00+00:00')")
    c.execute("INSERT INTO projects(kind, name, created_at) VALUES ('project', 'P', '2026-01-01T00:00:00+00:00')")
    c.execute("INSERT INTO tickets(year, seq, project_id, title, created_at, updated_at) VALUES (2026, 1, 1, 'x', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00')")
    c.execute("INSERT INTO ticket_messages(ticket_id, kind, author_id, body, created_at) VALUES (1, 'requester', NULL, 'm', '2026-11-01T08:00:00+00:00')")
    c.execute("INSERT INTO ticket_messages(ticket_id, kind, author_id, body, created_at) VALUES (1, 'system', 1, 's', '2026-11-01T08:30:00+00:00')")
    c.execute("INSERT INTO ticket_messages(ticket_id, kind, author_id, body, created_at) VALUES (1, 'reply', 1, 'r', '2026-11-01T09:00:00+00:00')")
    MIGRATIONS[10](c)
    assert c.execute("SELECT first_response_at FROM tickets").fetchone()[0] == "2026-11-01T09:00:00+00:00"
    assert c.execute("SELECT COUNT(*) FROM ticket_categories").fetchone()[0] == 6
