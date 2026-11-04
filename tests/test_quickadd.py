"""Schnelleingabe: Datum, Priorität, Liste, Zuständige."""
from datetime import date

from app.quickadd import parse

MI = date(2026, 11, 4)  # ein Mittwoch
PROJECTS = [{"id": 1, "name": "Praxis Henze"}, {"id": 2, "name": "Büro allgemein"}, {"id": 3, "name": "Praxis Nord"},
            {"id": 4, "name": "Einkauf"}]
USERS = [{"id": 10, "username": "anna", "display_name": "Anna Beispiel"},
         {"id": 11, "username": "mk", "display_name": "Max Kurz"}]


def p(text, today=MI):
    return parse(text, today, PROJECTS, USERS)


def test_dates():
    assert p("Backup prüfen morgen")["due_date"] == "2026-11-05"
    assert p("heute Toner")["due_date"] == "2026-11-04"
    assert p("übermorgen x")["due_date"] == "2026-11-06"
    assert p("x fr")["due_date"] == "2026-11-06"
    assert p("x Freitag")["due_date"] == "2026-11-06"
    assert p("x mi")["due_date"] == "2026-11-11"          # heute ist Mittwoch → nächste Woche
    assert p("x nächste woche")["due_date"] == "2026-11-09"
    assert p("x in 10 tagen")["due_date"] == "2026-11-14"
    assert p("x 12.11.")["due_date"] == "2026-11-12"
    assert p("x 3.1.")["due_date"] == "2027-01-03"         # schon vorbei → nächstes Jahr
    assert p("x 24.12.2027")["due_date"] == "2027-12-24"
    assert p("x 31.02.")["due_date"] is None and p("x 31.02.")["title"] == "x 31.02."
    assert p("x morgen.")["due_date"] == "2026-11-05"


def test_common_words_stay():
    r = p("So geht das")                                    # „so“ ist kein Sonntag
    assert r["due_date"] is None and r["title"] == "So geht das"
    r = p("Termin \\morgen absagen heute")                  # \ schützt, nur das erste Datum zählt
    assert r["title"] == "Termin morgen absagen" and r["due_date"] == "2026-11-04"
    r = p("Wiedervorlage morgen und Freitag")
    assert r["due_date"] == "2026-11-05" and r["title"] == "Wiedervorlage und Freitag"


def test_priority_project_people():
    r = p("Backup prüfen morgen !hoch #praxishenze @anna")
    assert r == {"title": "Backup prüfen", "due_date": "2026-11-05", "priority": 1, "project_id": 1,
                 "assignee_ids": [10], "unknown": []}
    assert p("x !2")["priority"] == 2 and p("x !niedrig")["priority"] == 3 and p("x !super")["priority"] is None
    assert p("x #büro")["project_id"] == 2
    assert p("x #ein")["project_id"] == 4
    r = p("x #praxis")                                       # mehrdeutig → bleibt im Titel
    assert r["project_id"] is None and r["unknown"] == ["#praxis"] and "#praxis" in r["title"]
    r = p("x @max @mk @niemand")
    assert r["assignee_ids"] == [11] and r["unknown"] == ["@niemand"]
    assert parse("x @anna", MI, PROJECTS, [])["assignee_ids"] == []   # Einzelperson: keine Personen
