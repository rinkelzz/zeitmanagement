"""Aufgaben, Listen, Kunden – inklusive Rechte im Team-Modus."""
import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.timeutil import today_local

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
TODAY = today_local()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731


def client(user="admin", pw=PW):
    c = TestClient(app)
    c.__enter__()
    assert c.post("/api/v1/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c


@pytest.fixture()
def team(tmp_path):
    with fresh_db(tmp_path, mode="team"):
        chef = client()
        ids = {}
        for name in ("anna", "ben"):
            ids[name] = chef.post("/api/v1/users", json={"username": name, "display_name": name.title(),
                                                         "password": "geheim123"}).json()["id"]
        yield {"chef": chef, "anna": client("anna", "geheim123"), "ben": client("ben", "geheim123"), "ids": ids}


def test_single_mode_basics(tmp_path):
    with fresh_db(tmp_path, mode="single"):
        c = client()
        lst = c.post("/api/v1/projects", json={"kind": "list", "name": "Einkauf", "color": "#94a3b8"}).json()
        assert lst["kind"] == "list" and lst["member_ids"] == [] and lst["can_manage"]
        t = c.post("/api/v1/tasks", json={"text": "Toner bestellen morgen !hoch #einkauf @anna"}).json()
        assert t["title"] == "Toner bestellen @anna" and t["due_date"] == D(1) and t["priority"] == 1
        assert t["project_id"] == lst["id"] and t["assignee_ids"] == []
        # Zuständige werden im Einzelperson-Modus ignoriert
        r = c.post("/api/v1/tasks", json={"title": "Ohne Liste", "assignee_ids": [1]}).json()
        assert r["assignee_ids"] == [] and r["project_id"] is None
        assert c.post("/api/v1/tasks", json={"title": "  "}).status_code == 400
        assert c.post("/api/v1/tasks", json={"text": "morgen !hoch"}).status_code == 400  # nur Kürzel, kein Titel


def test_views_order_and_counts(tmp_path):
    with fresh_db(tmp_path, mode="single"):
        c = client()
        mk = lambda title, **kw: c.post("/api/v1/tasks", json={"title": title, **kw}).json()["id"]  # noqa: E731
        a = mk("alt", due_date=D(-3))
        b = mk("heute wichtig", due_date=D(0), priority=1)
        h = mk("heute normal", due_date=D(0))
        u = mk("bald", due_date=D(5))
        n = mk("irgendwann")
        assert [t["id"] for t in c.get("/api/v1/tasks", params={"view": "open"}).json()] == [a, b, h, u, n]
        assert [t["id"] for t in c.get("/api/v1/tasks", params={"view": "today"}).json()] == [b, h]
        assert [t["id"] for t in c.get("/api/v1/tasks", params={"view": "overdue"}).json()] == [a]
        assert [t["id"] for t in c.get("/api/v1/tasks", params={"view": "upcoming"}).json()] == [u]
        assert c.get("/api/v1/tasks/counts").json() == {"today": 2, "overdue": 1, "upcoming": 1, "mine": 5, "open": 5, "inbox": 5}
        # erledigen und wieder öffnen
        r = c.patch(f"/api/v1/tasks/{a}", json={"done": True}).json()
        assert r["done"] and r["completed_at"] and r["completed_by_name"] == "Administrator"
        assert [t["id"] for t in c.get("/api/v1/tasks", params={"view": "done"}).json()] == [a]
        assert c.get("/api/v1/tasks/counts").json()["overdue"] == 0
        assert not c.patch(f"/api/v1/tasks/{a}", json={"done": False}).json()["done"]
        # ändern: Datum entfernen, Notiz, Priorität
        r = c.patch(f"/api/v1/tasks/{u}", json={"due_date": None, "notes": "Details", "priority": 2, "title": "neu"}).json()
        assert r["due_date"] is None and r["notes"] == "Details" and r["priority"] == 2 and r["title"] == "neu"
        assert c.get("/api/v1/tasks", params={"q": "Details"}).json()[0]["id"] == u
        assert c.get("/api/v1/tasks", params={"view": "quatsch"}).status_code == 400
        assert c.delete(f"/api/v1/tasks/{n}").json()["ok"]
        assert c.patch(f"/api/v1/tasks/{n}", json={"title": "x"}).status_code == 404
        log = c.get("/api/v1/audit", params={"category": "aufgaben"}).json()["entries"]
        assert {"task_created", "task_done", "task_reopened", "task_updated", "task_deleted"} <= {e["action"] for e in log}


def test_parse_preview(tmp_path):
    with fresh_db(tmp_path, mode="team"):
        c = client()
        c.post("/api/v1/projects", json={"kind": "list", "name": "Büro allgemein"})
        r = c.get("/api/v1/tasks/parse", params={"text": "Post holen morgen #büro @admin !2"}).json()
        assert r["title"] == "Post holen" and r["project"] == "Büro allgemein" and r["assignees"] == ["Administrator"]
        assert r["priority"] == 2 and r["due_date"] == D(1)


def test_team_rights(team):
    chef, anna, ben, ids = team["chef"], team["anna"], team["ben"], team["ids"]
    cust = chef.post("/api/v1/customers", json={"name": "Physio Henze"}).json()
    assert chef.post("/api/v1/customers", json={"name": "physio henze"}).status_code == 409
    assert anna.post("/api/v1/customers", json={"name": "X"}).status_code == 403
    proj = chef.post("/api/v1/projects", json={"kind": "project", "name": "Website", "customer_id": cust["id"],
                                               "member_ids": [ids["anna"]]}).json()
    assert proj["customer"] == "Physio Henze" and set(proj["member_ids"]) == {1, ids["anna"]}
    # Mitarbeiter dürfen keine Projekte anlegen, aber Listen
    assert anna.post("/api/v1/projects", json={"kind": "project", "name": "Eigenes"}).status_code == 403
    alist = anna.post("/api/v1/projects", json={"kind": "list", "name": "Annas Liste"}).json()
    assert alist["member_ids"] == [ids["anna"]] and alist["can_manage"]

    # Sichtbarkeit von Projekten
    assert {p["name"] for p in anna.get("/api/v1/projects").json()} == {"Website", "Annas Liste"}
    assert ben.get("/api/v1/projects").json() == []
    assert {p["name"] for p in chef.get("/api/v1/projects").json()} == {"Website", "Annas Liste"}
    assert [c["name"] for c in anna.get("/api/v1/customers").json()] == ["Physio Henze"]
    assert ben.get("/api/v1/customers").json() == []

    t1 = chef.post("/api/v1/tasks", json={"title": "Texte einpflegen", "project_id": proj["id"], "assignee_ids": [ids["anna"]]}).json()
    t2 = chef.post("/api/v1/tasks", json={"title": "Für Ben", "project_id": proj["id"], "assignee_ids": [ids["ben"]]}).json()
    t3 = ben.post("/api/v1/tasks", json={"title": "Bens Notiz"}).json()
    assert t3["project_id"] is None
    # Ben ist nicht im Projekt, sieht aber die ihm zugewiesene Aufgabe – und seine eigene
    assert {t["id"] for t in ben.get("/api/v1/tasks", params={"view": "open"}).json()} == {t2["id"], t3["id"]}
    assert ben.patch(f"/api/v1/tasks/{t1['id']}", json={"done": True}).status_code == 404
    assert ben.post("/api/v1/tasks", json={"title": "x", "project_id": proj["id"]}).status_code == 404
    # Anna sieht das ganze Projekt, aber nicht Bens private Aufgabe
    assert {t["id"] for t in anna.get("/api/v1/tasks", params={"view": "open"}).json()} == {t1["id"], t2["id"]}
    assert [t["id"] for t in anna.get("/api/v1/tasks", params={"view": "mine"}).json()] == [t1["id"]]
    assert anna.patch(f"/api/v1/tasks/{t3['id']}", json={"title": "x"}).status_code == 404
    # Löschen nur Ersteller oder Chef
    assert anna.delete(f"/api/v1/tasks/{t2['id']}").status_code == 403
    assert ben.delete(f"/api/v1/tasks/{t3['id']}").json()["ok"]
    # Schnelleingabe mit fremder Liste: #liste wird nur unter sichtbaren gesucht
    r = ben.post("/api/v1/tasks", json={"text": "Test #website"}).json()
    assert r["project_id"] is None and "#website" in r["title"]
    # Zuständige: nur aktive Benutzer
    assert chef.post("/api/v1/tasks", json={"title": "x", "assignee_ids": [999]}).status_code == 400
    # Liste verwalten: nur Ersteller oder Chef
    assert ben.patch(f"/api/v1/projects/{alist['id']}", json={"name": "geklaut"}).status_code == 404
    assert anna.patch(f"/api/v1/projects/{proj['id']}", json={"name": "geändert"}).status_code == 403
    r = anna.patch(f"/api/v1/projects/{alist['id']}", json={"member_ids": [ids["ben"]]}).json()
    assert set(r["member_ids"]) == {ids["anna"], ids["ben"]}  # sie bleibt selbst drin
    assert "Annas Liste" in {p["name"] for p in ben.get("/api/v1/projects").json()}
    # Personenliste für Auswahlfelder
    assert {p["username"] for p in ben.get("/api/v1/people").json()} == {"admin", "anna", "ben"}


def test_archive_and_delete_project(team):
    chef = team["chef"]
    p = chef.post("/api/v1/projects", json={"kind": "list", "name": "Alt"}).json()
    t = chef.post("/api/v1/tasks", json={"title": "drin", "project_id": p["id"]}).json()
    r = chef.delete(f"/api/v1/projects/{p['id']}")
    assert r.status_code == 400 and "1 Aufgabe" in r.json()["detail"]
    chef.patch(f"/api/v1/projects/{p['id']}", json={"archived": True})
    assert "Alt" not in {x["name"] for x in chef.get("/api/v1/projects").json()}
    assert "Alt" in {x["name"] for x in chef.get("/api/v1/projects", params={"include_archived": True}).json()}
    assert chef.post("/api/v1/tasks", json={"title": "neu", "project_id": p["id"]}).status_code == 400
    chef.patch(f"/api/v1/tasks/{t['id']}", json={"project_id": None})
    assert chef.delete(f"/api/v1/projects/{p['id']}").json()["ok"]
    assert chef.post("/api/v1/projects", json={"kind": "x", "name": "y"}).status_code == 400
    assert chef.post("/api/v1/projects", json={"kind": "list", "name": "y", "color": "rot"}).status_code == 422


def test_project_counts(team):
    chef = team["chef"]
    p = chef.post("/api/v1/projects", json={"kind": "list", "name": "Zähler"}).json()
    chef.post("/api/v1/tasks", json={"title": "a", "project_id": p["id"], "due_date": D(-1)})
    chef.post("/api/v1/tasks", json={"title": "b", "project_id": p["id"]})
    got = next(x for x in chef.get("/api/v1/projects").json() if x["id"] == p["id"])
    assert got["open"] == 2 and got["overdue"] == 1
