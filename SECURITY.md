# Sicherheit

Bitte Sicherheitslücken **nicht** als öffentliches Issue melden, sondern über
[GitHub → Security → Report a vulnerability](https://github.com/rinkelzz/zeitmanagement/security/advisories/new).
Ich melde mich so schnell wie möglich.

Unterstützt wird jeweils die neueste Version unter [Releases](https://github.com/rinkelzz/zeitmanagement/releases).

## Was die App selbst absichert

- **Passwörter** werden mit PBKDF2-SHA256 (200 000 Runden, eigenes Salz) gespeichert, Sitzungen nur als
  SHA-256-Hash. Beim Passwortwechsel und beim Deaktivieren eines Benutzers enden alle seine Sitzungen.
- **Login-Sperre:** 5 Fehlversuche pro Benutzername und Adresse, 10 pro Benutzername insgesamt – jeweils
  15 Minuten. Die Tabelle ist in der Größe begrenzt, sie lässt sich nicht volllaufen lassen.
- **CSRF:** Änderungen per Login-Cookie werden nur von der eigenen Seite angenommen (`Sec-Fetch-Site`
  bzw. `Origin`/`Referer`). Läuft die App hinter einem Proxy, der den `Host`-Header umschreibt, die
  **Öffentliche Adresse** in den Einstellungen eintragen.
- **DSM-Passwortprüfung** nur gegen die NAS selbst (`127.0.0.1`, `localhost`, `::1` – der Hostname wird
  exakt geprüft), ohne Proxy-Umgebungsvariablen und ohne Weiterleitungen. DSM-Passwörter verlassen die NAS nie.
- **Single Sign-on im DSM-Fenster:** Der Schlüssel (`.proxy_secret` im Paketordner) wird nur von
  Verbindungen über 127.0.0.1 ohne `X-Forwarded-For` angenommen, also nur von `app.cgi` auf der NAS selbst.
- **Echte Absenderadresse** (`X-Forwarded-For`) wird nur übernommen, wenn die Anfrage vom Reverse Proxy
  auf derselben NAS (127.0.0.1) kommt. Bei einem Proxy auf einem anderen Gerät sieht die App dessen Adresse –
  die Login-Sperre greift dann nur noch pro Benutzername.
- **Keine Zugangsdaten in Sicherungen:** Das SMTP-Passwort wird vor dem Sichern entfernt (inklusive
  `VACUUM`, damit es auch nicht in freien Seiten der Datei steht).
- **CSV/Excel-Exporte** entschärfen Zellen, die mit `=`, `+`, `-`, `@`, Tab oder CR beginnen (Formel-Injection).
- **Paketquelle** (`/spk`) nur aus dem lokalen Netz erreichbar.
- **Kalender-Abo:** Der Link enthält einen zufälligen 256-Bit-Schlüssel, gespeichert wird nur sein SHA-256-Hash. Der Link
  gibt nur die Termine und Aufgaben dieser Person heraus, nur lesend. Neu erzeugen oder sperren macht ihn sofort ungültig.

- **Meldeformular (Helpdesk):** Der Link enthält einen zufälligen Schlüssel (72 Bit) und funktioniert nur, solange
  das Modul und das Formular aktiv sind – sonst 404. Höchstens 5 Meldungen pro Absenderadresse und 20 pro Formular in
  15 Minuten, verstecktes Feld gegen Spam-Programme. Anhänge (PNG, JPG, PDF) werden am **Inhalt** erkannt, SVG/HTML
  abgelehnt, Größe schon beim Empfang begrenzt; hochladen lässt sich nur 30 Minuten lang mit dem Schlüssel aus der
  Antwort. Heruntergeladen werden Anhänge nur angemeldet, immer als Anhang mit `X-Content-Type-Options: nosniff` und
  `Content-Security-Policy: sandbox`.
- **Mails aus dem Helpdesk** tragen `Auto-Submitted`, damit Abwesenheitsnotizen keine Mail-Schleifen auslösen.
  Zeilenumbrüche in Betreff und Kopfzeilen werden entfernt (keine Header-Injection).

## Hinweise für den Betrieb

- Die App nur per **HTTPS** (Reverse Proxy von DSM mit Zertifikat) nach außen freigeben, nie den Port
  (Standard 8770) direkt. Bei Zugriff per `http://` aus dem Internet zeigt die App eine Warnung.
- Updates kommen aus den GitHub-Releases dieses Repos. Sie sind nicht zusätzlich signiert – geschützt
  sind sie durch HTTPS und das (per Zwei-Faktor gesicherte) GitHub-Konto des Maintainers.
- Das SMTP-Passwort liegt in der Datenbank der App (`/var/packages/zeitmanagement/var/`), lesbar nur für
  den Paketbenutzer. Am besten ein eigenes Mail-Konto oder App-Passwort nur für den Versand verwenden.
- Den freigegebenen Ordner für Sicherungen nicht für alle Benutzer freigeben – die Sicherungen enthalten
  alle Daten und die Passwort-Hashes.
- Das Meldeformular ist öffentlich. Den Link nur an die Leute geben, die melden sollen, und bei Missbrauch unter
  Verwaltung → Helpdesk „Neuer Link“ oder „Abschalten“ wählen. Im Formular steht ein Hinweis, keine vertraulichen
  Daten (z. B. Patientendaten) einzutragen – er lässt sich anpassen.

# Security (English)

Please report vulnerabilities privately via GitHub's *Report a vulnerability* button, not as a public issue.
