"""Projektzeiten: Stoppuhr, Nachtragen, Überschneidungen, Rechte."""
import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app
from app.timeutil import now_utc, to_local, to_utc_str, today_local

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
Y = (today_local() - timedelta(days=1)).isoformat()  # gestern – sicher abgeschlossen


def client(user="admin", pw=PW):
    c = TestClient(app)
    c.__enter__()
    assert c.post("/api/v1/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c


@pytest.fixture()
def single(tmp_path):
    with fresh_db(tmp_path, mode="single"):
        c = client()
        cust = c.post("/api/v1/customers", json={"name": "Physio Henze"}).json()["id"]
        p = c.post("/api/v1/projects", json={"kind": "project", "name": "Website", "customer_id": cust}).json()["id"]
        lst = c.post("/api/v1/projects", json={"kind": "list", "name": "Einkauf"}).json()["id"]
        yield {"c": c, "p": p, "list": lst, "cust": cust}


def test_stopwatch_and_switch(single):
    c, p = single["c"], single["p"]
    assert c.get("/api/v1/times/running").json()["running"] is None
    assert c.post("/api/v1/times/stop").status_code == 409
    task = c.post("/api/v1/tasks", json={"title": "Texte", "project_id": p}).json()["id"]
    r = c.post("/api/v1/times/start", json={"task_id": task, "note": "Startseite"}).json()
    assert r["running"]["project_id"] == p and r["running"]["task"] == "Texte" and r["stopped"] is None
    first = r["running"]["id"]
    # zweites Projekt starten → das erste wird automatisch beendet
    p2 = c.post("/api/v1/projects", json={"kind": "project", "name": "Intern"}).json()["id"]
    r = c.post("/api/v1/times/start", json={"project_id": p2, "on_site": True}).json()
    assert r["stopped"]["id"] == first and not r["stopped"]["running"] and r["running"]["on_site"]
    assert c.get("/api/v1/times/running").json()["running"]["project"] == "Intern"
    r = c.post("/api/v1/times/stop", json={"note": "fertig"}).json()
    assert r["stopped"]["note"] == "fertig" and r["running"] is None
    today = today_local().isoformat()
    lst = c.get("/api/v1/times", params={"from": today, "to": today}).json()
    assert len(lst) == 2 and all(not e["running"] for e in lst)


def test_targets(single):
    c = single["c"]
    r = c.post("/api/v1/times/start", json={"project_id": single["list"]})
    assert r.status_code == 400 and "Aufgabenliste" in r.json()["detail"]
    t = c.post("/api/v1/tasks", json={"title": "ohne Projekt"}).json()["id"]
    assert "keinem Projekt" in c.post("/api/v1/times/start", json={"task_id": t}).json()["detail"]
    t2 = c.post("/api/v1/tasks", json={"title": "x", "project_id": single["p"]}).json()["id"]
    other = c.post("/api/v1/projects", json={"kind": "project", "name": "Anderes"}).json()["id"]
    assert c.post("/api/v1/times/start", json={"project_id": other, "task_id": t2}).status_code == 400
    assert c.post("/api/v1/times/start", json={"project_id": 999}).status_code == 404
    c.patch(f"/api/v1/projects/{other}", json={"archived": True})
    assert "archiviert" in c.post("/api/v1/times/start", json={"project_id": other}).json()["detail"]


def test_manual_entries_and_overlaps(single):
    c, p = single["c"], single["p"]
    e = c.post("/api/v1/times", json={"project_id": p, "start": f"{Y}T09:00", "end": f"{Y}T10:45", "note": "=SUMME(A1)"}).json()
    assert e["minutes"] == 105 and e["day"] == Y and e["start"].startswith(f"{Y}T09:00")
    r = c.post("/api/v1/times", json={"project_id": p, "start": f"{Y}T10:30", "end": f"{Y}T11:00"})
    assert r.status_code == 409 and "Überschneidet" in r.json()["detail"]
    assert c.post("/api/v1/times", json={"project_id": p, "start": f"{Y}T10:45", "end": f"{Y}T11:00"}).status_code == 201  # direkt danach ok
    assert c.post("/api/v1/times", json={"project_id": p, "start": f"{Y}T12:00", "end": f"{Y}T11:00"}).status_code == 400
    r = c.post("/api/v1/times", json={"project_id": p, "start": f"{Y}T00:00", "end": f"{today_local().isoformat()}T01:00"})
    assert r.status_code == 400 and "24 Stunden" in r.json()["detail"]
    # ändern
    r = c.patch(f"/api/v1/times/{e['id']}", json={"end": f"{Y}T10:00", "on_site": True}).json()
    assert r["minutes"] == 60 and r["on_site"]
    assert c.patch(f"/api/v1/times/{e['id']}", json={"end": f"{Y}T11:00"}).status_code == 409
    assert c.delete(f"/api/v1/times/{e['id']}").json()["ok"]
    assert c.patch(f"/api/v1/times/{e['id']}", json={"note": "x"}).status_code == 404
    log = c.get("/api/v1/audit", params={"category": "zeiten"}).json()["entries"]
    assert {"time_added", "time_updated", "time_deleted"} <= {x["action"] for x in log}
    # Projekt mit Zeiten lässt sich nicht löschen
    assert "gebucht" in c.delete(f"/api/v1/projects/{p}").json()["detail"]


def test_forgotten_stopwatch_is_capped(single):
    c, p = single["c"], single["p"]
    rid = c.post("/api/v1/times/start", json={"project_id": p}).json()["running"]["id"]
    with connect() as conn:  # vor 30 Stunden gestartet
        conn.execute("UPDATE time_entries SET start_utc = ? WHERE id = ?", (to_utc_str(now_utc() - timedelta(hours=30)), rid))
    r = c.post("/api/v1/times/stop").json()["stopped"]
    assert r["minutes"] == 24 * 60 and "gekürzt" in r["note"]


def test_running_start_can_be_corrected(single):
    c, p = single["c"], single["p"]
    rid = c.post("/api/v1/times/start", json={"project_id": p}).json()["running"]["id"]
    earlier = to_local(now_utc() - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")
    r = c.patch(f"/api/v1/times/{rid}", json={"start": earlier}).json()
    assert r["running"] and 29 <= r["minutes"] <= 31
    later = to_local(now_utc() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    assert c.patch(f"/api/v1/times/{rid}", json={"start": later}).status_code == 400


def test_team_rights(tmp_path):
    with fresh_db(tmp_path, mode="team"):
        chef = client()
        anna_id = chef.post("/api/v1/users", json={"username": "anna", "display_name": "Anna", "password": "geheim123"}).json()["id"]
        chef.post("/api/v1/users", json={"username": "ben", "display_name": "Ben", "password": "geheim123"})
        p = chef.post("/api/v1/projects", json={"kind": "project", "name": "Website", "member_ids": [anna_id]}).json()["id"]
        anna, ben = client("anna", "geheim123"), client("ben", "geheim123")
        e = anna.post("/api/v1/times", json={"project_id": p, "start": f"{Y}T09:00", "end": f"{Y}T10:00"}).json()
        assert ben.post("/api/v1/times", json={"project_id": p, "start": f"{Y}T09:00", "end": f"{Y}T10:00"}).status_code == 404
        assert ben.patch(f"/api/v1/times/{e['id']}", json={"note": "x"}).status_code == 404
        assert ben.get("/api/v1/times", params={"from": Y, "to": Y, "user_id": anna_id}).json() == []   # sieht nur eigene
        assert anna.post("/api/v1/times", json={"project_id": p, "start": f"{Y}T11:00", "end": f"{Y}T12:00", "user_id": 1}).status_code == 403
        # der Chef sieht und ändert alles, trägt auch für andere nach
        assert [x["id"] for x in chef.get("/api/v1/times", params={"from": Y, "to": Y, "user_id": anna_id}).json()] == [e["id"]]
        r = chef.post("/api/v1/times", json={"project_id": p, "start": f"{Y}T13:00", "end": f"{Y}T14:00", "user_id": anna_id})
        assert r.status_code == 201 and r.json()["user_name"] == "Anna"
        assert chef.patch(f"/api/v1/times/{e['id']}", json={"note": "geprüft"}).json()["note"] == "geprüft"
        assert len(chef.get("/api/v1/times", params={"from": Y, "to": Y, "all_users": True}).json()) == 2
        assert len(anna.get("/api/v1/times", params={"from": Y, "to": Y, "all_users": True}).json()) == 2  # nur ihre eigenen
