"""Schnelleingabe für Aufgaben: „Backup prüfen morgen !hoch #praxis @anna“.

Erkannt wird (Groß-/Kleinschreibung egal, Reihenfolge egal):
- Fälligkeit: heute, morgen, übermorgen, Wochentage (mo/montag … so./sonntag → der nächste dieser Tag),
  „nächste woche“ (Montag), „in 3 tagen“, Datum „12.11.“ oder „12.11.2026“
- Priorität: !hoch / !1, !mittel / !2, !normal / !niedrig / !3
- Projekt oder Aufgabenliste: #name (ohne Leerzeichen, Anfang reicht – „#praxis“ findet „Praxis Henze“)
- Zuständige (nur im Team-Modus): @benutzername oder @vorname

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
    i = 0
    while i < len(words):
        w = words[i]
        low = w.lower()
        nxt = words[i + 1].lower() if i + 1 < len(words) else ""
        if w.startswith("\\") and len(w) > 1:
            out.append(w[1:])
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
    return {
        "title": " ".join(out).strip(),
        "due_date": due.isoformat() if due else None,
        "priority": prio,
        "project_id": project_id,
        "assignee_ids": assignees,
        "unknown": unknown,
    }
