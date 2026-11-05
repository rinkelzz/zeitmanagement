# Änderungen

Alle nennenswerten Änderungen an diesem Projekt. Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [0.6.0] – 2026-11-05

### Neu
- **Dienstplan** als neuer Reiter: Wochenraster Mitarbeiter × Montag–Sonntag mit Stundensumme pro Person und
  **Besetzung** pro Tag. Der Chef trägt per Klick eine Schichtart ein oder eigene Zeiten mit Pause und Notiz;
  geteilte Dienste sind möglich, Überschneidungen (auch über Mitternacht) werden abgelehnt.
- **Schichtarten** unter Verwaltung → Dienstplan: Frühdienst, Spätdienst, Nachtdienst, Urlaub, Krank und Frei sind
  voreingestellt; Name, Kürzel, Zeiten, Pause und Farbe änderbar, eigene anlegen, archivieren. Geplante Wochen
  behalten ihre Werte, auch wenn eine Schichtart später geändert wird.
- **Vorwoche übernehmen** kopiert alle Schichten der letzten Woche.
- **Veröffentlichen:** Entwürfe sehen nur der Chef; nach dem Veröffentlichen das ganze Team. Auf Wunsch bekommt jede
  Person ihre Schichten der Woche per Mail. „Zurücknehmen“ blendet die Woche wieder aus.
- **Wochenplan als PDF** im Querformat mit Firmenname, Logo, Farben, Stunden, Besetzung und Legende.
- Eigene veröffentlichte **Schichten im Kalender** und im **Kalender-Abo** (iCal).
- Festlegen, **wer im Dienstplan steht** (z. B. den Chef ausblenden); am Handy zusätzlich „Meine Woche“.
- Änderungsprotokoll mit dem neuen Bereich „Dienstplan“.

### Geändert
- Die Kopfzeile bleibt mit sechs Reitern und laufender Stoppuhr auch auf kleineren Bildschirmen einzeilig.

### Technik
- Datenbank-Schema Version 6 (Schichtarten, Schichten, veröffentlichte Wochen, „im Dienstplan“ je Benutzer).
- PDF-Erzeugung kann jetzt auch Querformat.

## [0.5.0] – 2026-11-05

### Neu
- **Kalender** mit Monats-, Wochen- und Tagesansicht (am Handy startet er in der Tagesansicht). Termine per Klick ins
  Raster anlegen, mit Uhrzeit oder ganztägig, mehrtägig, mit Ort, Projekt (Farbe) und Notiz; überlappende Termine
  stehen nebeneinander, eine rote Linie zeigt die aktuelle Uhrzeit.
- **Serientermine:** täglich, werktags, wöchentlich, alle 2 Wochen, monatlich, alle 3 Monate, jährlich – optional mit
  Enddatum. Einzelne Termine einer Serie löschen („Nur diesen Termin“) oder die ganze Serie. Uhrzeiten bleiben über
  die Sommer-/Winterzeit hinweg gleich.
- **Aufgaben im Kalender:** fällige Aufgaben erscheinen gestrichelt (mit Uhrzeit im Raster), abschaltbar.
- **Teilnehmer (Team):** Termine sind für die anlegende Person, die Teilnehmer und den Chef sichtbar.
- **Kalender-Abo (iCal) fürs Handy:** persönlicher geheimer Link unter „Mein Zugang“ – für iPhone, Outlook und Google
  Kalender. Mit Serien (RRULE), ausgefallenen Terminen, Zeitzone, Ort, Notiz und **Erinnerung** („15 Minuten vorher“),
  auf Wunsch mit fälligen Aufgaben. „Neuen Link erzeugen“ macht den alten sofort ungültig, „Link sperren“ beendet das Abo.
- Änderungsprotokoll mit dem neuen Bereich „Kalender“.

### Technik
- Datenbank-Schema Version 5 (Termine, Teilnehmer, Ausnahmen, Abo-Schlüssel – nur als Hash gespeichert).

## [0.4.0] – 2026-11-05

### Neu
- **Wiederkehrende Aufgaben:** täglich, werktags, wöchentlich, alle 2 Wochen, monatlich, alle 3 Monate, jährlich – im
  Bearbeiten-Feld „Wiederholung“ oder per Schnelleingabe („jeden montag“, „monatlich“, „alle 2 wochen“). Beim Erledigen
  entsteht die nächste Aufgabe mit Priorität, Liste, Zuständigen und Checkliste; der nächste Termin liegt immer nach heute.
- **Checkliste (Unteraufgaben)** in jeder Aufgabe – abhaken, hinzufügen, entfernen; Fortschritt „☑ 1/3“ in der Liste.
- **Erinnerung zu einer Uhrzeit:** Aufgaben mit Uhrzeit („um 14:30“) schicken zu dieser Zeit eine Mail an die Zuständigen.
  Der Zeitplan prüft dafür jetzt jede Minute.
- **Projektbudget** in Stunden mit Verbrauchsbalken im Projekt und in der Auswertung; Warn-Mail an den Chef bei 80 % und 100 %.
- **„Abgerechnet“-Kennzeichen:** In der Auswertung markiert der Chef alle offenen Zeiten der Auswahl oder einzelne
  Buchungen als abgerechnet (mit Vermerk, z. B. Rechnungsnummer) – und kann es zurücknehmen. Abgerechnete Zeiten sind
  gegen Ändern und Löschen gesperrt. Neuer Filter „Abrechnung“ (alle / nicht abgerechnet / abgerechnet), Kennzahlen
  „davon offen“ und „abgerechnet“, Spalten „Abgerechnet“ und „Vermerk“ in Excel und CSV.

### Technik
- Datenbank-Schema Version 4 (Wiederholung, Uhrzeit, Checkliste, Budget, Abrechnung).

## [0.3.0] – 2026-11-05

### Neu
- **Projektzeiten – Stoppuhr:** Projekt (und optional Aufgabe) wählen, Start drücken. Pro Person läuft höchstens eine Uhr;
  wer ein anderes Projekt wählt oder startet, beendet die laufende automatisch. Notiz, Aufgabe und „vor Ort“ lassen sich
  ändern, während die Uhr läuft, der Beginn lässt sich korrigieren. Eine vergessene Uhr wird beim Stoppen auf 24 Stunden gekürzt.
- **▶ an jeder Aufgabe** in einem Projekt startet die Stoppuhr direkt für diese Aufgabe, ■ stoppt sie.
- **Laufende Stoppuhr in der Kopfzeile** – auf jeder Seite sichtbar und mit einem Klick zu stoppen.
- **Nachtragen** mit Datum, von/bis oder Dauer (1:30 oder 1,5), auch über Mitternacht. Der Chef kann für andere nachtragen.
- **Kennzeichen „vor Ort“** für Termine beim Kunden.
- **Wochenansicht** mit Tagessummen, Wochensumme und Bearbeiten/Löschen jeder Buchung; der Chef kann die Woche jeder Person ansehen.
- **Auswertung:** Zeitraum (Woche, Monat, Jahr, frei), Kunde, Projekt, Mitarbeiter; gruppiert nach Kunde → Projekt, Projekt,
  Projekt → Aufgabe, Mitarbeiter → Projekt oder Tag; Summen mit Anteil oder Einzelbuchungen.
- **Export als PDF, Excel und CSV** – mit Firmenname und Logo, PDF mehrseitig, Excel mit Übersicht und Buchungen, alles gegen
  Formel-Injection abgesichert. Exporte stehen im Änderungsprotokoll.
- Buchungen derselben Person dürfen sich nicht überschneiden. Projekte mit gebuchten Zeiten lassen sich nur archivieren, nicht löschen.

### Rechte
- Mitarbeiter buchen nur auf Projekte, in denen sie Mitglied sind, und sehen in Woche und Auswertung nur ihre eigenen Zeiten.
  Der Chef sieht und ändert alle.

### Technik
- Datenbank-Schema Version 3 (Zeitbuchungen). openpyxl (reines Python) für den Excel-Export im Paket.

## [0.2.0] – 2026-11-04

### Neu
- **Aufgaben** mit Fälligkeit, Priorität (hoch, mittel, normal), Notiz und – im Team-Modus – Zuständigen.
  Ansichten: Heute, Überfällig, Demnächst, Meine Aufgaben, Alle offenen, Ohne Liste, Erledigt. Offene Aufgaben
  sind nach „Überfällig / Heute / Demnächst / Ohne Datum“ gruppiert. Abhaken per Klick, Bearbeiten direkt in der Liste.
- **Projekte und Aufgabenlisten:** Projekte gehören optional zu einem Kunden (später das Ziel für Zeitbuchungen),
  Aufgabenlisten sind einfache Sammlungen. Mit Farbe, Mitgliedern, Archivieren und Löschen (nur wenn leer).
  Kunden lassen sich direkt beim Anlegen eines Projekts neu anlegen.
- **Rechte im Team:** Der Chef sieht und darf alles. Mitarbeiter sehen nur ihre Projekte und Listen, ihnen zugewiesene
  Aufgaben und eigene ohne Liste; Listen dürfen sie selbst anlegen, Projekte nicht.
- **Schnelleingabe:** „Backup prüfen morgen !hoch #büro @anna“ – Datum, Priorität, Liste und Zuständige in einer Zeile.
  Was erkannt wurde, zeigt die App schon beim Tippen; Unbekanntes bleibt im Titel.
- **Tägliche Erinnerungs-Mail** mit überfälligen und heute fälligen Aufgaben: Uhrzeit unter Verwaltung → Mail,
  pro Person unter „Mein Zugang“ abschaltbar und sofort testbar. Höchstens eine Mail am Tag, bei Fehlern bis zu
  drei Versuche.
- **Handy:** Ansichten und Listen über ein Auswahlfeld statt der Seitenleiste.
- Änderungsprotokoll mit den neuen Bereichen „Aufgaben“ und „Projekte & Kunden“.

### Technik
- Datenbank-Schema Version 2 (Kunden, Projekte, Mitglieder, Aufgaben, Zuständige, Erinnerungsprotokoll) –
  bestehende Datenbanken werden beim Update automatisch erweitert.

## [0.1.0] – 2026-11-04

Erste Version: das Fundament. Aufgaben (0.2) und Projektzeiten (0.3) folgen.

### Neu
- **Synology-Paket für DSM 7** (noarch – ein Paket für Intel/AMD und ARM), läuft mit dem Python der NAS (3.8 bis 3.13).
- **Installationsassistent:** Betriebsart (nur ich / Team), Admin-Passwort, Firmenname und Port.
- **Betriebsart Einzelperson oder Team**, später umschaltbar. Im Modus Einzelperson gibt es genau ein Konto –
  jeder DSM-Administrator landet darin, Benutzer und Rollen sind ausgeblendet.
- **Eigenes DSM-Fenster mit Single Sign-on** und Anmeldung mit DSM-Konto (inklusive Zwei-Faktor-Code) oder eigenem Konto.
- **Benutzerverwaltung** im Team-Modus: Chef/Admin und Mitarbeiter, anlegen, bearbeiten, deaktivieren;
  DSM-Benutzer werden auf Wunsch beim ersten Öffnen automatisch angelegt.
- **Firmenname und Logo** in Kopfzeile und Anmeldung, **vier Designs** (Klassisch, Synology DSM, Dunkel, Warm & groß),
  pro Person wählbar.
- **Mailserver (SMTP)** mit Testmail – für die Erinnerungen ab Version 0.2.
- **Port änderbar** unter Verwaltung → System: Die App prüft den Port, speichert ihn und startet sich selbst neu.
- **Nächtliche Datensicherung** in den Paketordner und einen freigegebenen Ordner, Wiederherstellen, Herunterladen
  und Löschen per Klick.
- **Updates übers Paket-Zentrum** – die App ist ihre eigene Paketquelle.
- **Änderungsprotokoll** mit Filtern und CSV-Export.
- **Datenbank-Migrationen** über `PRAGMA user_version`, schrittweise und in Transaktionen.

### Sicherheit
- Passwörter mit PBKDF2, Sitzungen als Cookie bzw. Header mit CSRF-Schutz, Login-Sperre pro Adresse und pro Benutzername.
- DSM-Passwortprüfung nur gegen die NAS selbst, ohne Proxy-Variablen und ohne Weiterleitungen.
- Echte Client-IP hinter dem DSM-Reverse-Proxy nur von 127.0.0.1, kein SMTP-Passwort in Sicherungen,
  CSV-Export gegen Formel-Injection geschützt. Details in [SECURITY.md](SECURITY.md).

[0.6.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.6.0
[0.5.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.5.0
[0.4.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.4.0
[0.3.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.3.0
[0.2.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.2.0
[0.1.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.1.0
