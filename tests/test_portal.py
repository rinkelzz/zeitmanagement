"""Helpdesk 0.9: Melder-Portal – Anmelde-Link, eigene Tickets, Statuslink, Antworten, Bestätigen, Rechte."""
import os
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import dsm_auth, helpdesk, portal
from app.db import connect
from app.main import app
from app.timeutil import now_utc, to_utc_str

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50


@pytest.fixture()
def sent(monkeypatch):
    out = []

    def fake(conn, to, subject, body, attachments=None, headers=None):
        out.append({"to": to, "subject": subject, "body": body})
        return f"<m{len(out)}@x>"

    monkeypatch.setattr(helpdesk, "send_mail", fake)
    monkeypatch.setattr(portal, "send_mail", fake)
    return out


@pytest.fixture()
def env(tmp_path, sent):
    dsm_auth._fails.clear()
    with fresh_db(tmp_path, mode="team", company="Rinkel Digital"):
        c = TestClient(app)
        c.__enter__()
        c.post("/api/v1/auth/login", json={"username": "admin", "password": PW})
        c.patch("/api/v1/settings", json={"modules": {"helpdesk": True}, "smtp_host": "mail.example.de", "smtp_from": "it@rinkel.online"})
        p = c.post("/api/v1/projects", json={"kind": "project", "name": "IT-Support Praxis"}).json()["id"]
        token = next(f["token"] for f in c.put(f"/api/v1/helpdesk/forms/{p}", json={}).json()["forms"] if f["project_id"] == p)
        c.put(f"/api/v1/helpdesk/senders/{p}", json={"patterns": ["physio-henze.de"]})
        yield {"c": c, "p": p, "token": token, "sent": sent}


def report(token, email="s.kluge@physio-henze.de", title="Theorg startet nicht"):
    r = TestClient(app).post(f"/api/v1/public/form/{token}", json={"name": "Sabine", "email": email, "title": title, "body": "Fehler"})
    assert r.status_code == 201
    return r.json()["number"]


def login_link(sent):
    m = re.search(r"/portal#login=([\w-]+)", sent[-1]["body"])
    return m.group(1)


def portal_client(env, email="s.kluge@physio-henze.de"):
    p = TestClient(app)
    env["sent"].clear()
    assert p.post("/api/v1/portal/login", json={"email": email}).status_code == 200
    assert env["sent"], "keine Anmelde-Mail"
    assert p.post("/api/v1/portal/verify", json={"token": login_link(env["sent"])}).status_code == 200
    return p


def test_portal_off_by_default(env):
    p = TestClient(app)
    assert p.get("/portal").status_code == 404
    assert p.post("/api/v1/portal/login", json={"email": "x@physio-henze.de"}).status_code == 404
    report(env["token"])
    assert "portal/t/" not in env["sent"][0]["body"]   # ohne Portal kein Link in der Mail
    s = env["c"].patch("/api/v1/helpdesk/settings", json={"portal": True}).json()
    assert s["portal"] and s["portal_url"] == "https://zeit.example.de/portal"
    assert p.get("/portal").status_code == 200


def test_login_flow_and_own_tickets(env):
    env["c"].patch("/api/v1/helpdesk/settings", json={"portal": True})
    report(env["token"])
    report(env["token"], email="m.henze@physio-henze.de", title="Fremdes Ticket")
    anon = TestClient(app)
    # unbekannte Adresse: gleiche Antwort, aber keine Mail
    env["sent"].clear()
    r = anon.post("/api/v1/portal/login", json={"email": "wer@unbekannt.example"})
    assert r.status_code == 200 and "Wenn diese Adresse" in r.json()["message"] and env["sent"] == []
    p = portal_client(env)
    me = p.get("/api/v1/portal/me").json()
    assert me["email"] == "s.kluge@physio-henze.de" and [x["name"] for x in me["projects"]] == ["IT-Support Praxis"]
    mine = p.get("/api/v1/portal/tickets").json()
    assert [t["title"] for t in mine] == ["Theorg startet nicht"]
    other = env["c"].get("/api/v1/tickets", params={"q": "Fremdes"}).json()[0]["id"]
    assert p.get(f"/api/v1/portal/tickets/{other}").status_code == 404
    # Link ist nur einmal gültig
    assert anon.post("/api/v1/portal/verify", json={"token": login_link(env["sent"])}).status_code == 400
    # Abmelden
    p.post("/api/v1/portal/logout")
    assert p.get("/api/v1/portal/tickets").status_code == 401
    # Mitarbeiter-Sitzung zählt im Portal nicht
    assert env["c"].get("/api/v1/portal/tickets").status_code == 401


def test_login_expired_and_rate_limit(env):
    env["c"].patch("/api/v1/helpdesk/settings", json={"portal": True})
    report(env["token"])
    anon = TestClient(app)
    anon.post("/api/v1/portal/login", json={"email": "s.kluge@physio-henze.de"})
    tok = login_link(env["sent"])
    conn = connect()
    conn.execute("UPDATE portal_logins SET expires_at = ?", (to_utc_str(now_utc() - timedelta(minutes=1)),))
    conn.close()
    assert anon.post("/api/v1/portal/verify", json={"token": tok}).status_code == 400
    codes = [anon.post("/api/v1/portal/login", json={"email": "s.kluge@physio-henze.de"}).status_code for _ in range(3)]
    assert codes[-1] == 429   # 3 pro Adresse in 15 Minuten (eine war schon)


def test_conversation_hides_internal_and_reply_flow(env):
    c = env["c"]
    c.patch("/api/v1/helpdesk/settings", json={"portal": True})
    report(env["token"])
    tid = c.get("/api/v1/tickets").json()[0]["id"]
    c.post(f"/api/v1/tickets/{tid}/messages", json={"kind": "note", "body": "GEHEIM: Kunde zahlt schlecht"})
    f = c.post(f"/api/v1/tickets/{tid}/files", params={"name": "anleitung.pdf"}, content=b"%PDF-1.4 x").json()
    c.post(f"/api/v1/tickets/{tid}/messages", json={"kind": "reply", "body": "Bitte neu starten.", "file_ids": [f["id"]], "done": True})
    p = portal_client(env)
    d = p.get(f"/api/v1/portal/tickets/{tid}").json()
    text = str(d)
    assert "GEHEIM" not in text and "Status" not in [m["body"][:6] for m in d["messages"]]
    assert [m["from_me"] for m in d["messages"]] == [True, False] and d["status"] == "done"
    assert d["messages"][1]["files"][0]["name"] == "anleitung.pdf"
    assert p.get(f"/api/v1/portal/tickets/{tid}/files/{f['id']}").content.startswith(b"%PDF")
    # Datei an der internen Notiz wäre nicht abrufbar
    nf = c.post(f"/api/v1/tickets/{tid}/files", params={"name": "intern.png"}, content=PNG).json()
    c.post(f"/api/v1/tickets/{tid}/messages", json={"kind": "note", "body": "intern", "file_ids": [nf["id"]]})
    assert p.get(f"/api/v1/portal/tickets/{tid}/files/{nf['id']}").status_code == 404
    # antworten + Datei → Ticket wieder in Arbeit, Bearbeiter bekommt Hinweis
    c.patch("/api/v1/me", json={"email": "tim@rinkel.online"})
    env["sent"].clear()
    r = p.post(f"/api/v1/portal/tickets/{tid}/messages", json={"body": "Geht immer noch nicht"}).json()
    assert r["status"] == "open"
    up = p.post(f"/api/v1/portal/tickets/{tid}/messages/{r['message_id']}/files", params={"name": "foto.png"}, content=PNG)
    assert up.status_code == 201
    assert p.post(f"/api/v1/portal/tickets/{tid}/messages/{r['message_id']}/files", content=b"<svg/>").status_code == 400
    assert any("Antwort des Melders" in s["subject"] for s in env["sent"])
    staff = c.get(f"/api/v1/tickets/{tid}").json()
    assert any(m["kind"] == "requester" and m["body"] == "Geht immer noch nicht" for m in staff["messages"])
    assert any(a["name"] == "foto.png" for a in staff["attachments"])
    # an fremde Nachrichten (z. B. die Antwort des Supports) lässt sich nichts hängen
    reply_id = next(m["id"] for m in d["messages"] if not m["from_me"])
    assert p.post(f"/api/v1/portal/tickets/{tid}/messages/{reply_id}/files", content=PNG).status_code == 404
    # bestätigen / wieder öffnen
    assert p.post(f"/api/v1/portal/tickets/{tid}/resolve").json()["status"] == "done"
    assert p.post(f"/api/v1/portal/tickets/{tid}/reopen").json()["status"] == "open"
    sysm = [m["body"] for m in c.get(f"/api/v1/tickets/{tid}").json()["messages"] if m["kind"] == "system"]
    assert any("Problem gelöst" in s for s in sysm) and any("besteht noch" in s for s in sysm)


def test_status_link_only_one_ticket_and_renew(env):
    c = env["c"]
    c.patch("/api/v1/helpdesk/settings", json={"portal": True})
    report(env["token"])
    report(env["token"], title="Zweites")
    confirm = next(m for m in env["sent"] if "Deine Meldung" in m["subject"])
    url = re.search(r"https://zeit\.example\.de/portal/t/(\S+)", confirm["body"])
    assert url, confirm["body"]
    link = url.group(1)
    tid = int(link.split("-")[0])
    anon = TestClient(app)
    assert anon.get(f"/portal/t/{link}").status_code == 200
    h = {"X-Portal-Link": link}
    d = anon.get(f"/api/v1/portal/tickets/{tid}", headers=h).json()
    assert d["id"] == tid
    others = [t["id"] for t in c.get("/api/v1/tickets").json() if t["id"] != tid]
    assert anon.get(f"/api/v1/portal/tickets/{others[0]}", headers=h).status_code == 404
    assert anon.get("/api/v1/portal/tickets", headers=h).status_code == 403          # keine Liste
    assert anon.post("/api/v1/portal/tickets", headers=h, json={"title": "x", "body": "y"}).status_code == 403
    assert anon.post(f"/api/v1/portal/tickets/{tid}/messages", headers=h, json={"body": "Danke!"}).status_code == 201
    assert anon.get(f"/api/v1/portal/tickets/{tid}", headers={"X-Portal-Link": f"{tid}-" + "0" * 32}).status_code == 401
    # Mitarbeiter sieht den Link und kann ihn erneuern
    staff = c.get(f"/api/v1/tickets/{tid}").json()
    assert staff["portal_link"].endswith(link)
    new = c.post(f"/api/v1/tickets/{tid}/link").json()["portal_link"]
    assert not new.endswith(link)
    assert anon.get(f"/api/v1/portal/tickets/{tid}", headers=h).status_code == 401


def test_new_ticket_in_portal(env):
    c = env["c"]
    c.patch("/api/v1/helpdesk/settings", json={"portal": True})
    # Adresse nur über die Absender-Domain bekannt (noch kein Ticket)
    p = portal_client(env, "neu@physio-henze.de")
    r = p.post("/api/v1/portal/tickets", json={"title": "Kartenleser kaputt", "body": "blinkt rot", "urgent": True})
    assert r.status_code == 201 and r.json()["number"].startswith("T-")
    up = p.post(f"/api/v1/portal/tickets/{r.json()['id']}/messages/{r.json()['message_id']}/files", params={"name": "a.png"}, content=PNG)
    assert up.status_code == 201
    t = c.get(f"/api/v1/tickets/{r.json()['id']}").json()
    assert t["source"] == "portal" and t["priority"] == 1 and t["project_id"] == env["p"] and t["requester_email"] == "neu@physio-henze.de"
    assert t["attachments"][0]["name"] == "a.png"
    # zweites Projekt mit derselben Domain? geht nicht (Domain gehört genau einem Projekt) – aber per Ticket bekannt:
    other = c.post("/api/v1/projects", json={"kind": "project", "name": "Hotel"}).json()["id"]
    c.post("/api/v1/tickets", json={"project_id": other, "title": "x", "requester_email": "neu@physio-henze.de"})
    assert p.post("/api/v1/portal/tickets", json={"title": "y", "body": "z"}).status_code == 400   # Auswahl nötig
    assert p.post("/api/v1/portal/tickets", json={"title": "y", "body": "z", "project_id": other}).status_code == 201
    secret = c.post("/api/v1/projects", json={"kind": "project", "name": "Geheim"}).json()["id"]
    assert p.post("/api/v1/portal/tickets", json={"title": "y", "body": "z", "project_id": secret}).status_code == 400


def test_portal_secret_created_and_kept(env):
    conn = connect()
    try:
        assert len(conn.execute("SELECT value FROM app_settings WHERE key = 'portal_secret'").fetchone()[0]) == 64
    finally:
        conn.close()
