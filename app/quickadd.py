"""Schnelleingabe für Aufgaben: „Backup prüfen morgen !hoch #praxis @anna“.

Erkannt wird (Groß-/Kleinschreibung egal, Reihenfolge egal):
- Fälligkeit: heute, morgen, übermorgen, Wochentage (mo/montag … so./sonntag → der nächste dieser Tag),
  „nächste woche“ (Montag), „in 3 tagen“, Datum „12.11.“ oder „12.11.2026“
- Priorität: !hoch / !1, !mittel / !2, !normal / !niedrig / !3
- Projekt oder Aufgabenliste: #name (ohne Leerzeichen, Anfang reicht – „#praxis“ findet „Praxis Henze“)
- Zuständige (nur im Team-Modus): @benutzername oder @vorname
- Uhrzeit (Erinnerung): um 14, um 14:30, 14:30, 14 uhr
- Wiederholung: täglich, werktags, wöchentlich, monatlich, jährlich, jeden tag/montag/…/monat, alle 2 wochen

Was nicht erkannt wird, bleibt im Titel. Ein Wort lässt sich mit einem vorangestellten \\ schützen
(„\\morgen“ bleibt als „morgen“ im Titel).
"""
import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

# „so“ ist ein häufiges Wort – Sonntag deshalb nur als „so.“ oder „sonntag“
WEEKDAYS = {
    "mo": 0, "mo.": 0, "montag": 0, "di": 1, "di.": 1, "dienstag": 1, "mi": 2, "mi.": 2, "mittwoch": 2,
    "do": 3, "do.": 3, "donnerstag": 3, "fr": 4, "fr.": 4, "freitag": 4, "sa": 5, "sa.": 5, "samstag": 5,
    "so.": 6, "sonntag": 6,
}
PRIORITIES = {"hoch": 1, "1": 1, "wichtig": 1, "mittel": 2, "2": 2, "normal": 3, "niedrig": 3, "3": 3}
_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})?$")
_NORM = re.compile(r"[\s_\-.]+")
_TIME = re.compile(r"^([01]?\d|2[0-3])(?::([0-5]\d))?$")
RECUR_WORDS = {"täglich": "d:1", "taeglich": "d:1", "werktags": "wd", "wöchentlich": "w:1", "woechentlich": "w:1",
               "monatlich": "m:1", "jährlich": "y:1", "jaehrlich": "y:1"}
EVERY = {"tag": "d:1", "woche": "w:1", "monat": "m:1", "jahr": "y:1", "werktag": "wd"}
UNIT = {"tag": "d", "tage": "d", "tagen": "d", "woche": "w", "wochen": "w", "monat": "m", "monate": "m", "monaten": "m",
        "jahr": "y", "jahre": "y", "jahren": "y"}


def _norm(s: str) -> str:
    return _NORM.sub("", (s or "").lower())


def _date_word(word: str, today: date) -> Optional[date]:
    w = word.lower().rstrip(",;")
    if w in WEEKDAYS:
        pass
    elif w.endswith(".") and not _DATE.match(w):
        w = w.rstrip(".")
    if w == "heute":
        return today
    if w == "morgen":
        return today + timedelta(days=1)
    if w in ("übermorgen", "uebermorgen"):
        return today + timedelta(days=2)
    if w in WEEKDAYS:
        ahead = (WEEKDAYS[w] - today.weekday()) % 7 or 7  # immer der nächste – heute zählt nicht
        return today + timedelta(days=ahead)
    m = _DATE.match(w)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = (2000 + int(y) if len(y) == 2 else int(y)) if y else today.year
        try:
            result = date(year, mo, d)
        except ValueError:
            return None
        if not y and result < today:  # „3.1.“ im Dezember = nächstes Jahr
            try:
                result = date(year + 1, mo, d)
            except ValueError:
                return None
        return result
    return None


def parse(text: str, today: date, projects: Sequence[Dict] = (), users: Sequence[Dict] = ()) -> Dict:
    """Zerlegt die Eingabe. projects: [{id, name}], users: [{id, username, display_name}].

    Liefert {title, due_date, priority, project_id, assignee_ids, unknown: [nicht gefundene #/@-Angaben]}."""
    words = (text or "").split()
    out: List[str] = []
    due: Optional[date] = None
    prio: Optional[int] = None
    project_id: Optional[int] = None
    assignees: List[int] = []
    unknown: List[str] = []
    recur: Optional[str] = None
    due_time: Optional[str] = None
    i = 0
    while i < len(words):
        w = words[i]
        low = w.lower()
        nxt = words[i + 1].lower() if i + 1 < len(words) else ""
        nxt2 = words[i + 2].lower().rstrip(",") if i + 2 < len(words) else ""
        if w.startswith("\\") and len(w) > 1:
            out.append(w[1:])
        elif low.rstrip(",") in RECUR_WORDS and recur is None:
            recur = RECUR_WORDS[low.rstrip(",")]
        elif low in ("jeden", "jede", "jedes") and nxt.rstrip(",") in EVERY and recur is None:
            recur = EVERY[nxt.rstrip(",")]
            i += 1
        elif low in ("jeden", "jede") and nxt.rstrip(",.") in WEEKDAYS and recur is None:
            recur = "w:1"
            if due is None:
                due = _date_word(nxt.rstrip(","), today)
            i += 1
        elif low == "alle" and nxt.isdigit() and 1 <= int(nxt) <= 99 and nxt2 in UNIT and recur is None:
            recur = f"{UNIT[nxt2]}:{int(nxt)}"
            i += 2
        elif low == "um" and _TIME.match(nxt.rstrip(",")) and due_time is None:
            m = _TIME.match(nxt.rstrip(","))
            due_time = f"{int(m.group(1)):02d}:{m.group(2) or '00'}"
            i += 1
            if nxt2 == "uhr":
                i += 1
        elif due_time is None and re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", low.rstrip(",")):
            h, mi = low.rstrip(",").split(":")
            due_time = f"{int(h):02d}:{mi}"
            if nxt == "uhr":
                i += 1
        elif due_time is None and _TIME.match(low) and ":" not in low and nxt == "uhr":
            due_time = f"{int(low):02d}:00"
            i += 1
        elif low in ("nächste", "naechste") and nxt in ("woche", "woche,"):
            due = today + timedelta(days=7 - today.weekday())
            i += 1
        elif low == "in" and nxt.isdigit() and i + 2 < len(words) and words[i + 2].lower().rstrip(",") in ("tag", "tagen", "tage"):
            due = today + timedelta(days=int(nxt))
            i += 2
        elif low.startswith("!") and low[1:] in PRIORITIES:
            prio = PRIORITIES[low[1:]]
        elif low.startswith("#") and len(low) > 1:
            key = _norm(w[1:])
            hits = [p for p in projects if _norm(p["name"]) == key] or \
                   [p for p in projects if _norm(p["name"]).startswith(key)]
            if len(hits) == 1:
                project_id = hits[0]["id"]
            else:
                unknown.append(w)
                out.append(w)
        elif low.startswith("@") and len(low) > 1:
            key = low[1:]
            hits = [u for u in users if u["username"].lower() == key] or \
                   [u for u in users if (u["display_name"] or "").lower().split()[:1] == [key]]
            if len(hits) == 1:
                if hits[0]["id"] not in assignees:
                    assignees.append(hits[0]["id"])
            else:
                unknown.append(w)
                out.append(w)
        else:
            d = _date_word(w, today)
            if d is not None and due is None:
                due = d
            else:
                out.append(w)
        i += 1
    if (recur or due_time) and due is None:
        due = today
    return {
        "title": " ".join(out).strip(),
        "due_date": due.isoformat() if due else None,
        "priority": prio,
        "project_id": project_id,
        "assignee_ids": assignees,
        "unknown": unknown,
        "recur": recur,
        "due_time": due_time,
    }
