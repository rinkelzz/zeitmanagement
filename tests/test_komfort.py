"""Version 0.4: Wiederholung, Checkliste, Erinnerung zur Uhrzeit, Projektbudget, Abrechnungs-Kennzeichen."""
import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import budget, reminders
from app.db import connect
from app.main import app
from app.timeutil import today_local, tz

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
T = today_local()
D = lambda n: (T + timedelta(days=n)).isoformat()  # noqa: E731


def client(user="admin", pw=PW):
    c = TestClient(app)
    c.__enter__()
    assert c.post("/api/v1/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c


@pytest.fixture()
def c(tmp_path):
    with fresh_db(tmp_path, mode="single"):
        yield client()


def test_recurring_task(c):
    t = c.post("/api/v1/tasks", json={"text": "Backup prüfen täglich !hoch", "items": ["Log lesen", "Platz prüfen"]}).json()
    assert t["recur"] == "d:1" and t["recur_label"] == "täglich" and t["due_date"] == D(0)
    assert [i["title"] for i in t["items"]] == ["Log lesen", "Platz prüfen"]
    c.patch(f"/api/v1/tasks/{t['id']}/items/{t['items'][0]['id']}", json={"done": True})
    r = c.patch(f"/api/v1/tasks/{t['id']}", json={"done": True}).json()
    nxt = r["next"]
    assert nxt["due_date"] == D(1) and nxt["title"] == "Backup prüfen" and nxt["priority"] == 1 and nxt["recur"] == "d:1"
    assert [i["done"] for i in nxt["items"]] == [False, False]          # Checkliste wieder offen
    # wieder öffnen und erneut erledigen → keine zweite Folgeaufgabe
    c.patch(f"/api/v1/tasks/{t['id']}", json={"done": False})
    assert "next" not in c.patch(f"/api/v1/tasks/{t['id']}", json={"done": True}).json()
    assert len([x for x in c.get("/api/v1/tasks", params={"view": "open"}).json() if x["title"] == "Backup prüfen"]) == 1
    # verspätet erledigt: nächster Termin liegt nach heute
    w = c.post("/api/v1/tasks", json={"title": "Wochenbericht", "due_date": D(-15), "recur": "w:1"}).json()
    n = c.patch(f"/api/v1/tasks/{w['id']}", json={"done": True}).json()["next"]
    assert D(1) <= n["due_date"] <= D(7)
    m = c.post("/api/v1/tasks", json={"title": "Monatsende", "due_date": "2026-01-31", "recur": "m:1"}).json()
    assert m["recur"] == "m:1@31" and m["recur_label"] == "monatlich"
    assert c.post("/api/v1/tasks", json={"title": "x", "recur": "q:1"}).status_code == 400
    # Wiederholung entfernen
    assert c.patch(f"/api/v1/tasks/{m['id']}", json={"recur": None}).json()["recur"] is None
    log = c.get("/api/v1/audit", params={"category": "aufgaben"}).json()["entries"]
    assert any(e["action"] == "task_recurred" for e in log)


def test_checklist(c):
    t = c.post("/api/v1/tasks", json={"title": "Umzug"}).json()
    t = c.post(f"/api/v1/tasks/{t['id']}/items", json={"title": "Kartons"}).json()
    t = c.post(f"/api/v1/tasks/{t['id']}/items", json={"title": "Router"}).json()
    iid = t["items"][1]["id"]
    t = c.patch(f"/api/v1/tasks/{t['id']}/items/{iid}", json={"done": True, "title": "WLAN-Router"}).json()
    assert [(i["title"], i["done"]) for i in t["items"]] == [("Kartons", False), ("WLAN-Router", True)]
    t = c.delete(f"/api/v1/tasks/{t['id']}/items/{iid}").json()
    assert len(t["items"]) == 1
    assert c.patch(f"/api/v1/tasks/{t['id']}/items/99999", json={"done": True}).status_code == 404
    assert c.post(f"/api/v1/tasks/{t['id']}/items", json={"title": ""}).status_code == 422


def test_timed_reminder(c, monkeypatch):
    sent = []
    monkeypatch.setattr(reminders, "send_mail", lambda conn, to, subject, body: sent.append((to, subject, body)))
    c.patch("/api/v1/settings", json={"smtp_host": "mail.example.de", "smtp_from": "zeit@example.de"})
    c.patch("/api/v1/me", json={"email": "ich@example.de"})
    t = c.post("/api/v1/tasks", json={"text": "Anruf Physio Henze heute um 14:30"}).json()
    assert t["due_time"] == "14:30" and t["due_date"] == D(0)
    at = lambda h, m: datetime(T.year, T.month, T.day, h, m, tzinfo=tz())  # noqa: E731
    with connect() as conn:
        assert reminders.run_timed(conn, at(14, 29)) == []
        assert reminders.run_timed(conn, at(14, 30))[0]["status"] == "sent"
        assert reminders.run_timed(conn, at(14, 31)) == []                # nur einmal
    assert sent[0][0] == "ich@example.de" and sent[0][1] == "Erinnerung 14:30: Anruf Physio Henze"
    # neue Uhrzeit → neue Erinnerung; weit verspätet → still übersprungen
    c.patch(f"/api/v1/tasks/{t['id']}", json={"due_time": "08:00"})
    with connect() as conn:
        assert reminders.run_timed(conn, at(15, 0))[0]["status"] == "late"
    assert len(sent) == 1
    assert c.patch(f"/api/v1/tasks/{t['id']}", json={"due_time": "25:00"}).status_code == 422


def _project(c, **kw):
    return c.post("/api/v1/projects", json={"kind": "project", "name": "Website", **kw}).json()


def test_budget_warning(c, monkeypatch):
    mails = []
    monkeypatch.setattr(budget, "send_mail", lambda conn, to, subject, body: mails.append(subject))
    c.patch("/api/v1/settings", json={"smtp_host": "mail.example.de", "smtp_from": "zeit@example.de"})
    c.patch("/api/v1/me", json={"email": "chef@example.de"})
    p = _project(c, budget_minutes=600)
    assert p["budget"] == {"budget_minutes": 600, "used_minutes": 0, "percent": 0, "level": 0}
    y = D(-1)
    add = lambda s, e: c.post("/api/v1/times", json={"project_id": p["id"], "start": f"{y}T{s}", "end": f"{y}T{e}"}).json()  # noqa: E731
    add("08:00", "14:00")                                   # 6 h = 60 %
    assert mails == []
    e = add("14:00", "16:30")                               # 8,5 h = 85 %
    assert e["budget_warning"] == 80 and mails == ["Budget zu 80 % verbraucht: Website"]
    add("16:30", "18:00")                                   # 10 h = 100 %
    assert mails[-1] == "Budget überschritten: Website" and len(mails) == 2
    got = next(x for x in c.get("/api/v1/projects").json() if x["id"] == p["id"])
    assert got["budget"]["percent"] == 100 and got["budget"]["level"] == 100
    # Budget erhöhen → Schwelle zurück, keine neue Mail
    c.patch(f"/api/v1/projects/{p['id']}", json={"budget_minutes": 3000})
    with connect() as conn:
        assert conn.execute("SELECT budget_warned FROM projects WHERE id = ?", (p["id"],)).fetchone()[0] == 0
    assert len(mails) == 2
    assert c.patch(f"/api/v1/projects/{p['id']}", json={"budget_minutes": None}).json()["budget"] is None
    lst = c.post("/api/v1/projects", json={"kind": "list", "name": "Liste", "budget_minutes": 60}).json()
    assert lst["budget"] is None


def test_billing_lock(tmp_path):
    with fresh_db(tmp_path, mode="team"):
        chef = client()
        anna_id = chef.post("/api/v1/users", json={"username": "anna", "display_name": "Anna", "password": "geheim123"}).json()["id"]
        p = _project(chef, member_ids=[anna_id])
        anna = client("anna", "geheim123")
        y = D(-1)
        e1 = anna.post("/api/v1/times", json={"project_id": p["id"], "start": f"{y}T08:00", "end": f"{y}T10:00"}).json()
        e2 = anna.post("/api/v1/times", json={"project_id": p["id"], "start": f"{y}T10:00", "end": f"{y}T11:00"}).json()
        q = {"from": D(-7), "to": D(0)}
        assert anna.post("/api/v1/reports/times/billing", json={**q, "action": "bill"}).status_code == 403
        r = chef.post("/api/v1/reports/times/billing", json={**q, "action": "bill", "ref": "RE-2026-017", "entry_ids": [e1["id"]]}).json()
        assert r == {"count": 1, "minutes": 120}
        # gesperrt – auch für den Chef, bis die Abrechnung zurückgenommen ist
        for who in (anna, chef):
            r = who.patch(f"/api/v1/times/{e1['id']}", json={"note": "x"})
            assert r.status_code == 409 and "abgerechnet" in r.json()["detail"] and "RE-2026-017" in r.json()["detail"]
            assert who.delete(f"/api/v1/times/{e1['id']}").status_code == 409
        assert anna.patch(f"/api/v1/times/{e2['id']}", json={"note": "geht"}).status_code == 200
        rep = chef.get("/api/v1/reports/times", params={**q, "billed": "open"}).json()
        assert rep["total_minutes"] == 60 and rep["filters"] == ["Nicht abgerechnet"]
        rep = chef.get("/api/v1/reports/times", params=q).json()
        assert rep["billed_minutes"] == 120 and rep["open_minutes"] == 60 and rep["can_bill"]
        assert [x["billed_ref"] for x in rep["entries"] if x["billed"]] == ["RE-2026-017"]
        assert anna.get("/api/v1/reports/times", params=q).json()["can_bill"] is False
        csv_ = chef.get("/api/v1/reports/times/export", params={**q, "format": "csv"}).content.decode("utf-8-sig")
        assert "Abgerechnet;Vermerk" in csv_.splitlines()[0] and "RE-2026-017" in csv_
        # alles Offene abrechnen, dann alles zurücknehmen
        assert chef.post("/api/v1/reports/times/billing", json={**q, "action": "bill"}).json()["count"] == 1
        assert chef.post("/api/v1/reports/times/billing", json={**q, "action": "unbill"}).json() == {"count": 2, "minutes": 180}
        assert anna.delete(f"/api/v1/times/{e1['id']}").json()["ok"]
        assert chef.post("/api/v1/reports/times/billing", json={**q, "action": "bill", "entry_ids": []}).status_code == 400
        log = chef.get("/api/v1/audit", params={"category": "zeiten"}).json()["entries"]
        assert {"time_billed", "time_unbilled"} <= {x["action"] for x in log}
