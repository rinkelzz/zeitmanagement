# Änderungen

Alle nennenswerten Änderungen an diesem Projekt. Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

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

[0.2.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.2.0
[0.1.0]: https://github.com/rinkelzz/zeitmanagement/releases/tag/v0.1.0
