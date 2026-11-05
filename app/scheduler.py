"""Hintergrund-Aufgaben: Erinnerungs-Mails, alte Tickets löschen, nächtliche Datensicherung und Update-Prüfung.

Ein Thread, der alle paar Minuten nachsieht, ob etwas fällig ist. Fehler werden protokolliert,
stoppen aber nie die App.
"""
import threading
from typing import Optional

from .config import settings

_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def run_once() -> None:
    try:
        from . import reminders
        from .db import connect
        conn = connect()
        try:
            for r in reminders.run_timed(conn):
                print(f"[zeitmanagement] Erinnerung Aufgabe {r['task_id']}: {r['status']}", flush=True)
            for r in reminders.run_due(conn):
                state = "verschickt" if r["status"] == "sent" else r["status"] + (": " + r["error"] if r.get("error") else "")
                print(f"[zeitmanagement] Erinnerung für Benutzer {r['user_id']}: {state}", flush=True)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[zeitmanagement] Erinnerungen FEHLER: {e}", flush=True)
    try:
        from . import helpdesk
        from .db import connect as _connect
        conn = _connect()
        try:
            n = helpdesk.purge_old(conn)
            if n:
                print(f"[zeitmanagement] {n} alte erledigte Tickets gelöscht", flush=True)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[zeitmanagement] Helpdesk-Aufräumen FEHLER: {e}", flush=True)
    try:
        from . import backup
        b = backup.run_due()
        if b:
            print(f"[zeitmanagement] Sicherung {b['name']} erstellt"
                  + (f" – Ordner: {b['shared_error']}" if b["shared_error"] else ""), flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[zeitmanagement] Datensicherung FEHLER: {e}", flush=True)
    if settings.update_check:
        try:
            from . import updates  # neue Version auf GitHub? (höchstens alle 30 Minuten)
            st = updates.check()
            if st["error"]:
                print(f"[zeitmanagement] Update-Prüfung: {st['error']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[zeitmanagement] Update-Prüfung FEHLER: {e}", flush=True)


def _loop(interval: float) -> None:
    if _stop.wait(60):  # nach dem Start kurz warten
        return
    while not _stop.is_set():
        run_once()
        _stop.wait(interval)


def start(interval: float = 60) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), name="zeitmanagement-zeitplan", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
