"""Helpdesk 0.8: Meldungen per E-Mail-Postfach – Zerlegen, Zuordnen, Antworten, Posteingang, IMAP-Abruf."""
import os
from datetime import timedelta
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app import helpdesk, mailin
from app.db import connect
from app.main import app
from app.timeutil import now_utc, to_utc_str

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def mail(frm="Marco Henze <m.henze@physio-henze.de>", subject="Drucker druckt nicht", body="Seit heute kommen nur leere Seiten.",
         html=None, files=(), mid=None, headers=None, reply_to_id=None) -> bytes:
    m = EmailMessage()
    m["From"] = frm
    m["To"] = "support@rinkel.online"
    m["Subject"] = subject
    m["Date"] = "Thu, 05 Nov 2026 09:00:00 +0100"
    m["Message-ID"] = mid or f"<{abs(hash((frm, subject, body)))}@example.de>"
    if reply_to_id:
        m["In-Reply-To"] = reply_to_id
        m["References"] = reply_to_id
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    for name, data, mt in files:
        main, sub = mt.split("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return m.as_bytes()


@pytest.fixture()
def sent(monkeypatch):
    out = []

    def fake(conn, to, subject, body, attachments=None, headers=None):
        out.append({"to": to, "subject": subject, "body": body})
        return f"<out{len(out)}@rinkel.online>"

    monkeypatch.setattr(helpdesk, "send_mail", fake)
    return out


@pytest.fixture()
def env(tmp_path, sent):
    with fresh_db(tmp_path, mode="team", company="Rinkel Digital"):
        c = TestClient(app)
        c.__enter__()
        c.post("/api/v1/auth/login", json={"username": "admin", "password": PW})
        c.patch("/api/v1/settings", json={"modules": {"helpdesk": True}, "smtp_host": "mail.example.de",
                                          "smtp_from": "support@rinkel.online"})
        c.patch("/api/v1/me", json={"email": "tim@rinkel.online"})
        cust = c.post("/api/v1/customers", json={"name": "Physio Henze"}).json()["id"]
        p = c.post("/api/v1/projects", json={"kind": "project", "name": "IT-Support Praxis", "customer_id": cust}).json()["id"]
        assert c.put(f"/api/v1/helpdesk/senders/{p}", json={"patterns": ["physio-henze.de", " @Praxis-Henze.de "]}).json()["senders"] == \
            ["physio-henze.de", "praxis-henze.de"]
        conn = connect()
        yield {"c": c, "p": p, "conn": conn, "sent": sent}
        conn.close()


# --- Zerlegen ------------------------------------------------------------------------

def test_parse_html_subject_quote():
    raw = mail(subject="AW: Re: [#T-2026-0007] Drucker", body="",
               html="<html><head><style>p{}</style></head><body><p>Hallo&nbsp;Tim,</p><script>alert(1)</script>"
                    "<ul><li>eins</li></ul></body></html>")
    p = mailin.parse(raw, [])
    assert "alert" not in p.body and "p{}" not in p.body and "Hallo Tim," in p.body and "- eins" in p.body
    assert mailin.clean_subject(p.subject) == "Drucker"
    text = "Geht wieder, danke!\n\nAm 05.11.2026 um 10:00 schrieb Support <support@rinkel.online>:\n> alter Text\n> mehr"
    assert mailin.strip_quote(text) == "Geht wieder, danke!"
    assert mailin.strip_quote("> nur Zitat") == "> nur Zitat"   # nichts übrig → Original behalten
    assert mailin.clean_subject("") == "(ohne Betreff)"


@pytest.mark.parametrize("headers,frm,reason", [
    ({"Auto-Submitted": "auto-replied"}, None, "Auto-Submitted"),
    ({"Precedence": "bulk"}, None, "Massenmail"),
    ({"List-Id": "<news.example.de>"}, None, "Newsletter"),
    ({"X-Autoreply": "yes"}, None, "Abwesenheit"),
    ({}, "MAILER-DAEMON@mx.example.de", "automatischer Absender"),
    ({}, "no-reply@shop.example.de", "automatischer Absender"),
    ({}, "support@rinkel.online", "eigene"),
])
def test_auto_mails_are_ignored(env, headers, frm, reason):
    r = mailin.process(env["conn"], mail(frm=frm or "Marco <m.henze@physio-henze.de>", headers=headers))
    assert r["action"] == "ignored" and reason in r["reason"]
    assert env["c"].get("/api/v1/tickets/counts").json()["new"] == 0


# --- Zuordnen --------------------------------------------------------------------------

def test_new_ticket_by_domain_with_attachments(env):
    raw = mail(files=[("fehler.png", PNG, "image/png"), ("bericht.docx", b"PK\x03\x04xx", "application/octet-stream"),
                      ("tarnung.png", b"<svg/>", "image/png")])
    r = mailin.process(env["conn"], raw)
    assert r["action"] == "ticket"
    t = env["c"].get(f"/api/v1/tickets/{r['ticket_id']}").json()
    assert t["source"] == "mail" and t["project_id"] == env["p"] and t["requester_email"] == "m.henze@physio-henze.de"
    assert t["title"] == "Drucker druckt nicht" and t["requester_name"] == "Marco Henze"
    assert [a["name"] for a in t["attachments"]] == ["fehler.png"]
    assert t["attachments"][0]["message_id"] == t["messages"][0]["id"]
    note = [m["body"] for m in t["messages"] if m["kind"] == "system" and "Nicht übernommen" in m["body"]][0]
    assert "bericht.docx" in note and "tarnung.png" in note
    assert any(s["subject"].startswith(f"[#{t['number']}] Deine Meldung") for s in env["sent"])
    # dieselbe Mail noch einmal → nichts Neues
    assert mailin.process(env["conn"], raw)["action"] == "seen"


def test_unknown_sender_goes_to_inbox_and_assign(env):
    c = env["c"]
    r = mailin.process(env["conn"], mail(frm="Hotel <info@hotel-rosenplatz.de>", subject="WLAN weg",
                                         files=[("bild.png", PNG, "image/png")]))
    assert r["action"] == "inbox"
    box = c.get("/api/v1/helpdesk/inbox").json()
    assert len(box) == 1 and "keinem Projekt" in box[0]["reason"] and box[0]["files"][0]["name"] == "bild.png"
    assert c.get("/api/v1/tickets/counts").json()["inbox"] == 1
    f = c.get(f"/api/v1/helpdesk/inbox/{box[0]['id']}/files/{box[0]['files'][0]['id']}")
    assert f.content == PNG and f.headers["content-disposition"].startswith("attachment")
    hotel = c.post("/api/v1/projects", json={"kind": "project", "name": "IT-Support Hotel"}).json()["id"]
    a = c.post(f"/api/v1/helpdesk/inbox/{box[0]['id']}/assign", json={"project_id": hotel, "remember": True}).json()
    assert a["remembered"] == "hotel-rosenplatz.de" and a["ticket"]["project_id"] == hotel and a["ticket"]["files"] == 1
    assert c.get("/api/v1/helpdesk/inbox").json() == []
    assert c.post(f"/api/v1/helpdesk/inbox/{box[0]['id']}/assign", json={"project_id": hotel}).status_code == 409
    # beim nächsten Mal automatisch
    assert mailin.process(env["conn"], mail(frm="info@hotel-rosenplatz.de", subject="Noch was"))["action"] == "ticket"


def test_freemail_and_known_sender(env):
    c = env["c"]
    r = c.put(f"/api/v1/helpdesk/senders/{env['p']}", json={"patterns": ["gmx.de"]})
    assert r.status_code == 400 and "Freemail" in r.json()["detail"]
    assert c.put(f"/api/v1/helpdesk/senders/{env['p']}", json={"patterns": ["kaputt"]}).status_code == 400
    other = c.post("/api/v1/projects", json={"kind": "project", "name": "Hotel"}).json()["id"]
    assert c.put(f"/api/v1/helpdesk/senders/{other}", json={"patterns": ["physio-henze.de"]}).status_code == 409
    # Freemail-Absender im Posteingang → beim Merken wird die Adresse gespeichert, nicht gmx.de
    iid = mailin.process(env["conn"], mail(frm="Chefin <henze.privat@gmx.de>"))["inbox_id"]
    a = c.post(f"/api/v1/helpdesk/inbox/{iid}/assign", json={"project_id": env["p"], "remember": True}).json()
    assert a["remembered"] == "henze.privat@gmx.de"
    # ein Melder, der schon ein Ticket hat, wird wiedererkannt (auch ohne Domain-Eintrag)
    c.put(f"/api/v1/helpdesk/senders/{env['p']}", json={"patterns": []})
    assert mailin.process(env["conn"], mail(frm="henze.privat@gmx.de", subject="Zweites Problem"))["action"] == "ticket"


def test_discard_and_rights(env):
    c = env["c"]
    iid = mailin.process(env["conn"], mail(frm="spam@irgendwo.example", subject="Gewinn!"))["inbox_id"]
    assert c.post(f"/api/v1/helpdesk/inbox/{iid}/discard").json()["ok"]
    assert c.get("/api/v1/helpdesk/inbox").json() == []
    c.post("/api/v1/users", json={"username": "mia", "display_name": "Mia", "password": "geheim123"})
    mia = TestClient(app)
    mia.__enter__()
    mia.post("/api/v1/auth/login", json={"username": "mia", "password": "geheim123"})
    assert mia.get("/api/v1/helpdesk/inbox").status_code == 403
    assert mia.get("/api/v1/tickets/counts").json()["inbox"] == 0
    assert mia.put(f"/api/v1/helpdesk/senders/{env['p']}", json={"patterns": []}).status_code == 403


def test_rate_limit_per_sender(env, monkeypatch):
    monkeypatch.setattr(mailin, "MAX_NEW_PER_HOUR", 2)
    acts = [mailin.process(env["conn"], mail(subject=f"Problem {i}"))["action"] for i in range(3)]
    assert acts == ["ticket", "ticket", "inbox"]
    assert "Mehr als 2" in env["c"].get("/api/v1/helpdesk/inbox").json()[0]["reason"]


# --- Antworten ---------------------------------------------------------------------------

def test_reply_by_subject_tag_and_in_reply_to(env):
    c, conn = env["c"], env["conn"]
    tid = mailin.process(conn, mail())["ticket_id"]
    t = c.get(f"/api/v1/tickets/{tid}").json()
    c.post(f"/api/v1/tickets/{tid}/messages", json={"kind": "reply", "body": "Bitte Drucker neu starten.", "done": True})
    last = conn.execute("SELECT last_mail_id FROM tickets WHERE id = ?", (tid,)).fetchone()[0]
    assert last and last.startswith("<out")
    env["sent"].clear()
    # Antwort mit Ticketnummer im Betreff, aus derselben Domain → wieder „In Arbeit“
    r = mailin.process(conn, mail(frm="Sabine <s.kluge@physio-henze.de>", subject=f"AW: [#{t['number']}] Re: Drucker",
                                  body="Geht immer noch nicht.\n\nAm 05.11.2026 schrieb Support:\n> Bitte Drucker neu starten."))
    assert r == {"action": "reply", "ticket_id": tid}
    d = c.get(f"/api/v1/tickets/{tid}").json()
    assert d["status"] == "open" and d["unread"] is False   # beim Ansehen gelesen
    assert any(m["kind"] == "requester" and m["body"] == "Geht immer noch nicht." for m in d["messages"])
    assert any("wieder „In Arbeit“" in m["body"] for m in d["messages"] if m["kind"] == "system")
    assert any("Antwort des Melders" in s["subject"] for s in env["sent"])   # Hinweis an den Chef
    # Antwort nur über In-Reply-To (Betreff ohne Nummer)
    r = mailin.process(conn, mail(subject="Re: Drucker", body="Jetzt geht's!", reply_to_id=last))
    assert r["action"] == "reply" and r["ticket_id"] == tid


def test_reply_from_stranger_becomes_new_mail(env):
    c, conn = env["c"], env["conn"]
    tid = mailin.process(conn, mail())["ticket_id"]
    nr = c.get(f"/api/v1/tickets/{tid}").json()["number"]
    r = mailin.process(conn, mail(frm="boese@fremd.example", subject=f"Re: [#{nr}] Drucker", body="Ich schreibe in fremde Tickets"))
    assert r["action"] == "inbox"
    assert "nicht vom Melder" in c.get("/api/v1/helpdesk/inbox").json()[0]["reason"]
    assert not any("fremde" in m["body"] for m in c.get(f"/api/v1/tickets/{tid}").json()["messages"])


# --- IMAP-Abruf -------------------------------------------------------------------------

class FakeImap:
    """Nachgebautes Postfach: UIDs → Rohmail. Verschobene Mails wandern in moved."""

    def __init__(self, mails, caps=(b"IMAP4REV1", b"MOVE"), fail_select=False):
        self.mails = dict(mails)
        self.moved = {}
        self.capabilities = caps
        self.created = []
        self.fail_select = fail_select
        self.logged_out = False

    def select(self, folder, readonly=False):
        return ("NO", [b"no such folder"]) if self.fail_select else ("OK", [str(len(self.mails)).encode()])

    def list(self, *a):
        return "OK", [b'(\\HasNoChildren) "." "INBOX"']

    def create(self, name):
        self.created.append(name)
        return "OK", [b""]

    def subscribe(self, name):
        return "OK", [b""]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return "OK", [b" ".join(sorted(self.mails))]
        if cmd == "FETCH":
            uid, what = args
            raw = self.mails[uid]
            if "HEADER" in what:
                head = raw.split(b"\n\n", 1)[0] + b"\n\n"
                size = getattr(self, "fake_size", {}).get(uid, len(raw))
                return "OK", [(b"1 (UID " + uid + b" RFC822.SIZE " + str(size).encode() + b" BODY[HEADER] {1}", head), b")"]
            return "OK", [(b"1 (UID " + uid + b" BODY[] {1}", raw), b")"]
        if cmd == "MOVE":
            uid, folder = args
            self.moved[uid] = (folder, self.mails.pop(uid))
            return "OK", [b""]
        raise AssertionError(cmd)

    def logout(self):
        self.logged_out = True


def test_fetch_moves_and_records(env):
    c, conn = env["c"], env["conn"]
    c.patch("/api/v1/helpdesk/imap", json={"host": "imap.example.de", "user": "support@rinkel.online", "password": "geheim",
                                           "enabled": True, "done_folder": "Zeitmanagement/Erledigt"})
    big = mail(frm="info@fremd.example", subject="Riesig")
    box = FakeImap({b"1": mail(), b"2": mail(frm="info@fremd.example", subject="Unbekannt"),
                    b"3": mail(headers={"Auto-Submitted": "auto-replied"}, subject="Abwesend"), b"4": big})
    box.fake_size = {b"4": mailin.MAX_MAIL_BYTES + 1}
    r = mailin.fetch(conn, factory=lambda cfg: box, force=True)
    assert r["ticket"] == 1 and r["inbox"] == 2 and r["ignored"] == 1 and not r.get("error")
    assert set(box.moved) == {b"1", b"2", b"3"} and b"4" in box.mails     # zu große bleibt im Postfach
    assert box.moved[b"1"][0] == '"Zeitmanagement.Erledigt"'               # Trennzeichen des Servers
    assert box.logged_out
    reasons = [x["reason"] for x in c.get("/api/v1/helpdesk/inbox").json()]
    assert any("zu groß" in x for x in reasons)
    # zweiter Lauf: die große Mail wird nicht noch einmal gemeldet
    r = mailin.fetch(conn, factory=lambda cfg: box, force=True)
    assert r["inbox"] == 0 and r["seen"] == 1
    imap = c.get("/api/v1/helpdesk/imap").json()
    assert imap["password_set"] and "password" not in imap and imap["last_error"] == "" and imap["last_run"]
    # nicht fällig → kein Abruf
    assert mailin.fetch(conn, factory=lambda cfg: box) is None


def test_fetch_errors_and_settings(env):
    c, conn = env["c"], env["conn"]
    assert c.patch("/api/v1/helpdesk/imap", json={"enabled": True}).status_code == 400   # ohne Zugangsdaten nicht
    assert c.patch("/api/v1/helpdesk/imap", json={"security": "none"}).status_code == 400
    assert c.patch("/api/v1/helpdesk/imap", json={"folder": 'IN"BOX'}).status_code == 400
    c.patch("/api/v1/helpdesk/imap", json={"host": "imap.example.de", "user": "u", "password": "p", "enabled": True})
    assert c.post("/api/v1/helpdesk/imap/run").status_code == 502   # echter Server nicht erreichbar → Fehler, kein Absturz
    assert c.get("/api/v1/helpdesk/imap").json()["last_error"]
    r = mailin.fetch(conn, factory=lambda cfg: FakeImap({}, fail_select=True), force=True)
    assert "gibt es im Postfach nicht" in r["error"]
    # Helpdesk aus → kein Abruf
    c.patch("/api/v1/settings", json={"modules": {"helpdesk": False}})
    assert mailin.fetch(conn, factory=lambda cfg: FakeImap({b"1": mail()}), force=True) is None


def test_copy_fallback_without_move():
    class NoMove(FakeImap):
        def __init__(self, *a, **k):
            super().__init__(*a, caps=(b"IMAP4REV1",), **k)
            self.calls = []

        def uid(self, cmd, *args):
            self.calls.append(cmd)
            if cmd in ("COPY", "STORE"):
                return "OK", [b""]
            return super().uid(cmd, *args)

        def expunge(self):
            self.calls.append("EXPUNGE")

    box = NoMove({b"1": mail()})
    assert mailin._move(box, b"1", "Erledigt")
    assert box.calls == ["COPY", "STORE", "EXPUNGE"]


def test_imap_password_not_in_backup(env):
    from app import backup
    c = env["c"]
    c.patch("/api/v1/helpdesk/imap", json={"host": "imap.example.de", "user": "u", "password": "streng-geheim"})
    b = backup.create("test")
    import gzip
    data = gzip.open(backup.local_dir() / b["name"]).read()
    assert b"streng-geheim" not in data


def test_inbox_old_entries_untouched_by_purge(env):
    # Aufräumen löscht nur erledigte Tickets, keine offenen Posteingangs-Mails
    iid = mailin.process(env["conn"], mail(frm="x@fremd.example"))["inbox_id"]
    env["conn"].execute("UPDATE mail_inbox SET created_at = ? WHERE id = ?", (to_utc_str(now_utc() - timedelta(days=900)), iid))
    helpdesk.purge_old(env["conn"])
    assert len(env["c"].get("/api/v1/helpdesk/inbox").json()) == 1
