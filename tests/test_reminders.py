"""Tägliche Erinnerungs-Mail."""
import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import reminders
from app.db import connect
from app.main import app
from app.timeutil import today_local, tz

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
TODAY = today_local()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731


def _at(hour):
    return datetime(TODAY.year, TODAY.month, TODAY.day, hour, 5, tzinfo=tz())


def _setup(c, sent):
    c.patch("/api/v1/settings", json={"smtp_host": "mail.example.de", "smtp_from": "zeit@example.de", "reminder_hour": 7})
    return sent


def test_reminders_team(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(reminders, "send_mail", lambda conn, to, subject, body: sent.append((to, subject, body)))
    with fresh_db(tmp_path, mode="team"), TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"username": "admin", "password": PW})
        _setup(c, sent)
        anna = c.post("/api/v1/users", json={"username": "anna", "display_name": "Anna", "email": "anna@example.de",
                                              "password": "geheim123"}).json()["id"]
        c.post("/api/v1/users", json={"username": "ohne", "display_name": "Ohne Mail", "password": "geheim123"})
        c.patch("/api/v1/me", json={"email": "chef@example.de"})
        lst = c.post("/api/v1/projects", json={"kind": "list", "name": "Praxis"}).json()["id"]
        c.post("/api/v1/tasks", json={"title": "Alt", "due_date": D(-2), "assignee_ids": [anna], "project_id": lst, "priority": 1})
        c.post("/api/v1/tasks", json={"title": "Heute", "due_date": D(0), "assignee_ids": [anna]})
        c.post("/api/v1/tasks", json={"title": "Später", "due_date": D(3), "assignee_ids": [anna]})
        done = c.post("/api/v1/tasks", json={"title": "Erledigt", "due_date": D(0), "assignee_ids": [anna]}).json()["id"]
        c.patch(f"/api/v1/tasks/{done}", json={"done": True})
        with connect() as conn:
            assert reminders.run_due(conn, _at(6)) == []           # vor der Uhrzeit nichts
            r = {x["user_id"]: x for x in reminders.run_due(conn, _at(7))}
            assert r[anna]["status"] == "sent" and r[anna]["count"] == 2
            assert r[1]["status"] == "empty"                       # der Chef hat nichts Fälliges
            assert len(r) == 2                                     # „Ohne Mail“ bekommt nichts
            assert reminders.run_due(conn, _at(9)) == []           # nur einmal am Tag
        to, subject, body = sent[0]
        assert to == "anna@example.de" and subject == "2 Aufgaben fällig (1 überfällig)"
        assert "Überfällig (1):\n- Alt [hoch] – Praxis (fällig" in body and "Heute fällig (1):\n- Heute" in body
        assert "Später" not in body and "Erledigt" not in body
        # abgeschaltet: pro Person und global
        with TestClient(app) as a:
            a.post("/api/v1/auth/login", json={"username": "anna", "password": "geheim123"})
            assert a.patch("/api/v1/me", json={"remind": False}).json()["remind"] is False
        c.patch("/api/v1/settings", json={"reminders_enabled": False})
        with connect() as conn:
            conn.execute("DELETE FROM reminder_log")
            assert reminders.run_due(conn, _at(8)) == []


def test_errors_are_retried_three_times(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise reminders.MailError("Server weg")
    monkeypatch.setattr(reminders, "send_mail", boom)
    with fresh_db(tmp_path, mode="single"), TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"username": "admin", "password": PW})
        _setup(c, [])
        c.patch("/api/v1/me", json={"email": "ich@example.de"})
        c.post("/api/v1/tasks", json={"title": "Fällig", "due_date": D(0)})
        with connect() as conn:
            for _ in range(5):
                reminders.run_due(conn, _at(8))
            log = conn.execute("SELECT status, detail FROM reminder_log").fetchone()
        assert log["status"] == "error" and log["detail"].startswith("3|Server weg")


def test_reminder_test_endpoint(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(reminders, "send_mail", lambda conn, to, subject, body: sent.append(subject))
    with fresh_db(tmp_path, mode="single"), TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"username": "admin", "password": PW})
        assert c.post("/api/v1/me/reminder-test").status_code == 400          # keine Adresse
        c.patch("/api/v1/me", json={"email": "ich@example.de"})
        assert "nichts fällig" in c.post("/api/v1/me/reminder-test").json()["message"]
        c.post("/api/v1/tasks", json={"title": "Fällig", "due_date": D(0)})
        assert "1 Aufgabe an ich@example.de" in c.post("/api/v1/me/reminder-test").json()["message"]
        assert sent == ["1 Aufgabe fällig"]
