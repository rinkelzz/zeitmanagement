# Änderungen

Alle nennenswerten Änderungen an diesem Projekt. Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [0.8.0] – 2026-11-05

### Neu
- **Meldungen per E-Mail-Postfach (IMAP):** Die App ruft ein Postfach (z. B. `support@…`) alle 1–60 Minuten ab
  (Standard 2) und macht aus neuen Mails Tickets – mit Eingangsbestätigung und Hinweis an den Bearbeiter wie beim
  Formular. Einstellungen, „Verbindung testen“, „Jetzt abrufen“ und Stand des letzten Abrufs unter Verwaltung → Helpdesk.
- **Zuordnung zum Projekt:** zuerst ein Melder, der schon einmal gemeldet hat, dann die neuen **Absender-Domains pro
  Projekt** (z. B. `physio-henze.de`; bei Freemail wie gmx.de die einzelne Adresse).
- **Posteingang „Unzugeordnet“** (nur Chef) in der Ticket-Seitenleiste: Mails ohne passendes Projekt mit Text, Anhängen
  und Grund. Projekt wählen → „Ticket anlegen“, auf Wunsch wird die Domain dabei fürs nächste Mal gemerkt; oder „Verwerfen“.
- **Antworten per Mail landen im Ticket:** über `[#T-2026-0042]` im Betreff oder die Antwort auf unsere letzte Mail
  (In-Reply-To). Der zitierte alte Text wird abgeschnitten. Ein wartendes oder erledigtes Ticket ist danach wieder
  „In Arbeit“, der Bearbeiter bekommt einen Hinweis. Anhängen dürfen nur der Melder, seine Domain oder die
  Absender-Domains des Projekts – alle anderen landen im Posteingang.
- Verarbeitete Mails wandern in den Ordner **„Zeitmanagement/Erledigt“** (wird angelegt; Trennzeichen des Servers
  wird beachtet, ohne MOVE-Unterstützung per Kopieren + Löschen). Jede Mail wird nur einmal verarbeitet.
- Anhänge aus Mails wie beim Formular: PNG, JPG, PDF bis 10 MB, höchstens 5 – was fehlt, steht im Verlauf.
- Der Zähler am Reiter „Tickets“ zählt jetzt auch unzugeordnete Mails mit.

### Sicherheit
- Abwesenheitsnotizen, Rückläufer, Newsletter, Zustellberichte und Mails von der eigenen Adresse werden ignoriert
  (keine Mail-Schleifen). Höchstens 10 neue Tickets pro Absender und Stunde, der Rest geht in den Posteingang.
- Mails über 25 MB werden nicht geladen; sie bleiben im Postfach und erscheinen als Hinweis.
- HTML-Mails werden in Text umgewandelt (ohne Skripte, Styles, Bilder). Nur verschlüsselte Verbindungen (SSL oder STARTTLS).
- Das Postfach-Passwort wird wie das SMTP-Passwort **nicht** in Sicherungen gespeichert.

### Technik
- Datenbank-Schema Version 8 (Posteingang, verarbeitete Mails, Absender-Domains, Message-ID der letzten Mail je Ticket).
- Nur Standardbibliothek (`imaplib`, `email`) – keine neue Abhängigkeit.

## [0.7.0] – 2026-11-05

### Neu
- **Helpdesk** als neuer Reiter „Tickets“: Mitarbeiter deiner Kunden (oder im eigenen Betrieb) melden Probleme über ein
  **öffentliches Meldeformular**, du arbeitest sie als Tickets ab. Ticketnummern wie `T-2026-0042`, die Zählung beginnt
  jedes Jahr neu.
- **Meldeformular pro Projekt** mit eigenem, geheimem Link (Verwaltung → Helpdesk): Name, E-Mail, Telefon, Betreff,
  Beschreibung, Dringlichkeit („Arbeit steht still“) und bis zu 5 **Anhänge** (PNG, JPG, PDF, je 10 MB). Hinweis
  über dem Absenden-Knopf (z. B. „Bitte keine Patientendaten“) und optional Link zu Datenschutzhinweisen.
  „Neuer Link“ macht den alten ungültig, „Abschalten“ legt das Formular still.
- **Ticketliste** mit Ansichten Neu, Mir zugewiesen, Alle offenen, Wartet auf Rückmeldung, Erledigt und nach Projekt
  (gruppiert nach Kunde), Suche nach Nummer, Titel oder Melder, Zähler für neue Tickets am Reiter.
- **Ticketansicht** mit Verlauf: Meldung, **Antworten an den Melder per Mail** (mit Anhängen), **interne Notizen**,
  Statuswechsel und Mails als Einträge. Status, Priorität, Bearbeiter und Projekt direkt änderbar, „Senden + Erledigt“
  in einem Schritt. Tickets lassen sich auch von Hand anlegen (z. B. nach einem Anruf).
- **Zeit pro Ticket:** Stoppuhr direkt am Ticket (in Liste und Ansicht) – gebucht wird auf das Projekt des Tickets, mit
  „vor Ort“. Ein neues Ticket ist beim Start automatisch „in Arbeit“ und gehört dir. Die Ticketnummer steht in der
  Kopfzeile, unter Zeiten und in der Buchung; nachgetragene Zeiten können ebenfalls ein Ticket haben.
- **Mails:** Eingangsbestätigung mit Ticketnummer, Antworten, „erledigt“ – einzeln abschaltbar. Neue Meldungen gehen
  als Hinweis-Mail an den Bearbeiter des Formulars (sonst an den Chef), dringende mit „DRINGEND“ im Betreff. Alle
  Mails tragen die Kennung `[#T-…]` im Betreff und `Auto-Submitted`, damit Abwesenheitsnotizen keine Schleifen bauen.
  Schlägt eine Mail fehl, steht das im Verlauf – das Ticket bleibt gespeichert.
- **Aufbewahrung:** Erledigte Tickets werden nach einer einstellbaren Frist (3–36 Monate, Standard 12, oder nie) samt
  Verlauf und Anhängen gelöscht. Gebuchte Zeiten bleiben, die Ticketnummer wandert in ihre Notiz.
- **Module an- und abschaltbar** (Verwaltung → Module): Aufgaben, Projektzeiten, Kalender, Dienstplan und Helpdesk.
  Ein abgeschaltetes Modul verschwindet aus Menü, Kalender, Abo und Mails, die Schnittstelle antwortet mit 403 – die
  Daten bleiben erhalten. So lässt sich die App z. B. nur als Helpdesk oder nur als Dienstplan nutzen.
- Änderungsprotokoll mit dem neuen Bereich „Helpdesk“.

### Geändert
- Der Helpdesk ist nach dem Update **abgeschaltet** und muss unter Verwaltung → Module eingeschaltet werden – ein
  Update schaltet also kein öffentliches Formular frei.
- Sieben Reiter: Abstände in der Kopfzeile etwas enger, der Stoppuhr-Text verschwindet auf mittelgroßen Bildschirmen.

### Sicherheit
- Meldeformular: höchstens 5 Meldungen pro Absenderadresse und 20 pro Formular in 15 Minuten, verstecktes Feld gegen
  Spam-Programme, Link wird bei abgeschaltetem Modul oder Formular zu 404.
- Anhänge: Typ wird am **Inhalt** geprüft (SVG/HTML abgelehnt), Dateiname bereinigt, Größe schon beim Empfang begrenzt.
  Hochladen nur 30 Minuten nach dem Absenden mit dem Schlüssel aus der Antwort. Download nur angemeldet, immer als
  Anhang mit `nosniff` und `Content-Security-Policy: sandbox`.
- Mitarbeiter sehen nur Tickets ihrer Projekte, Löschen und Helpdesk-Verwaltung nur der Chef.

### Technik
- Datenbank-Schema Version 7 (Tickets, Verlauf, Anhänge, Formulare; `ticket_id` an Zeitbuchungen). Anhänge liegen in
  der Datenbank und sind damit in jeder Sicherung enthalten.
- DSM-Fenster: Anfragen bis 11 MB (für Anhänge).

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

[0.8.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.8.0
[0.7.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.7.0
[0.6.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.6.0
[0.5.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.5.0
[0.4.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.4.0
[0.3.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.3.0
[0.2.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.2.0
[0.1.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.1.0
