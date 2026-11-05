"""Kalender: Termine, Serien, Ausnahmen, Sichtbarkeit und iCal-Abo."""
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import calendar_core, ical
from app.main import app
from app.timeutil import today_local

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]


def client(user="admin", pw=PW):
    c = TestClient(app, base_url="https://zeit.example.de")
    c.__enter__()
    assert c.post("/api/v1/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c


@pytest.fixture()
def c(tmp_path):
    with fresh_db(tmp_path, mode="single"):
        yield client()


def ev(c, **kw):
    body = {"title": "Termin", "start": "2026-11-10T09:00", "end": "2026-11-10T10:30", **kw}
    r = c.post("/api/v1/events", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def rng(c, a, b, **kw):
    return c.get("/api/v1/events", params={"from": a, "to": b, **kw}).json()


def test_single_and_all_day(c):
    e = ev(c, title="Physio Henze vor Ort", location="Hauptstr. 1", alarm_minutes=30)
    assert e["start"].startswith("2026-11-10T09:00") and e["end"].startswith("2026-11-10T10:30") and e["alarm_minutes"] == 30
    a = ev(c, title="Urlaub", start="2026-11-12T00:00", end="2026-11-14T00:00", all_day=True)
    assert a["all_day"] and a["day"] == "2026-11-12" and a["last_day"] == "2026-11-14"
    r = rng(c, "2026-11-09", "2026-11-15")
    assert [x["title"] for x in r["events"]] == ["Urlaub", "Physio Henze vor Ort"]
    assert rng(c, "2026-11-13", "2026-11-13")["events"][0]["title"] == "Urlaub"        # mehrtägig: auch mittendrin
    assert rng(c, "2026-11-15", "2026-11-20")["events"] == []
    assert c.post("/api/v1/events", json={"title": "x", "start": "2026-11-10T10:00", "end": "2026-11-10T09:00"}).status_code == 400
    assert c.post("/api/v1/events", json={"title": "x", "start": "2026-11-10T10:00", "end": "2026-11-10T11:00", "alarm_minutes": 7}).status_code == 400
    assert c.get("/api/v1/events", params={"from": "2026-01-01", "to": "2026-06-01"}).status_code == 400
    # ändern
    u = c.patch(f"/api/v1/events/{e['id']}", json={"start": "2026-11-10T13:00", "end": "2026-11-10T14:00", "title": "Neu"}).json()
    assert u["title"] == "Neu" and u["start"].startswith("2026-11-10T13:00")
    u = c.patch(f"/api/v1/events/{a['id']}", json={"title": "Urlaub!"}).json()            # ganztägig bleibt gleich lang
    assert u["day"] == "2026-11-12" and u["last_day"] == "2026-11-14"
    assert c.delete(f"/api/v1/events/{e['id']}").json() == {"ok": True, "series": False}
    assert c.get(f"/api/v1/events/{e['id']}").status_code == 404


def test_series_dst_and_exceptions(c):
    # wöchentlich Mo 09:00 über die Zeitumstellung Ende Oktober
    s = ev(c, title="Teamrunde", start="2026-10-19T09:00", end="2026-10-19T09:30", recur="w:1", recur_until="2026-11-16")
    occ = rng(c, "2026-10-01", "2026-11-30")["events"]
    assert [o["day"] for o in occ] == ["2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"]
    assert all(o["start"][11:16] == "09:00" for o in occ)                                   # auch nach der Umstellung
    assert occ[0]["start"].endswith("+02:00") and occ[-1]["start"].endswith("+01:00")
    r = c.delete(f"/api/v1/events/{s['id']}", params={"day": "2026-11-02"}).json()
    assert r == {"ok": True, "series": False}
    assert "2026-11-02" not in [o["day"] for o in rng(c, "2026-10-01", "2026-11-30")["events"]]
    m = ev(c, title="Monatsende", start="2026-10-31T00:00", end="2026-10-31T00:00", all_day=True, recur="m:1")
    assert m["recur"] == "m:1@31"
    days = [o["day"] for o in rng(c, "2026-10-01", "2026-11-30")["events"] if o["title"] == "Monatsende"]
    assert days == ["2026-10-31", "2026-11-30"]
    werk = ev(c, title="Post", start="2026-11-06T08:00", end="2026-11-06T08:15", recur="wd")
    assert [o["day"] for o in rng(c, "2026-11-06", "2026-11-10")["events"] if o["id"] == werk["id"]] == ["2026-11-06", "2026-11-09", "2026-11-10"]
    assert c.post("/api/v1/events", json={"title": "x", "start": "2026-11-10T09:00", "end": "2026-11-10T10:00", "recur": "z:1"}).status_code == 400
    # ganze Serie löschen
    assert c.delete(f"/api/v1/events/{s['id']}").json()["series"] is True


def test_tasks_shown_in_calendar(c):
    c.post("/api/v1/tasks", json={"title": "Angebot", "due_date": "2026-11-11", "due_time": "14:00"})
    c.post("/api/v1/tasks", json={"title": "Ohne Zeit", "due_date": "2026-11-11"})
    r = rng(c, "2026-11-09", "2026-11-15")
    assert [(t["title"], t["time"]) for t in r["tasks"]] == [("Angebot", "14:00"), ("Ohne Zeit", None)]
    assert rng(c, "2026-11-09", "2026-11-15", with_tasks=False)["tasks"] == []


def test_team_visibility(tmp_path):
    with fresh_db(tmp_path, mode="team"):
        chef = client()
        ids = {n: chef.post("/api/v1/users", json={"username": n, "display_name": n.title(), "password": "geheim123"}).json()["id"]
               for n in ("anna", "ben")}
        anna, ben = client("anna", "geheim123"), client("ben", "geheim123")
        e = ev(anna, title="Kundentermin", attendee_ids=[ids["ben"], ids["ben"]])
        assert e["attendees"] == ["Ben"] and e["can_edit"]
        p = ev(anna, title="Privat")
        assert [x["title"] for x in rng(ben, "2026-11-10", "2026-11-10")["events"]] == ["Kundentermin"]
        assert rng(ben, "2026-11-10", "2026-11-10")["events"][0]["can_edit"] is False
        assert ben.patch(f"/api/v1/events/{e['id']}", json={"title": "x"}).status_code == 403
        assert ben.delete(f"/api/v1/events/{p['id']}").status_code == 404
        assert {x["title"] for x in rng(chef, "2026-11-10", "2026-11-10")["events"]} == {"Kundentermin", "Privat"}
        assert chef.patch(f"/api/v1/events/{p['id']}", json={"notes": "vom Chef"}).status_code == 200
        r = anna.patch(f"/api/v1/events/{e['id']}", json={"attendee_ids": []}).json()
        assert r["attendees"] == [] and rng(ben, "2026-11-10", "2026-11-10")["events"] == []
        assert anna.post("/api/v1/events", json={"title": "x", "start": "2026-11-10T09:00", "end": "2026-11-10T10:00",
                                                 "attendee_ids": [999]}).status_code == 400


def test_ical_feed(c):
    ev(c, title="Teamrunde; wichtig", start=f"{today_local():%Y-%m-%d}T09:00", end=f"{today_local():%Y-%m-%d}T09:30",
       recur="w:1", alarm_minutes=15, location="Büro, 1. OG", notes="Zeile 1\nZeile 2")
    s = ev(c, title="Monatsabschluss", start=f"{today_local():%Y-%m-%d}T00:00", end=f"{today_local():%Y-%m-%d}T00:00",
           all_day=True, recur="m:1")
    c.delete(f"/api/v1/events/{s['id']}", params={"day": today_local().isoformat()})
    c.post("/api/v1/tasks", json={"title": "Backup prüfen", "due_date": today_local().isoformat(), "due_time": "08:30"})
    st = c.get("/api/v1/me/calendar-feed").json()
    assert st["active"] is False and st["public"] is True
    r = c.post("/api/v1/me/calendar-feed", json={"with_tasks": True}).json()
    assert r["url"].startswith("https://zeit.example.de/ical/") and r["webcal"].startswith("webcal://zeit.example.de/ical/")
    path = "/" + r["url"].split("/", 3)[3]
    with TestClient(app) as anon:
        resp = anon.get(path)
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/calendar")
        text = resp.text
        assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
        assert "BEGIN:VTIMEZONE" in text and "TZID:Europe/Berlin" in text
        assert "SUMMARY:Teamrunde\\; wichtig" in text and "LOCATION:Büro\\, 1. OG" in text and "Zeile 1\\nZeile 2" in text
        assert "RRULE:FREQ=WEEKLY" in text and "TRIGGER:-PT15M" in text
        assert f"RRULE:FREQ=MONTHLY;BYMONTHDAY={today_local().day}" in text and f"EXDATE;VALUE=DATE:{today_local():%Y%m%d}" in text
        assert "SUMMARY:☐ Backup prüfen" in text
        assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
        assert anon.get("/ical/falsch.ics").status_code == 404
        # ohne Aufgaben
        c.patch("/api/v1/me/calendar-feed", json={"with_tasks": False})
        assert "Backup prüfen" not in anon.get(path).text
        # neuer Link sperrt den alten
        r2 = c.post("/api/v1/me/calendar-feed", json={"with_tasks": True}).json()
        assert anon.get(path).status_code == 404 and anon.get("/" + r2["url"].split("/", 3)[3]).status_code == 200
        c.delete("/api/v1/me/calendar-feed")
        assert anon.get("/" + r2["url"].split("/", 3)[3]).status_code == 404
    assert c.get("/api/v1/me/calendar-feed").json()["last_used_at"] is None


def test_fold_and_escape():
    long = "SUMMARY:" + "Ä" * 60
    folded = ical.fold(long)
    assert all(len(x.encode()) <= 75 for x in folded.split("\r\n")) and folded.replace("\r\n ", "") == long
    assert ical.esc("a;b,c\\d\ne") == "a\\;b\\,c\\\\d\\ne"
    row = {"start_utc": "2026-01-31T08:00:00+00:00", "end_utc": "2026-01-31T09:00:00+00:00", "recur": "m:1@31",
           "recur_until": "2026-05-31", "all_day": 0}
    days = [s.date() for s, _ in calendar_core.occurrences(row, date(2026, 1, 1), date(2026, 12, 31))]
    assert days == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)]
    assert ical.rrule("w:2", None, False, None) == "RRULE:FREQ=WEEKLY;INTERVAL=2"
    assert ical.rrule("wd", "2026-12-31", True, None) == "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;UNTIL=20261231"
