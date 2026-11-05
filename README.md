# Zeitmanagement für Synology

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-☕-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/lapalutschi)

[![Tests](https://github.com/rinkelzz/zeitmanagement/actions/workflows/ci.yml/badge.svg)](https://github.com/rinkelzz/zeitmanagement/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/rinkelzz/zeitmanagement?include_prereleases)](https://github.com/rinkelzz/zeitmanagement/releases)
[![Lizenz: MIT](https://img.shields.io/badge/Lizenz-MIT-blue.svg)](LICENSE)

Aufgaben, Projektzeiten, Kalender und Dienstplan für kleine Firmen, Praxen und Einzelpersonen – als Paket
auf der eigenen Synology NAS, mit eigenem Fenster in DSM. Wer in DSM angemeldet ist, ist es auch hier.
Die Daten bleiben im Haus.

> **Stand:** Version 0.3 – Aufgaben und Projektzeiten mit Auswertung als PDF, Excel und CSV.
> Kalender und Dienstplan folgen – siehe [Fahrplan](#fahrplan).

![Aufgaben](docs/screenshots/aufgaben.png)

## Inhalt

- [Funktionen](#funktionen)
- [Aufgaben](#aufgaben)
- [Projektzeiten und Auswertung](#projektzeiten-und-auswertung)
- [Fahrplan](#fahrplan)
- [Installation auf der Synology](#installation-auf-der-synology)
- [Updates über das Paket-Zentrum](#updates-über-das-paket-zentrum)
- [Datensicherung](#datensicherung)
- [Von unterwegs erreichbar machen (Reverse Proxy)](#von-unterwegs-erreichbar-machen-reverse-proxy)
- [Port ändern](#port-ändern)
- [Entwicklung](#entwicklung)
- [English](#english)

## Funktionen

- **Aufgaben** mit Fälligkeit, Priorität, Projekten und Aufgabenlisten, Notizen und (im Team) Zuständigen.
  Ansichten Heute, Überfällig, Demnächst, Meine Aufgaben, Alle offenen, Ohne Liste und Erledigt.
- **Schnelleingabe** in einer Zeile: „Backup prüfen morgen !hoch #büro @anna“ – die App zeigt vor dem Speichern, was sie erkannt hat.
- **Tägliche Erinnerungs-Mail** mit überfälligen und heute fälligen Aufgaben, Uhrzeit einstellbar, pro Person abschaltbar.
- **Projektzeiten** per Stoppuhr (auch direkt an der Aufgabe mit ▶) oder nachgetragen, mit Kennzeichen „vor Ort“.
  Die laufende Stoppuhr steht immer in der Kopfzeile.
- **Auswertung** nach Kunde, Projekt, Aufgabe, Mitarbeiter oder Tag – als **PDF, Excel und CSV**, mit Firmenname und Logo.
- **Zwei Betriebsarten**, jederzeit umschaltbar:
  - **Einzelperson** – nur du, ohne Benutzer, Rollen und Zuständige. Jeder DSM-Administrator landet in deinem Konto.
  - **Team** – Chef/Admin (darf alles) und Mitarbeiter (sehen nur ihre eigenen Projekte).
- **Eigenes DSM-Fenster** mit **Single Sign-on**: kein zweites Login für DSM-Benutzer. Alternativ Anmeldung mit
  DSM-Konto (inklusive Zwei-Faktor-Code) oder eigenem Konto – z. B. am Handy über den Reverse Proxy.
- **Firmenname und Logo** in Kopfzeile, Anmeldung und Berichten – jede Installation entscheidet selbst.
- **Vier Designs:** Klassisch (folgt hell/dunkel des Geräts), Synology DSM, Dunkel, Warm & groß. Für Handy und Desktop.
- **Nächtliche Datensicherung** in einen freigegebenen Ordner (für Hyper Backup), Wiederherstellen per Klick,
  alte Sicherungen löschbar. Logo und Einstellungen stecken in der Datenbank und sind damit mitgesichert.
- **Updates übers Paket-Zentrum** – die App ist ihre eigene Paketquelle.
- **Änderungsprotokoll** aller wichtigen Aktionen, filterbar und als CSV.
- **Port änderbar** im Installationsassistenten und später in der App – sie startet sich selbst neu.
- **Sicherheit von Anfang an** – siehe [SECURITY.md](SECURITY.md).

| | |
|---|---|
| ![Zeiten](docs/screenshots/zeiten.png) | ![Auswertung](docs/screenshots/auswertung.png) |
| ![Projekt bearbeiten](docs/screenshots/projekt.png) | ![Einstellungen](docs/screenshots/einstellungen.png) |
| ![Anmeldung](docs/screenshots/anmeldung.png) | ![Mein Zugang](docs/screenshots/mein-zugang.png) |

## Aufgaben

**Projekte und Aufgabenlisten.** Ein *Projekt* gehört optional zu einem Kunden und ist ab Version 0.3 auch das Ziel für
Zeitbuchungen. Eine *Aufgabenliste* ist eine einfache Sammlung ohne Kunde, z. B. „Büro allgemein“ oder „Einkauf“.
Aufgaben ohne Liste landen unter „Ohne Liste“.

**Wer sieht was (Team-Modus)?**

| | Chef/Admin | Mitarbeiter |
|---|---|---|
| Projekte anlegen | ✅ | – |
| Aufgabenlisten anlegen | ✅ | ✅ (wird automatisch Mitglied) |
| Projekte und Listen sehen | alle | nur die, in denen er Mitglied ist |
| Aufgaben sehen | alle | in seinen Projekten/Listen, ihm zugewiesene und eigene ohne Liste |
| Aufgaben löschen | alle | selbst angelegte |

**Schnelleingabe** – alles in einer Zeile, Reihenfolge egal:

| Kürzel | Bedeutung |
|---|---|
| `heute`, `morgen`, `übermorgen` | Fälligkeit |
| `mo` … `sa`, `so.`, `montag` … `sonntag` | der nächste dieser Wochentage |
| `nächste woche`, `in 3 tagen`, `12.11.`, `12.11.2026` | Fälligkeit |
| `!hoch`, `!mittel`, `!normal` (oder `!1`, `!2`, `!3`) | Priorität |
| `#büro` | Projekt oder Liste – ohne Leerzeichen, der Anfang reicht |
| `@anna` | zuständig (Benutzername oder Vorname, nur im Team) |
| `\morgen` | bleibt als Wort im Titel |

![Schnelleingabe](docs/screenshots/schnelleingabe.png)

**Erinnerungen:** Unter **Verwaltung → Mail** den Mailserver eintragen und die Uhrzeit wählen (Standard 7 Uhr). Jede Person
mit E-Mail-Adresse bekommt dann morgens eine Sammel-Mail mit ihren überfälligen und heute fälligen Aufgaben – im Team die
ihr zugewiesenen und eigene ohne Zuständige. Ist nichts fällig, kommt keine Mail. Unter **Mein Zugang** lässt sich die
Erinnerung abschalten oder sofort testen.

## Projektzeiten und Auswertung

**Buchen.** Zeiten gehen immer auf ein *Projekt* (Aufgabenlisten sind nur für To-dos), optional auf eine Aufgabe darin:

- **Stoppuhr** unter „Zeiten“ oder mit **▶** direkt an einer Aufgabe. Pro Person läuft höchstens eine Uhr – wer ein anderes
  Projekt startet, beendet die laufende automatisch. Die Uhr läuft auf der NAS weiter, auch wenn Browser oder Handy zu
  sind. Den Beginn kann man nachträglich korrigieren; eine vergessene Uhr wird beim Stoppen auf 24 Stunden gekürzt.
- **Nachtragen** mit Datum, von/bis oder Dauer (z. B. `1:30` oder `1,5`). Der Chef kann auch für andere nachtragen.
- **vor Ort** markiert Termine beim Kunden (steht in Auswertung und Export).
- Buchungen derselben Person dürfen sich nicht überschneiden. Ändern und löschen geht in der Wochenansicht.

**Auswerten.** Zeitraum (Woche, Monat, Jahr oder frei), Kunde, Projekt und – für den Chef – Mitarbeiter wählen, gruppieren
nach Kunde → Projekt, Projekt, Projekt → Aufgabe, Mitarbeiter → Projekt oder Tag. Summen oder Einzelbuchungen,
Export als PDF (mit Logo, mehrseitig), Excel (Übersicht + Buchungen) oder CSV (Semikolon, für Excel). Gezählt werden nur
abgeschlossene Buchungen. Ohne Stundensätze und ohne Rundung – die Stunden sind die Grundlage für deine Rechnung.

Im Team sieht ein Mitarbeiter nur seine eigenen Zeiten und kann nur auf Projekte buchen, in denen er Mitglied ist.
Der Chef sieht alles.

![PDF-Bericht](docs/screenshots/bericht-pdf.png)

## Fahrplan

| Version | Inhalt |
|---|---|
| **0.1** ✅ | Fundament: Anmeldung (lokal + DSM, SSO), Einzelperson/Team, Benutzer, Firma & Logo, Designs, Mail, Port, Datensicherung, Updates, Protokoll |
| **0.2** ✅ | **Aufgaben:** Aufgabenlisten und Projekte, Fälligkeit, Priorität, Zuständige, Schnelleingabe („morgen !hoch“), tägliche Erinnerungs-Mail |
| **0.3** ✅ | **Projektzeiten:** Kunden und Projekte, Stoppuhr oder Nachtragen, Auswertung nach Kunde/Projekt/Mitarbeiter, Export PDF/Excel/CSV |
| später | Wiederkehrende Aufgaben, Unteraufgaben, Projektbudget · **Kalender** mit iCal-Abo fürs iPhone · **Dienstplan** mit Wochenplan als PDF |

## Installation auf der Synology

Voraussetzung: DSM 7. Ein Paket für alle Modelle – **Intel/AMD** und **ARM** (ARMv8, ARMv7).

1. Unter [Releases](https://github.com/rinkelzz/zeitmanagement/releases) die neueste
   `zeitmanagement-<version>-0001-noarch.spk` herunterladen.
2. DSM → **Paket-Zentrum** → **Manuelle Installation** → SPK auswählen → Hinweis zu Drittanbieter-Paketen bestätigen.
3. Im Assistenten wählen:
   - **Nur ich** oder **Ein Team** (später änderbar),
   - ein Passwort für den Benutzer `admin` (mindestens 8 Zeichen),
   - optional den **Firmennamen**,
   - den **Port** (Standard `8770` – nur ändern, wenn er schon belegt ist).
4. „Zeitmanagement“ erscheint im DSM-Hauptmenü.

Das Paket nutzt das Python der NAS (3.8 bis 3.13). Fehlt es, bricht die Installation mit einem Hinweis ab –
dann im Paket-Zentrum „Python 3.9“ installieren. Die Daten liegen unter `/var/packages/zeitmanagement/var/` und
bleiben bei Updates erhalten. **Beim Deinstallieren werden sie gelöscht** – deshalb die
[Datensicherung](#datensicherung) einrichten.

## Updates über das Paket-Zentrum

Die App ist ihre eigene Paketquelle – neue Releases von GitHub erscheinen im Paket-Zentrum zum Aktualisieren.
Einmalig einrichten:

1. **Paket-Zentrum → Einstellungen → Allgemein → Vertrauensebene:** „Beliebiger Herausgeber“
   (das Paket ist nicht von Synology signiert).
2. **Paket-Zentrum → Einstellungen → Paketquellen → Hinzufügen:** Name `Zeitmanagement`,
   Ort `http://127.0.0.1:8770/spk` (bei geändertem Port entsprechend – die genaue Adresse steht in der App
   unter **Verwaltung → System → Updates**).

Die App prüft etwa alle 30 Minuten (oder per „Jetzt prüfen“), lädt eine neue SPK auf die NAS und bietet sie dort an.
Die Paketquelle antwortet nur im lokalen Netz.

## Datensicherung

Die App sichert die Datenbank jede Nacht (Standard 2 Uhr) und behält die letzten 7 Sicherungen im Paketordner.
Weil DSM diesen Ordner beim Deinstallieren löscht und Hyper Backup ihn nicht erfasst, gehört zusätzlich ein
**freigegebener Ordner** dazu:

1. **Systemsteuerung → Freigegebener Ordner** → z. B. `backup` → **Bearbeiten → Berechtigungen** →
   oben **System-interner Benutzer** wählen → `zeitmanagement` **Lesen/Schreiben** geben.
2. In der App unter **Verwaltung → System → Datensicherung** den Ordner eintragen, z. B.
   `/volume1/backup/zeitmanagement` (die App prüft die Schreibrechte), und die Aufbewahrung in Tagen festlegen.
3. Den freigegebenen Ordner in Hyper Backup aufnehmen.

Sicherungen lassen sich dort auch von Hand erstellen, herunterladen, wiederherstellen und löschen (die neueste ist
geschützt); vor dem Wiederherstellen wird der aktuelle Stand automatisch gesichert. Das **Mail-Passwort wird nicht
mitgesichert** – auf einer neuen NAS trägst du es einmal neu ein. Den Ordner bitte **nicht für alle freigeben**.

## Von unterwegs erreichbar machen (Reverse Proxy)

Für Handy und Laptop unterwegs braucht die App eine eigene https-Adresse, z. B. `https://zeit.meinedomain.de`:

1. DNS-Eintrag für die Subdomain auf die NAS zeigen lassen (z. B. CNAME auf die DDNS-Adresse).
2. **Systemsteuerung → Sicherheit → Zertifikat**: Let's-Encrypt-Zertifikat für die Subdomain.
3. **Systemsteuerung → Anmeldeportal → Erweitert → Reverse Proxy**: Quelle `HTTPS zeit.meinedomain.de:443`,
   Ziel `HTTP localhost:8770`.
4. In der App unter **Verwaltung → Einstellungen → Öffentliche Adresse** genau diese Adresse eintragen.

**Wichtig:** Den Port im Router **nicht** direkt freigeben – sonst laufen Passwörter unverschlüsselt durchs Internet.
Greift jemand per `http://` von außerhalb des Heimnetzes zu, zeigt die App einen Warnhinweis.

## Port ändern

Unter **Verwaltung → System → Port** einen neuen Port eintragen (1025–65535, nicht von DSM belegt). Die App prüft,
ob er frei ist, speichert ihn in `/var/packages/zeitmanagement/var/config.env` und startet sich selbst neu.
Im DSM-Fenster geht es danach einfach weiter. Anpassen musst du nur, was du selbst eingerichtet hast:
die **Paketquelle** im Paket-Zentrum und das **Ziel im Reverse Proxy**.

## Entwicklung

```bash
pip install -r requirements-dev.txt
pytest -q                                   # alle Tests
ZM_DATA_DIR=./data python -m app.main       # startet auf http://localhost:8770
```

Beim ersten Start ohne `ZM_ADMIN_PASSWORD` steht das Admin-Passwort in `data/ERSTES_ADMIN_PASSWORT.txt`.
Die SPK baut `./synology/build_spk.sh` (lädt die Abhängigkeiten für Python 3.8–3.13 und alle Prozessoren von PyPI).

- **Technik:** Python + FastAPI + SQLite, statisches HTML/CSS/JS ohne Build-Schritt, keine nativen Bibliotheken.
- **Datenbank-Migrationen** über `PRAGMA user_version` in `app/db.py`: Neue Tabellen und Spalten kommen immer als
  neuer, nummerierter Schritt dazu; jeder Schritt läuft in einer eigenen Transaktion. Die Tests spielen jede
  Ausgangsversion durch.
- **Tests** laufen bei jedem Push auf Python 3.8 und 3.13 (GitHub Actions „Tests“).
- **Release:** Version in `app/__init__.py` und `CHANGELOG.md` eintragen, pushen, dann
  **Actions → Release → Run workflow** – baut die SPK und legt das GitHub-Release an.

## Lizenz

[MIT](LICENSE) – frei nutzbar, auch kommerziell. Gefällt dir die App? [Spendier mir einen Kaffee ☕](https://buymeacoffee.com/lapalutschi)

## English

**Zeitmanagement** is a self-hosted package for Synology NAS (DSM 7) for small businesses, medical practices and
individuals: tasks, project time tracking, calendar and shift planning – in its own DSM window with single sign-on
for DSM users. The user interface is German.

- One noarch package for Intel/AMD and ARM, no native libraries; uses the NAS's Python 3.8–3.13.
- Single-person or team mode, DSM login incl. 2FA, company name and logo, four themes, nightly backups to a shared
  folder (for Hyper Backup), updates through Package Center (the app is its own package source), audit log,
  configurable port.
- Tasks (0.2): projects and task lists, due dates, priorities, assignees, one-line quick add
  („Backup prüfen morgen !hoch #büro @anna“), daily reminder email.
- Project time tracking (0.3): stopwatch (also straight from a task), manual entries, “on site” flag, weekly view,
  reports by customer/project/task/employee/day with PDF, Excel and CSV export. Calendar and shift planning come next.

Install: download the `.spk` from [Releases](https://github.com/rinkelzz/zeitmanagement/releases) →
Package Center → Manual Install. Security notes: [SECURITY.md](SECURITY.md). License: MIT.

Like it? [Buy me a coffee ☕](https://buymeacoffee.com/lapalutschi)
