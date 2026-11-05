"""Auswertung und Exporte (PDF, Excel, CSV)."""
import io
import os
from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.timeutil import today_local

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
T = today_local()
D = lambda n: (T - timedelta(days=n)).isoformat()  # noqa: E731


def client(user="admin", pw=PW):
    c = TestClient(app)
    c.__enter__()
    assert c.post("/api/v1/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c


def _seed(chef, anna_id):
    ph = chef.post("/api/v1/customers", json={"name": "Physio Henze"}).json()["id"]
    web = chef.post("/api/v1/projects", json={"kind": "project", "name": "Website", "customer_id": ph, "member_ids": [anna_id]}).json()["id"]
    it = chef.post("/api/v1/projects", json={"kind": "project", "name": "Interne IT"}).json()["id"]
    task = chef.post("/api/v1/tasks", json={"title": "Startseite", "project_id": web}).json()["id"]
    add = lambda c, p, day, s, e, **kw: c.post("/api/v1/times", json={"project_id": p, "start": f"{day}T{s}", "end": f"{day}T{e}", **kw})  # noqa: E731
    assert add(chef, web, D(2), "09:00", "10:30", task_id=task, note="=1+1").status_code == 201
    assert add(chef, it, D(2), "11:00", "11:45", on_site=True).status_code == 201
    assert add(chef, web, D(1), "08:00", "09:00", user_id=anna_id).status_code == 201
    return ph, web, it


def test_report_grouping_and_rights(tmp_path):
    with fresh_db(tmp_path, mode="team", company="Rinkel Digital"):
        chef = client()
        anna_id = chef.post("/api/v1/users", json={"username": "anna", "display_name": "Anna", "password": "geheim123"}).json()["id"]
        ph, web, it = _seed(chef, anna_id)
        chef.post("/api/v1/times/start", json={"project_id": it})  # laufende zählt nicht
        q = {"from": D(7), "to": T.isoformat()}
        r = chef.get("/api/v1/reports/times", params=q).json()
        assert r["total_minutes"] == 90 + 45 + 60 and r["total"] == "3:15" and r["count"] == 3 and r["show_users"]
        assert [g["label"] for g in r["groups"]] == ["Physio Henze", "Ohne Kunde"]
        assert r["groups"][0]["minutes"] == 150 and r["groups"][0]["children"][0]["label"] == "Website"
        assert set(r["groups"][0]["users"]) == {"Administrator", "Anna"}
        assert "user" in r["group_options"]
        r = chef.get("/api/v1/reports/times", params={**q, "group": "user"}).json()
        assert {g["label"]: g["minutes"] for g in r["groups"]} == {"Administrator": 135, "Anna": 60}
        r = chef.get("/api/v1/reports/times", params={**q, "group": "task", "project_id": web}).json()
        assert [(c["label"], c["minutes"]) for c in r["groups"][0]["children"]] == [("Startseite", 90), ("(ohne Aufgabe)", 60)]
        r = chef.get("/api/v1/reports/times", params={**q, "group": "day"}).json()
        assert [g["minutes"] for g in r["groups"]] == [135, 60]  # chronologisch
        assert chef.get("/api/v1/reports/times", params={**q, "customer_id": 0}).json()["total_minutes"] == 45
        assert chef.get("/api/v1/reports/times", params={**q, "customer_id": ph}).json()["filters"] == ["Kunde: Physio Henze"]
        assert chef.get("/api/v1/reports/times", params={**q, "group": "quatsch"}).status_code == 400
        assert chef.get("/api/v1/reports/times", params={"from": T.isoformat(), "to": D(3)}).status_code == 400
        # Mitarbeiter: nur eigene Zeiten, keine Gruppierung nach Mitarbeiter
        anna = client("anna", "geheim123")
        r = anna.get("/api/v1/reports/times", params={**q, "user_id": 1, "group": "user"}).json()
        assert r["total_minutes"] == 60 and r["group"] == "customer" and not r["show_users"] and "user" not in r["group_options"]


def test_exports(tmp_path):
    with fresh_db(tmp_path, mode="team", company="Rinkel Digital"):
        chef = client()
        anna_id = chef.post("/api/v1/users", json={"username": "anna", "display_name": "@Anna", "password": "geheim123"}).json()["id"]
        _seed(chef, anna_id)
        q = {"from": D(7), "to": T.isoformat()}
        csv_ = chef.get("/api/v1/reports/times/export", params={**q, "format": "csv"})
        assert csv_.status_code == 200 and 'filename="zeitnachweis_' in csv_.headers["content-disposition"]
        text = csv_.content.decode("utf-8-sig")
        assert text.splitlines()[0].startswith("Datum;Von;Bis;Dauer (h:mm);Stunden;Mitarbeiter;Kunde")
        assert "'=1+1" in text and "'@Anna" in text and ";1:30;1,50;" in text and "Summe;;;3:15;3,25" in text
        pdf = chef.get("/api/v1/reports/times/export", params={**q, "format": "pdf"}).content
        assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
        from openpyxl import load_workbook
        x = chef.get("/api/v1/reports/times/export", params={**q, "format": "xlsx"}).content
        wb = load_workbook(io.BytesIO(x))
        assert wb.sheetnames == ["Übersicht", "Buchungen"]
        ws = wb["Buchungen"]
        notes = [ws.cell(r, 11).value for r in range(2, ws.max_row)]
        assert "'=1+1" in notes and all(ws.cell(r, 11).data_type != "f" for r in range(2, ws.max_row + 1))
        assert ws.cell(ws.max_row, 4).value == "3:15"
        assert chef.get("/api/v1/reports/times/export", params={**q, "format": "doc"}).status_code == 400
        log = chef.get("/api/v1/audit", params={"category": "zeiten"}).json()["entries"]
        assert sum(1 for e in log if e["action"] == "report_export") == 3


def test_pdf_with_logo_and_many_pages(tmp_path):
    from app import pdf as pdfmod
    from .test_branding import data_url, make_png
    with fresh_db(tmp_path, mode="single"):
        c = client()
        c.put("/api/v1/branding/logo", json={"data_url": data_url(make_png(60, 20)[0])})
        p = c.post("/api/v1/projects", json={"kind": "project", "name": "Viel"}).json()["id"]
        for i in range(60):  # 60 Tage × 1 Buchung → mehrere Seiten
            day = (T - timedelta(days=i + 1)).isoformat()
            c.post("/api/v1/times", json={"project_id": p, "start": f"{day}T08:00", "end": f"{day}T09:00", "note": f"Tag {i}"})
        data = c.get("/api/v1/reports/times/export", params={"from": (T - timedelta(days=70)).isoformat(), "to": T.isoformat(), "format": "pdf"}).content
        assert data.count(b"/Type /Page ") >= 3 and b"/Subtype /Image" in data
        assert pdfmod.text_width("Zeit", 10) > 0
