"""SQLite-Datenbank: Schema, schrittweise Migrationen (PRAGMA user_version) und Verbindungen.

Neue Tabellen oder Spalten kommen IMMER als neuer Migrationsschritt dazu – bestehende Schritte
werden nie verändert. So kommt jede Datenbank (auch eine alte Sicherung) Schritt für Schritt
auf den aktuellen Stand, ohne dass Daten verloren gehen.
"""
import secrets
import sqlite3
from typing import Callable, Dict, Iterator

from .config import MODES, settings
from .timeutil import now_utc_str


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _run(conn: sqlite3.Connection, script: str) -> None:
    """Mehrere Anweisungen einzeln ausführen (executescript würde die laufende Transaktion beenden)."""
    for stmt in script.split(";\n"):
        if stmt.strip():
            conn.execute(stmt)


def _m1_basis(conn: sqlite3.Connection) -> None:
    """Version 0.1: Benutzer, Sitzungen, Änderungsprotokoll, Einstellungen."""
    _run(conn, """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL,
    email         TEXT,
    password_hash TEXT,                       -- NULL = Anmeldung nur mit DSM-Konto
    is_admin      INTEGER NOT NULL DEFAULT 0, -- Chef/Admin: darf alles
    active        INTEGER NOT NULL DEFAULT 1,
    theme         TEXT,                       -- eigenes Design, NULL = Standard der Firma
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor_id  INTEGER,
    action    TEXT NOT NULL,
    target    TEXT,
    details   TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
""")


def _m2_aufgaben(conn: sqlite3.Connection) -> None:
    """Version 0.2: Kunden, Projekte und Aufgabenlisten, Aufgaben mit Zuständigen, Erinnerungen."""
    _run(conn, """
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL DEFAULT 'project',   -- project (Kunde/Zeiten) | list (reine Aufgabenliste)
    name        TEXT NOT NULL,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    color       TEXT NOT NULL DEFAULT '#0f766e',
    archived    INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    title         TEXT NOT NULL,
    notes         TEXT NOT NULL DEFAULT '',
    due_date      TEXT,                          -- YYYY-MM-DD (lokal)
    priority      INTEGER NOT NULL DEFAULT 3,    -- 1 hoch, 2 mittel, 3 normal
    done          INTEGER NOT NULL DEFAULT 0,
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT,
    completed_at  TEXT,
    completed_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, done);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(done, due_date);

CREATE TABLE IF NOT EXISTS task_assignees (
    task_id  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_assignees_user ON task_assignees(user_id);

CREATE TABLE IF NOT EXISTS reminder_log (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day      TEXT NOT NULL,                      -- YYYY-MM-DD (lokal)
    status   TEXT NOT NULL,                      -- sent | empty | error
    detail   TEXT,
    ts       TEXT NOT NULL,
    PRIMARY KEY (user_id, day)
);
""")
    if "remind" not in _columns(conn, "users"):
        # tägliche Erinnerungs-Mail an diese Person (wenn E-Mail hinterlegt)
        conn.execute("ALTER TABLE users ADD COLUMN remind INTEGER NOT NULL DEFAULT 1")


def _m3_zeiten(conn: sqlite3.Connection) -> None:
    """Version 0.3: Zeitbuchungen auf Projekte (Stoppuhr oder nachgetragen)."""
    _run(conn, """
CREATE TABLE IF NOT EXISTS time_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    task_id     INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    start_utc   TEXT NOT NULL,
    end_utc     TEXT,                              -- NULL = Stoppuhr läuft
    note        TEXT NOT NULL DEFAULT '',
    on_site     INTEGER NOT NULL DEFAULT 0,        -- vor Ort beim Kunden
    created_at  TEXT NOT NULL,
    updated_at  TEXT,
    updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_time_user_start ON time_entries(user_id, start_utc);
CREATE INDEX IF NOT EXISTS idx_time_project_start ON time_entries(project_id, start_utc);
CREATE INDEX IF NOT EXISTS idx_time_task ON time_entries(task_id);
-- höchstens eine laufende Stoppuhr pro Person
CREATE UNIQUE INDEX IF NOT EXISTS idx_time_one_running ON time_entries(user_id) WHERE end_utc IS NULL;
""")


def _m4_komfort(conn: sqlite3.Connection) -> None:
    """Version 0.4: Wiederholung, Uhrzeit und Checkliste an Aufgaben, Projektbudget, Abrechnungs-Kennzeichen."""
    cols = _columns(conn, "tasks")
    for name, ddl in (("recur", "TEXT"),                 # d:1, wd, w:2, m:1, y:1 – siehe recur.py
                      ("recur_next_id", "INTEGER"),      # schon angelegte Folgeaufgabe
                      ("due_time", "TEXT"),              # HH:MM – Erinnerung zu dieser Uhrzeit
                      ("reminded_at", "TEXT")):
        if name not in cols:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {ddl}")
    _run(conn, """
CREATE TABLE IF NOT EXISTS task_items (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    title    TEXT NOT NULL,
    done     INTEGER NOT NULL DEFAULT 0,
    sort     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_items_task ON task_items(task_id, sort);
""")
    cols = _columns(conn, "projects")
    if "budget_minutes" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN budget_minutes INTEGER")
    if "budget_warned" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN budget_warned INTEGER NOT NULL DEFAULT 0")  # 0, 80 oder 100
    cols = _columns(conn, "time_entries")
    for name, ddl in (("billed_at", "TEXT"), ("billed_by", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
                      ("billed_ref", "TEXT")):
        if name not in cols:
            conn.execute(f"ALTER TABLE time_entries ADD COLUMN {name} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_time_billed ON time_entries(billed_at)")


def _m5_kalender(conn: sqlite3.Connection) -> None:
    """Version 0.5: Kalender mit Terminen, Serien, Teilnehmern und iCal-Abo."""
    _run(conn, """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    start_utc     TEXT NOT NULL,
    end_utc       TEXT NOT NULL,
    all_day       INTEGER NOT NULL DEFAULT 0,
    location      TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    recur         TEXT,                          -- wie bei Aufgaben: d:1, wd, w:1, m:1@15, y:1
    recur_until   TEXT,                          -- letztes Datum der Serie (lokal), NULL = endlos
    alarm_minutes INTEGER,                       -- Erinnerung im Kalender des Handys (Minuten vorher)
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT,
    uid           TEXT NOT NULL                  -- fest für iCal
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_utc);

CREATE TABLE IF NOT EXISTS event_attendees (
    event_id  INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_event_att_user ON event_attendees(user_id);

CREATE TABLE IF NOT EXISTS event_exceptions (
    event_id  INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    day       TEXT NOT NULL,                     -- dieser Termin der Serie fällt aus (lokales Datum)
    PRIMARY KEY (event_id, day)
);

CREATE TABLE IF NOT EXISTS calendar_tokens (
    user_id        INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_hash     TEXT NOT NULL UNIQUE,
    with_tasks     INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    last_used_at   TEXT
);
""")


def _m6_dienstplan(conn: sqlite3.Connection) -> None:
    """Version 0.6: Dienstplan mit Schichtarten, Schichten und veröffentlichten Wochen."""
    _run(conn, """
CREATE TABLE IF NOT EXISTS shift_types (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    short         TEXT NOT NULL,                 -- Kürzel im Raster, z. B. F, S, N, U
    kind          TEXT NOT NULL DEFAULT 'work',  -- work (Dienst) | off (Abwesenheit: Urlaub, krank, frei)
    start_time    TEXT,                          -- HH:MM (nur Dienst)
    end_time      TEXT,                          -- HH:MM; kleiner als Beginn = über Mitternacht
    break_minutes INTEGER NOT NULL DEFAULT 0,
    color         TEXT NOT NULL DEFAULT '#0f766e',
    sort          INTEGER NOT NULL DEFAULT 0,
    archived      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shifts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day            TEXT NOT NULL,                -- YYYY-MM-DD (lokal), Tag des Beginns
    shift_type_id  INTEGER REFERENCES shift_types(id) ON DELETE SET NULL,
    kind           TEXT NOT NULL DEFAULT 'work',
    start_time     TEXT,
    end_time       TEXT,
    break_minutes  INTEGER NOT NULL DEFAULT 0,
    label          TEXT NOT NULL DEFAULT '',     -- Name zum Zeitpunkt der Planung
    short          TEXT NOT NULL DEFAULT '',
    color          TEXT NOT NULL DEFAULT '#0f766e',
    note           TEXT NOT NULL DEFAULT '',
    created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_shifts_day ON shifts(day, user_id);
CREATE INDEX IF NOT EXISTS idx_shifts_user ON shifts(user_id, day);

CREATE TABLE IF NOT EXISTS shift_weeks (
    week_start    TEXT PRIMARY KEY,              -- Montag YYYY-MM-DD
    published_at  TEXT,
    published_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
);
""")
    if "in_roster" not in _columns(conn, "users"):
        # im Dienstplan aufführen (z. B. Chef ohne Schichten ausblenden)
        conn.execute("ALTER TABLE users ADD COLUMN in_roster INTEGER NOT NULL DEFAULT 1")
    if not conn.execute("SELECT 1 FROM shift_types LIMIT 1").fetchone():
        now = now_utc_str()
        conn.executemany(
            "INSERT INTO shift_types(name, short, kind, start_time, end_time, break_minutes, color, sort, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [("Frühdienst", "F", "work", "06:00", "14:00", 30, "#0891b2", 1, now),
             ("Spätdienst", "S", "work", "14:00", "22:00", 30, "#7c3aed", 2, now),
             ("Nachtdienst", "N", "work", "22:00", "06:00", 30, "#1e3a8a", 3, now),
             ("Urlaub", "U", "off", None, None, 0, "#16a34a", 10, now),
             ("Krank", "K", "off", None, None, 0, "#dc2626", 11, now),
             ("Frei", "–", "off", None, None, 0, "#94a3b8", 12, now)])


# Version → Schritt. Nur anhängen, nie ändern!
MIGRATIONS: Dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _m1_basis,
    2: _m2_aufgaben,
    3: _m3_zeiten,
    4: _m4_komfort,
    5: _m5_kalender,
    6: _m6_dienstplan,
}
SCHEMA_VERSION = max(MIGRATIONS)

DEFAULT_SETTINGS = {
    # Betriebsart: single (eine Person, keine Rollen sichtbar) | team
    "mode": "single",
    "company_name": "",
    "theme_default": "classic",
    # Öffentliche Adresse (Reverse Proxy) fürs DSM-Fenster; leer = automatisch
    "public_url": "",
    # Anmeldung mit DSM-Konten
    "dsm_login": "1",        # Login-Formular prüft auch DSM-Passwörter
    "dsm_auto_create": "1",  # unbekannte DSM-Benutzer beim ersten Login anlegen (nur Team-Modus)
    "dsm_admins": "1",       # DSM-Administratoren werden Chef/Admin
    "dsm_url": "http://127.0.0.1:5000",
    # Mailversand (SMTP)
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_security": "starttls",   # starttls | ssl | none
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_from_name": "Zeitmanagement",
    # Datensicherung: jede Nacht, lokal + optional in einen freigegebenen Ordner
    "backup_enabled": "1",
    "backup_hour": "2",
    "backup_keep_days": "30",
    "backup_dir": "",              # z. B. /volume1/backup/zeitmanagement
    # Tägliche Erinnerungs-Mail (fällige und überfällige Aufgaben)
    "reminders_enabled": "1",
    "reminder_hour": "7",
}


class MigrationError(RuntimeError):
    pass


def migrate(conn: sqlite3.Connection) -> int:
    """Bringt die Datenbank Schritt für Schritt auf SCHEMA_VERSION. Liefert die Ausgangsversion.

    Jeder Schritt läuft in einer eigenen Transaktion und setzt user_version erst am Ende – bricht
    ein Schritt ab, bleibt die Datenbank auf dem letzten vollständigen Stand."""
    start = conn.execute("PRAGMA user_version").fetchone()[0]
    if start > SCHEMA_VERSION:
        raise MigrationError(
            f"Die Datenbank stammt aus einer neueren Version (Schema {start}, diese App kennt {SCHEMA_VERSION}). "
            "Bitte die neueste Version des Pakets installieren.")
    for version in range(start + 1, SCHEMA_VERSION + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            MIGRATIONS[version](conn)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return start


def connect() -> sqlite3.Connection:
    # Autocommit: jede Änderung ist sofort gespeichert (FastAPI räumt get_db() erst nach dem Senden auf).
    # check_same_thread=False: FastAPI öffnet die Verbindung u. U. in einem anderen Thread als dem des
    # Endpunkts. Jede Verbindung gehört trotzdem nur einem Request.
    conn = sqlite3.connect(str(settings.db_path), timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI-Dependency: eine Verbindung pro Request (Autocommit)."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Legt Datenordner, Schema und (beim ersten Start) den Chef-Zugang an."""
    from .security import hash_password  # vermeidet Import-Zyklus

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        migrate(conn)
        first_start = not conn.execute("SELECT 1 FROM app_settings LIMIT 1").fetchone()
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO app_settings(key, value) VALUES (?, ?)", (key, value))
        if first_start:
            # Werte aus dem Installationsassistenten (nur beim allerersten Start)
            if settings.initial_mode in MODES:
                set_setting(conn, "mode", settings.initial_mode)
            if settings.initial_company.strip():
                set_setting(conn, "company_name", settings.initial_company.strip()[:80])

        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            password = settings.initial_admin_password or secrets.token_urlsafe(12)
            conn.execute(
                "INSERT INTO users(username, display_name, password_hash, is_admin, created_at) "
                "VALUES ('admin', 'Administrator', ?, 1, ?)",
                (hash_password(password), now_utc_str()),
            )
            if not settings.initial_admin_password:
                cred_file = settings.data_dir / "ERSTES_ADMIN_PASSWORT.txt"
                cred_file.write_text(
                    "Benutzer: admin\nPasswort: " + password + "\n\n"
                    "Bitte nach dem ersten Login ändern und diese Datei löschen.\n",
                    encoding="utf-8",
                )
                print(f"[zeitmanagement] Admin angelegt, Passwort steht in {cred_file}", flush=True)
    finally:
        conn.close()


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO app_settings(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def is_team(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, "mode") == "team"


def audit(conn: sqlite3.Connection, actor_id, action: str, target: str = "", details: str = "") -> None:
    """Eintrag ins Änderungsprotokoll. Einträge werden nie geändert oder gelöscht."""
    conn.execute(
        "INSERT INTO audit_log(ts, actor_id, action, target, details) VALUES (?, ?, ?, ?, ?)",
        (now_utc_str(), actor_id, action, target or "", (details or "")[:2000]),
    )
