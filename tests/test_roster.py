"""Dienstplan: Schichtarten, Schichten, Überschneidungen, Kopieren, Veröffentlichen, PDF, Mail."""
import os

from fastapi.testclient import TestClient

from app import roster
from app.main import app

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
WEEK = "2026-11-09"   # Montag


def client(user="admin", pw=PW):
    c = TestClient(app, base_url="https://zeit.example.de")
    c.__enter__()
    assert c.post("/api/v1/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c


def team(tmp_path):
    chef = client()
    ids = {}
    for n in ("anna", "ben"):
        ids[n] = chef.post("/api/v1/users", json={"username": n, "display_name": n.title(), "password": "geheim123",
                                                   "email": f"{n}@example.de"}).json()["id"]
    return chef, ids


def types(c):
    return {t["short"]: t for t in c.get("/api/v1/shift-types").json()}


def put(c, uid, day, **kw):
    return c.post("/api/v1/shifts", json={"user_id": uid, "day": day, **kw})


def test_minutes_and_span():
    assert roster.minutes("06:00", "14:00", 30) == 450
    assert roster.minutes("22:00", "06:00", 0) == 480        # über Mitternacht
    assert roster.minutes("08:00", "08:00", 0) == 1440
    assert roster.minutes(None, None) == 0
    s, e = roster.span("2026-11-09", "22:00", "06:00")
    assert s.day == 9 and e.day == 10 and e.hour == 6


def test_default_types_and_crud(tmp_path):
    with fresh_db(tmp_path, mode="team"):
        chef, ids = team(tmp_path)
        t = types(chef)
        assert set(t) >= {"F", "S", "N", "U", "K"} and t["F"]["minutes"] == 450 and t["U"]["kind"] == "off"
        r = chef.post("/api/v1/shift-types", json={"name": "Mittel", "short": "M", "start_time": "10:00", "end_time": "18:00",
                                                   "break_minutes": 30, "color": "#AABBCC"})
        assert r.status_code == 201 and r.json()["color"] == "#aabbcc"
        assert chef.post("/api/v1/shift-types", json={"name": "x", "short": "X"}).status_code == 400      # Dienst ohne Zeit
        assert chef.post("/api/v1/shift-types", json={"name": "x", "short": "X", "start_time": "25:00",
                                                      "end_time": "10:00"}).status_code == 422
        m = r.json()["id"]
        assert chef.patch(f"/api/v1/shift-types/{m}", json={"archived": True}).json()["archived"]
        assert "M" not in types(chef)
        anna = client("anna", "geheim123")
        assert anna.post("/api/v1/shift-types", json={"name": "y", "short": "Y", "kind": "off"}).status_code == 403
        assert put(anna, ids["anna"], WEEK, shift_type_id=t["F"]["id"]).status_code == 403


def test_plan_overlap_publish_visibility(tmp_path, monkeypatch):
    with fresh_db(tmp_path, mode="team"):
        chef, ids = team(tmp_path)
        t = types(chef)
        a = ids["anna"]
        f = put(chef, a, WEEK, shift_type_id=t["F"]["id"], note="Kasse")
        assert f.status_code == 201 and f.json()["label"] == "Frühdienst" and f.json()["minutes"] == 450
        # Überschneidung am selben Tag
        assert put(chef, a, WEEK, start_time="13:00", end_time="15:00").status_code == 409
        # Nachtdienst am Vortag ragt in den Frühdienst
        assert put(chef, a, "2026-11-08", shift_type_id=t["N"]["id"], end_time="07:00").status_code == 409
        n = put(chef, a, "2026-11-08", shift_type_id=t["N"]["id"])          # bis 06:00 grenzt nur an
        assert n.status_code == 201 and chef.delete(f"/api/v1/shifts/{n.json()['id']}").status_code == 200
        # geteilter Dienst am selben Tag geht
        assert put(chef, a, WEEK, start_time="18:00", end_time="21:00").status_code == 201
        # Abwesenheit und Dienst schließen sich aus – außer mit replace
        assert put(chef, a, WEEK, shift_type_id=t["U"]["id"]).status_code == 409
        u = put(chef, a, "2026-11-10", shift_type_id=t["U"]["id"])
        assert u.status_code == 201 and u.json()["minutes"] == 0
        assert put(chef, a, "2026-11-10", shift_type_id=t["F"]["id"]).status_code == 409
        assert put(chef, a, "2026-11-10", shift_type_id=t["F"]["id"], replace=True).status_code == 201
        put(chef, ids["ben"], WEEK, shift_type_id=t["S"]["id"])
        put(chef, ids["ben"], "2026-11-15", shift_type_id=t["N"]["id"])   # Sonntag über Mitternacht
        w = chef.get("/api/v1/roster", params={"week": "2026-11-12"}).json()
        assert w["week_start"] == WEEK and not w["published"] and w["can_edit"]
        assert w["totals"][str(a)] == 450 + 180 + 450 and w["totals"][str(ids["ben"])] == 450 + 450
        assert w["coverage"][0]["working"] == 2 and w["coverage"][0]["by_type"] == {"F": 1, "eigene Zeit": 1, "S": 1}
        # Mitarbeiter sieht unveröffentlichte Woche nicht
        anna = client("anna", "geheim123")
        aw = anna.get("/api/v1/roster", params={"week": WEEK}).json()
        assert aw["shifts"] == [] and not aw["visible"] and not aw["can_edit"]
        assert anna.get("/api/v1/roster/pdf", params={"week": WEEK}).status_code == 404
        # veröffentlichen mit Mail
        sent = []
        monkeypatch.setattr(roster, "send_mail", lambda conn, to, subject, body: sent.append((to, subject, body)))
        r = chef.post("/api/v1/roster/publish", json={"week": WEEK}).json()
        assert r["sent"] == 2 and {s[0] for s in sent} == {"anna@example.de", "ben@example.de"}
        anna_mail = [s for s in sent if s[0] == "anna@example.de"][0]
        assert "KW 46" in anna_mail[1] and "Frühdienst 06:00–14:00 (Kasse)" in anna_mail[2] and "Summe: 18:00" in anna_mail[2]
        aw = anna.get("/api/v1/roster", params={"week": WEEK}).json()
        assert aw["published"] and len(aw["shifts"]) == 5          # ganzes Team sichtbar
        pdf = anna.get("/api/v1/roster/pdf", params={"week": WEEK})
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and b"/MediaBox [0 0 841.89 595.28]" in pdf.content
        assert chef.post("/api/v1/roster/unpublish", json={"week": WEEK}).json()["ok"]
        assert anna.get("/api/v1/roster", params={"week": WEEK}).json()["shifts"] == []


def test_edit_delete_copy_snapshot(tmp_path):
    with fresh_db(tmp_path, mode="team"):
        chef, ids = team(tmp_path)
        t = types(chef)
        a, b = ids["anna"], ids["ben"]
        s = put(chef, a, WEEK, shift_type_id=t["F"]["id"]).json()
        # eigene Zeiten innerhalb der Schichtart
        u = chef.patch(f"/api/v1/shifts/{s['id']}", json={"start_time": "07:00", "note": "später"}).json()
        assert u["start_time"] == "07:00" and u["end_time"] == "14:00" and u["label"] == "Frühdienst" and u["note"] == "später"
        # Schichtart wechseln übernimmt deren Zeiten
        u = chef.patch(f"/api/v1/shifts/{s['id']}", json={"shift_type_id": t["S"]["id"]}).json()
        assert u["start_time"] == "14:00" and u["short"] == "S"
        # Schichtart ändern verändert alte Schicht nicht
        chef.patch(f"/api/v1/shift-types/{t['S']['id']}", json={"name": "Spät", "start_time": "13:00"})
        w = chef.get("/api/v1/roster", params={"week": WEEK}).json()
        assert w["shifts"][0]["label"] == "Spätdienst" and w["shifts"][0]["start_time"] == "14:00"
        put(chef, b, "2026-11-11", shift_type_id=t["K"]["id"])
        # kopieren
        r = chef.post("/api/v1/roster/copy", json={"from_week": WEEK, "to_week": "2026-11-18"}).json()
        assert r == {"copied": 2, "skipped": 0}
        nw = chef.get("/api/v1/roster", params={"week": "2026-11-16"}).json()
        assert sorted(x["day"] for x in nw["shifts"]) == ["2026-11-16", "2026-11-18"]
        assert chef.post("/api/v1/roster/copy", json={"from_week": WEEK, "to_week": WEEK}).status_code == 400
        # ohne replace: doppelte Dienste werden übersprungen
        r = chef.post("/api/v1/roster/copy", json={"from_week": WEEK, "to_week": "2026-11-16", "replace": False}).json()
        assert r["skipped"] == 1
        assert chef.delete(f"/api/v1/shifts/{s['id']}").json() == {"ok": True}
        assert chef.delete(f"/api/v1/shifts/{s['id']}").status_code == 404
        # aus dem Plan ausblenden
        chef.patch(f"/api/v1/roster/users/{b}", json={"in_roster": False})
        names = [x["display_name"] for x in chef.get("/api/v1/roster", params={"week": WEEK}).json()["users"]]
        assert "Ben (nicht im Plan)" in names and "Anna" in names
        log = chef.get("/api/v1/audit", params={"category": "dienstplan"}).json()
        assert any(e["action"] == "roster_copied" for e in log["entries"])


def test_single_mode(tmp_path):
    with fresh_db(tmp_path, mode="single"):
        c = client()
        me = c.get("/api/v1/me").json()
        assert put(c, me["id"], WEEK, start_time="09:00", end_time="17:00", break_minutes=60).status_code == 201
        w = c.get("/api/v1/roster", params={"week": WEEK}).json()
        assert [u["id"] for u in w["users"]] == [me["id"]] and w["totals"][str(me["id"])] == 420


def test_calendar_and_ical(tmp_path):
    with fresh_db(tmp_path, mode="team"):
        chef, ids = team(tmp_path)
        t = types(chef)
        a = ids["anna"]
        put(chef, a, WEEK, shift_type_id=t["N"]["id"], note="Station 2")
        put(chef, a, "2026-11-11", shift_type_id=t["U"]["id"])
        anna = client("anna", "geheim123")
        cal = lambda: anna.get("/api/v1/events", params={"from": WEEK, "to": "2026-11-15"}).json()["shifts"]
        assert cal() == []                                     # Entwurf: nicht im Kalender
        chef.post("/api/v1/roster/publish", json={"week": WEEK, "notify": False})
        assert [s["label"] for s in cal()] == ["Nachtdienst", "Urlaub"]
        url = anna.post("/api/v1/me/calendar-feed", json={"with_tasks": False}).json()["url"]
        body = anna.get(url.split("zeit.example.de", 1)[1]).text
        assert "SUMMARY:Nachtdienst" in body and "DTEND;TZID=Europe/Berlin:20261110T060000" in body
        assert "DESCRIPTION:Station 2" in body and "DTSTART;VALUE=DATE:20261111" in body
        # Chef selbst ist nicht eingeplant – sein Kalender bleibt leer
        assert chef.get("/api/v1/events", params={"from": WEEK, "to": "2026-11-15"}).json()["shifts"] == []
