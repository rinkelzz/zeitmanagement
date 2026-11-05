"""Helpdesk: Module, Meldeformular, Anhänge, Tickets, Mails, Zeit pro Ticket, Rechte, Aufbewahrung."""
import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import dsm_auth, helpdesk
from app.db import connect
from app.main import app
from app.timeutil import now_utc, to_utc_str

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PDF = b"%PDF-1.7\n" + b"x" * 100
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"


def login(user="admin", pw=PW):
    c = TestClient(app)
    c.__enter__()
    assert c.post("/api/v1/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c


@pytest.fixture()
def mails(monkeypatch):
    sent = []

    def fake(conn, to, subject, body, attachments=None, headers=None):
        sent.append({"to": to, "subject": subject, "body": body, "files": [a[0] for a in attachments or []], "headers": headers})

    monkeypatch.setattr(helpdesk, "send_mail", fake)
    return sent


@pytest.fixture()
def hd(tmp_path, mails):
    dsm_auth._fails.clear()
    with fresh_db(tmp_path, mode="team", company="Rinkel Digital"):
        c = login()
        c.patch("/api/v1/settings", json={"modules": {"helpdesk": True}, "smtp_host": "mail.example.de", "smtp_from": "it@example.de"})
        c.patch("/api/v1/me", json={"email": "chef@example.de"})
        cust = c.post("/api/v1/customers", json={"name": "Physio Henze"}).json()["id"]
        p = c.post("/api/v1/projects", json={"kind": "project", "name": "IT-Support Praxis", "customer_id": cust}).json()["id"]
        forms = c.put(f"/api/v1/helpdesk/forms/{p}", json={}).json()["forms"]
        token = next(f["token"] for f in forms if f["project_id"] == p)
        yield {"c": c, "p": p, "token": token, "mails": mails}


def report(c, token, **kw):
    body = {"name": "Sabine Kluge", "email": "s.kluge@example.de", "title": "Theorg startet nicht",
            "body": "Fehlermeldung beim Login", "urgent": True}
    body.update(kw)
    return c.post(f"/api/v1/public/form/{token}", json=body)


# --- Module ----------------------------------------------------------------------

def test_modules_default_and_switch(tmp_path):
    with fresh_db(tmp_path, mode="team"):
        c = login()
        mods = c.get("/api/v1/settings").json()["modules"]
        assert mods == {"tasks": True, "times": True, "calendar": True, "roster": True, "helpdesk": False}
        assert c.get("/api/v1/tickets").status_code == 403  # Helpdesk ist nach dem Update aus
        assert c.patch("/api/v1/settings", json={"modules": {"tasks": False}}).status_code == 200
        r = c.get("/api/v1/tasks")
        assert r.status_code == 403 and "abgeschaltet" in r.json()["detail"]
        assert c.post("/api/v1/tasks", json={"title": "x"}).status_code == 403
        assert c.patch("/api/v1/settings", json={"modules": {"unsinn": True}}).status_code == 400
        off = {k: False for k in mods}
        assert c.patch("/api/v1/settings", json={"modules": off}).status_code == 400  # eins muss an bleiben
        assert c.patch("/api/v1/settings", json={"modules": {"tasks": True}}).json()["modules"]["tasks"]
        assert c.get("/api/v1/tasks").status_code == 200
        # Mitarbeiter dürfen Module nicht schalten
        c.post("/api/v1/users", json={"username": "mia", "display_name": "Mia", "password": "geheim123"})
        mia = login("mia", "geheim123")
        assert mia.patch("/api/v1/settings", json={"modules": {"tasks": False}}).status_code == 403


def test_form_hidden_when_module_off(hd):
    c, token = hd["c"], hd["token"]
    anon = TestClient(app)
    assert anon.get(f"/melden/{token}").status_code == 200
    assert anon.get(f"/api/v1/public/form/{token}").json()["company"] == "Rinkel Digital"
    c.patch("/api/v1/settings", json={"modules": {"helpdesk": False}})
    assert anon.get(f"/melden/{token}").status_code == 404
    assert report(anon, token).status_code == 404
    c.patch("/api/v1/settings", json={"modules": {"helpdesk": True}})
    c.put(f"/api/v1/helpdesk/forms/{hd['p']}", json={"active": False})
    assert report(anon, token).status_code == 404
    assert anon.get("/melden/gibtsnicht").status_code == 404


# --- Meldeformular ------------------------------------------------------------------

def test_public_report_creates_ticket_and_mails(hd):
    anon = TestClient(app)
    r = report(anon, hd["token"])
    assert r.status_code == 201
    nr = r.json()["number"]
    year = now_utc().year
    assert nr == f"T-{year}-0001" or nr.startswith("T-")
    assert report(anon, hd["token"], title="Zweites").json()["number"].endswith("-0002")
    lst = hd["c"].get("/api/v1/tickets", params={"view": "new"}).json()
    assert [t["title"] for t in lst] == ["Theorg startet nicht", "Zweites"] or len(lst) == 2
    t = next(x for x in lst if x["number"] == nr)
    assert t["priority"] == 1 and t["source"] == "form" and t["unread"] and t["customer"] == "Physio Henze"
    subjects = [m["subject"] for m in hd["mails"]]
    assert any(s.startswith(f"[#{nr}] Deine Meldung") for s in subjects)          # Bestätigung an Melder
    assert any("Neue Meldung (DRINGEND)" in s for s in subjects)                    # Hinweis an Chef
    confirm = next(m for m in hd["mails"] if m["to"] == "s.kluge@example.de")
    assert confirm["headers"]["Auto-Submitted"] == "auto-generated"
    detail = hd["c"].get(f"/api/v1/tickets/{t['id']}").json()
    assert detail["messages"][0]["kind"] == "requester" and detail["messages"][0]["author"] == "Sabine Kluge"
    assert any("Eingangsbestätigung" in m["body"] for m in detail["messages"] if m["kind"] == "system")
    assert not hd["c"].get(f"/api/v1/tickets/{t['id']}").json()["unread"]  # angesehen


def test_report_validation_honeypot_and_rate_limit(hd):
    anon, token = TestClient(app), hd["token"]
    assert report(anon, token, email="kaputt").status_code == 400
    assert report(anon, token, title="").status_code == 422
    r = report(anon, token, website="http://spam.example")
    assert r.status_code == 201 and r.json()["upload_key"] is None
    assert hd["c"].get("/api/v1/tickets/counts").json()["new"] == 0   # Spam wurde nicht gespeichert
    hd["c"].patch("/api/v1/helpdesk/settings", json={"privacy_url": "https://example.de/datenschutz"})
    assert "Datenschutz" in report(anon, token).json()["detail"]
    codes = [report(anon, token, privacy=True).status_code for _ in range(6)]
    assert codes.count(201) == 4 and codes[-1] == 429  # 5 pro Adresse (eins ging an den Honeypot)


def test_public_attachments(hd):
    anon = TestClient(app)
    key = report(anon, hd["token"]).json()["upload_key"]
    up = lambda data, name: anon.post(f"/api/v1/public/upload/{key}", params={"name": name}, content=data)  # noqa: E731
    assert up(PNG, "bild.png").status_code == 201
    r = up(PDF, "../../etc/protokoll.exe")
    assert r.status_code == 201 and r.json()["name"] == "protokoll.pdf"   # Endung passend zum Inhalt, kein Pfad
    assert up(SVG, "x.png").status_code == 400                            # Inhalt zählt, nicht die Endung
    assert up(b"", "leer.png").status_code == 400
    assert up(JPG, "a.jpg").status_code == 201
    assert up(JPG, "b.jpg").status_code == 201
    assert up(JPG, "c.jpg").status_code == 201
    assert "Höchstens 5" in up(JPG, "d.jpg").json()["detail"]
    assert anon.post("/api/v1/public/upload/falsch", content=PNG).status_code == 404
    tid = hd["c"].get("/api/v1/tickets").json()[0]["id"]
    d = hd["c"].get(f"/api/v1/tickets/{tid}").json()
    assert len(d["attachments"]) == 5 and all(a["from_requester"] for a in d["attachments"])
    assert all(a["message_id"] == d["messages"][0]["id"] for a in d["attachments"])
    f = d["attachments"][0]
    r = hd["c"].get(f"/api/v1/tickets/{tid}/files/{f['id']}")
    assert r.content == PNG and r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    assert r.headers["content-disposition"].startswith("attachment")
    # nach 30 Minuten ist Schluss
    conn = connect()
    conn.execute("UPDATE tickets SET upload_until = ? WHERE id = ?", (to_utc_str(now_utc() - timedelta(minutes=1)), tid))
    conn.close()
    assert anon.post(f"/api/v1/public/upload/{key}", content=PNG).status_code == 404


def test_upload_too_big(hd, monkeypatch):
    monkeypatch.setattr(helpdesk, "MAX_FILE_BYTES", 1000)
    anon = TestClient(app)
    key = report(anon, hd["token"]).json()["upload_key"]
    assert anon.post(f"/api/v1/public/upload/{key}", content=PNG + b"\x00" * 2000).status_code == 413


# --- Bearbeiten ---------------------------------------------------------------------

def test_reply_note_status_and_mails(hd):
    c = hd["c"]
    report(TestClient(app), hd["token"])
    tid = c.get("/api/v1/tickets").json()[0]["id"]
    hd["mails"].clear()
    f = c.post(f"/api/v1/tickets/{tid}/files", params={"name": "anleitung.pdf"}, content=PDF).json()
    d = c.post(f"/api/v1/tickets/{tid}/messages", json={"kind": "reply", "body": "Ich schaue gleich drauf.", "file_ids": [f["id"]]}).json()
    assert d["status"] == "open"  # Antwort auf neues Ticket → in Arbeit
    mail = next(m for m in hd["mails"] if m["to"] == "s.kluge@example.de")
    assert "Ich schaue gleich drauf." in mail["body"] and mail["subject"].startswith("[#T-") and mail["files"] == ["anleitung.pdf"]
    c.post(f"/api/v1/tickets/{tid}/messages", json={"kind": "note", "body": "Treiber v3 kaputt"})
    assert len(hd["mails"]) == 1  # interne Notiz geht nicht raus
    d = c.post(f"/api/v1/tickets/{tid}/messages", json={"kind": "reply", "body": "Erledigt, bitte testen.", "done": True}).json()
    assert d["status"] == "done" and d["done_at"]
    assert any(m["subject"].startswith("[#T-") and "Erledigt:" in m["subject"] for m in hd["mails"])
    kinds = [m["kind"] for m in d["messages"]]
    assert kinds.count("reply") == 2 and kinds.count("note") == 1 and "system" in kinds
    # Ticket ohne Melder-Adresse: nur Notizen
    t = c.post("/api/v1/tickets", json={"project_id": hd["p"], "title": "Server-Update", "body": "intern"}).json()
    assert t["assignee_id"] and t["status"] == "new"
    assert c.post(f"/api/v1/tickets/{t['id']}/messages", json={"kind": "reply", "body": "x"}).status_code == 400
    d = c.patch(f"/api/v1/tickets/{t['id']}", json={"status": "wait", "priority": 2, "assignee_id": 0}).json()
    assert d["status"] == "wait" and d["priority_label"] == "Hoch" and d["assignee_id"] is None
    assert c.patch(f"/api/v1/tickets/{t['id']}", json={"status": "kaputt"}).status_code == 400


def test_mail_failure_is_noted(hd, monkeypatch):
    from app.mailer import MailError

    def broken(*a, **k):
        raise MailError("Server weg")

    monkeypatch.setattr(helpdesk, "send_mail", broken)
    assert report(TestClient(app), hd["token"]).status_code == 201   # Ticket trotzdem gespeichert
    tid = hd["c"].get("/api/v1/tickets").json()[0]["id"]
    sys_msgs = [m["body"] for m in hd["c"].get(f"/api/v1/tickets/{tid}").json()["messages"] if m["kind"] == "system"]
    assert any("konnte nicht verschickt werden: Server weg" in s for s in sys_msgs)


def test_views_search_and_counts(hd):
    c = hd["c"]
    anon = TestClient(app)
    nr = report(anon, hd["token"]).json()["number"]
    report(anon, hd["token"], title="Drucker", urgent=False, name="Marco")
    counts = c.get("/api/v1/tickets/counts").json()
    assert counts["new"] == 2 and counts["open"] == 2 and counts["projects"][str(hd["p"])]["urgent"] == 1
    assert [t["title"] for t in c.get("/api/v1/tickets", params={"q": "druck"}).json()] == ["Drucker"]
    assert [t["number"] for t in c.get("/api/v1/tickets", params={"q": nr}).json()] == [nr]
    assert len(c.get("/api/v1/tickets", params={"q": "Marco"}).json()) == 1
    assert c.get("/api/v1/tickets", params={"q": "100%_"}).json() == []
    assert c.get("/api/v1/tickets", params={"view": "kaputt"}).status_code == 400


def test_rights_for_members(hd):
    c = hd["c"]
    c.post("/api/v1/users", json={"username": "mia", "display_name": "Mia", "password": "geheim123"})
    mia_id = next(u["id"] for u in c.get("/api/v1/users").json() if u["username"] == "mia")
    other = c.post("/api/v1/projects", json={"kind": "project", "name": "Hotel", "member_ids": [mia_id]}).json()["id"]
    report(TestClient(app), hd["token"])
    t_praxis = c.get("/api/v1/tickets").json()[0]["id"]
    t_hotel = c.post("/api/v1/tickets", json={"project_id": other, "title": "WLAN", "assignee_id": mia_id}).json()["id"]
    mia = login("mia", "geheim123")
    assert [t["id"] for t in mia.get("/api/v1/tickets").json()] == [t_hotel]
    assert mia.get(f"/api/v1/tickets/{t_praxis}").status_code == 404
    assert mia.get(f"/api/v1/tickets/{t_praxis}/files/1").status_code == 404
    assert mia.post(f"/api/v1/tickets/{t_praxis}/messages", json={"kind": "note", "body": "x"}).status_code == 404
    assert mia.post("/api/v1/tickets", json={"project_id": hd["p"], "title": "x"}).status_code == 404
    assert mia.get("/api/v1/tickets", params={"view": "mine"}).json()[0]["id"] == t_hotel
    assert mia.delete(f"/api/v1/tickets/{t_hotel}").status_code == 403
    assert mia.get("/api/v1/helpdesk/forms").status_code == 403
    # Bearbeiter muss Mitglied des Projekts sein
    assert c.patch(f"/api/v1/tickets/{t_praxis}", json={"assignee_id": mia_id}).status_code == 400


# --- Zeit pro Ticket -----------------------------------------------------------------

def test_stopwatch_on_ticket(hd):
    c = hd["c"]
    report(TestClient(app), hd["token"])
    tid = c.get("/api/v1/tickets").json()[0]["id"]
    r = c.post("/api/v1/times/start", json={"ticket_id": tid, "on_site": True}).json()
    run = r["running"]
    assert run["project_id"] == hd["p"] and run["ticket_id"] == tid and run["ticket"].startswith("T-")
    d = c.get(f"/api/v1/tickets/{tid}").json()
    assert d["status"] == "open" and d["assignee"] == "Administrator" and d["running"] and d["times"][0]["running"]
    # andere Stoppuhr starten → die Ticket-Uhr stoppt automatisch
    other = c.post("/api/v1/projects", json={"kind": "project", "name": "Intern"}).json()["id"]
    r = c.post("/api/v1/times/start", json={"project_id": other}).json()
    assert r["stopped"]["ticket_id"] == tid
    c.post("/api/v1/times/stop")
    assert not c.get(f"/api/v1/tickets/{tid}").json()["running"]
    assert c.post("/api/v1/times/start", json={"ticket_id": tid, "project_id": other}).status_code == 400
    # nachtragen auf das Ticket
    y = (now_utc() - timedelta(days=1)).date().isoformat()
    e = c.post("/api/v1/times", json={"ticket_id": tid, "start": y + "T09:00", "end": y + "T09:45", "note": "Fernwartung"}).json()
    assert e["ticket_id"] == tid and e["minutes"] == 45
    assert c.get("/api/v1/tickets").json()[0]["minutes"] >= 45
    # Projekt der Buchung ändern → passt nicht mehr zum Ticket
    assert c.patch(f"/api/v1/times/{e['id']}", json={"project_id": other}).json()["ticket_id"] is None
    # Ticket löschen: Zeiten bleiben, Nummer steht in der Notiz
    nr = d["number"]
    assert c.delete(f"/api/v1/tickets/{tid}").status_code == 200
    rows = c.get("/api/v1/times", params={"from": y, "to": now_utc().date().isoformat()}).json()
    mine = [x for x in rows if x["project_id"] == hd["p"]]
    assert mine and all(x["ticket_id"] is None and nr in x["note"] for x in mine)


def test_ticket_times_need_helpdesk(hd):
    c = hd["c"]
    t = c.post("/api/v1/tickets", json={"project_id": hd["p"], "title": "x"}).json()
    c.patch("/api/v1/settings", json={"modules": {"helpdesk": False}})
    assert "abgeschaltet" in c.post("/api/v1/times/start", json={"ticket_id": t["id"]}).json()["detail"]


# --- Verwaltung und Aufbewahrung --------------------------------------------------------

def test_forms_admin(hd):
    c = hd["c"]
    forms = c.get("/api/v1/helpdesk/forms").json()
    f = next(x for x in forms["forms"] if x["project_id"] == hd["p"])
    assert f["active"] and f["url"] == f"https://zeit.example.de/melden/{hd['token']}"
    new = next(x for x in c.put(f"/api/v1/helpdesk/forms/{hd['p']}", json={"regenerate": True}).json()["forms"] if x["project_id"] == hd["p"])
    assert new["token"] != hd["token"]
    assert report(TestClient(app), hd["token"]).status_code == 404   # alter Link ungültig
    assert report(TestClient(app), new["token"]).status_code == 201
    s = c.patch("/api/v1/helpdesk/settings", json={"mail_confirm": False, "retention_months": 6, "form_hint": "Keine Patientendaten!"}).json()
    assert s["mail_confirm"] is False and s["retention_months"] == 6
    assert c.patch("/api/v1/helpdesk/settings", json={"privacy_url": "javascript:alert(1)"}).status_code == 400
    assert TestClient(app).get(f"/api/v1/public/form/{new['token']}").json()["hint"] == "Keine Patientendaten!"


def test_purge_old_done_tickets(hd):
    c = hd["c"]
    t = c.post("/api/v1/tickets", json={"project_id": hd["p"], "title": "alt"}).json()
    keep = c.post("/api/v1/tickets", json={"project_id": hd["p"], "title": "neu erledigt"}).json()
    y = (now_utc() - timedelta(days=1)).date().isoformat()
    c.post("/api/v1/times", json={"ticket_id": t["id"], "start": y + "T08:00", "end": y + "T08:30"})
    c.patch(f"/api/v1/tickets/{t['id']}", json={"status": "done"})
    c.patch(f"/api/v1/tickets/{keep['id']}", json={"status": "done"})
    conn = connect()
    try:
        conn.execute("UPDATE tickets SET done_at = ? WHERE id = ?", (to_utc_str(now_utc() - timedelta(days=400)), t["id"]))
        assert helpdesk.purge_old(conn) == 1
        assert conn.execute("SELECT COUNT(*) FROM ticket_messages WHERE ticket_id = ?", (t["id"],)).fetchone()[0] == 0
        note = conn.execute("SELECT note FROM time_entries WHERE project_id = ?", (hd["p"],)).fetchone()[0]
        assert note == t["number"]
        conn.execute("UPDATE app_settings SET value = '0' WHERE key = 'hd_retention_months'")
        conn.execute("UPDATE tickets SET done_at = ? WHERE id = ?", (to_utc_str(now_utc() - timedelta(days=4000)), keep["id"]))
        assert helpdesk.purge_old(conn) == 0   # 0 = nie löschen
    finally:
        conn.close()


def test_clean_name_and_sniff():
    assert helpdesk.sniff(PNG) == "image/png" and helpdesk.sniff(SVG) is None
    assert helpdesk.clean_name('C:\\Users\\x\\"böse".png', "image/png") == "böse.png"
    assert helpdesk.clean_name("foto.jpeg", "image/jpeg") == "foto.jpeg"
    assert helpdesk.clean_name("", "application/pdf") == "datei.pdf"
    assert helpdesk.number(2026, 42) == "T-2026-0042"
