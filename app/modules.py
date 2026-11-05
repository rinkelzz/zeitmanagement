"""Module an- und abschalten.

Jeder Bereich (Aufgaben, Projektzeiten, Kalender, Dienstplan, Helpdesk) lässt sich in der Verwaltung einzeln
abschalten. Ein abgeschaltetes Modul verschwindet aus Menü und Schnittstelle (die API antwortet mit 403),
seine Daten bleiben aber erhalten – beim Wiedereinschalten ist alles wieder da.
"""
import sqlite3
from typing import Dict

from fastapi import Depends, HTTPException

from .db import get_db, get_setting, set_setting

# Schlüssel → Anzeigename (Reihenfolge wie im Menü)
MODULES: Dict[str, str] = {
    "tasks": "Aufgaben",
    "times": "Projektzeiten",
    "calendar": "Kalender",
    "roster": "Dienstplan",
    "helpdesk": "Helpdesk",
}
# Neue Module starten abgeschaltet, damit ein Update nichts Neues (z. B. ein öffentliches Formular) freischaltet
DEFAULTS = {"tasks": "1", "times": "1", "calendar": "1", "roster": "1", "helpdesk": "0"}


def setting_key(key: str) -> str:
    return "module_" + key


def enabled(conn: sqlite3.Connection, key: str) -> bool:
    return get_setting(conn, setting_key(key)) == "1"


def all_states(conn: sqlite3.Connection) -> Dict[str, bool]:
    return {k: enabled(conn, k) for k in MODULES}


def set_states(conn: sqlite3.Connection, states: Dict[str, bool]) -> Dict[str, bool]:
    unknown = [k for k in states if k not in MODULES]
    if unknown:
        raise HTTPException(400, "Unbekanntes Modul: " + ", ".join(unknown))
    merged = {**all_states(conn), **states}
    if not any(merged.values()):
        raise HTTPException(400, "Mindestens ein Modul muss eingeschaltet bleiben")
    for k, v in states.items():
        set_setting(conn, setting_key(k), "1" if v else "0")
    return all_states(conn)


def require(key: str):
    """FastAPI-Dependency für einen ganzen Router: 403, wenn das Modul abgeschaltet ist."""
    def _check(conn: sqlite3.Connection = Depends(get_db)) -> None:
        if not enabled(conn, key):
            raise HTTPException(403, f"Das Modul „{MODULES[key]}“ ist abgeschaltet (Verwaltung → Module)")
    return _check
