"""Einzelperson- und Team-Modus: Startwerte aus dem Assistenten, Anmeldung, Umschalten."""
import os

from fastapi.testclient import TestClient

from app import dsm_auth
from app.main import app
from app.routers import auth as auth_router

from .fresh import fresh_db

PW = os.environ["ZM_ADMIN_PASSWORD"]
LOCAL = ("127.0.0.1", 50000)


def sso(user, admin=False):
    return {"X-ZM-Proxy-Secret": dsm_auth.proxy_secret(), "X-ZM-Proxy-Base": "app.cgi",
            "X-DSM-User": user, "X-DSM-Admin": "1" if admin else "0"}


def login(c, user="admin", pw=PW):
    r = c.post("/api/v1/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()


def test_wizard_values_only_on_first_start(tmp_path):
    with fresh_db(tmp_path, mode="team", company="Rinkel Digital"):
        with TestClient(app) as c:
            login(c)
            s = c.get("/api/v1/settings").json()
            assert s["mode"] == "team" and s["company_name"] == "Rinkel Digital"
            assert c.get("/api/v1/branding").json()["company_name"] == "Rinkel Digital"
            c.patch("/api/v1/settings", json={"mode": "single"})
        # erneuter Start (z. B. Update) überschreibt nichts
        from app.db import init_db
        init_db()
        with TestClient(app) as c:
            login(c)
            assert c.get("/api/v1/settings").json()["mode"] == "single"


def test_single_mode_sso_maps_every_dsm_admin_to_owner(tmp_path):
    with fresh_db(tmp_path, mode="single"):
        with TestClient(app, client=LOCAL) as c:
            me = c.get("/api/v1/me", headers=sso("tim", admin=True)).json()
            assert me["username"] == "admin" and me["is_admin"] and me["mode"] == "single"
            # ein zweiter DSM-Admin landet im selben Konto – es entsteht kein neuer Benutzer
            assert c.get("/api/v1/me", headers=sso("chefin", admin=True)).json()["id"] == me["id"]
            r = c.get("/api/v1/me", headers=sso("praktikant"))
            assert r.status_code == 403 and "Einzelperson" in r.json()["detail"]
            login(c)
            assert [u["username"] for u in c.get("/api/v1/users").json()] == ["admin"]
            # Benutzer anlegen geht nur im Team-Modus
            r = c.post("/api/v1/users", json={"username": "x", "display_name": "X", "password": "geheim123"})
            assert r.status_code == 409 and "Team" in r.json()["detail"]


def test_single_mode_dsm_password_login(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_router, "dsm_password_check", lambda *a, **k: "ok")
    monkeypatch.setattr(auth_router, "is_dsm_admin", lambda u: u == "tim")
    with fresh_db(tmp_path, mode="single"):
        with TestClient(app, client=LOCAL) as c:
            assert login(c, "tim", "dsm-pass")["username"] == "admin"
            r = c.post("/api/v1/auth/login", json={"username": "gast", "password": "dsm-pass"})
            assert r.status_code == 403


def test_switch_modes(tmp_path):
    with fresh_db(tmp_path, mode="team"):
        with TestClient(app) as c:
            login(c)
            uid = c.post("/api/v1/users", json={"username": "mia", "display_name": "Mia", "password": "geheim123"}).json()["id"]
            assert c.patch("/api/v1/settings", json={"mode": "bunt"}).status_code == 400
            r = c.patch("/api/v1/settings", json={"mode": "single"})
            assert r.status_code == 400 and "Mia" in r.json()["detail"]
            c.patch(f"/api/v1/users/{uid}", json={"active": False})
            assert c.patch("/api/v1/settings", json={"mode": "single"}).json()["mode"] == "single"
            with TestClient(app) as m:  # Mia kommt im Einzelperson-Modus nicht rein, auch reaktiviert nicht
                assert m.post("/api/v1/auth/login", json={"username": "mia", "password": "geheim123"}).status_code == 401
            assert c.patch("/api/v1/settings", json={"mode": "team"}).json()["mode"] == "team"
            log = c.get("/api/v1/audit", params={"category": "einstellungen"}).json()["entries"]
            assert any("single" in e["text"] for e in log)
