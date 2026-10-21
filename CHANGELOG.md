# Änderungen

## 0.6.0 (lokaler Entwicklungsstand, noch nicht veröffentlicht)

- Neue Startansicht, ruhigere helle und dunkle Oberflächen, gruppierte Werkzeuge
  und kurze Animationen. Unter Ansicht lassen sich Animationen abschalten;
  erkannte Systemeinstellungen für reduzierte Bewegung werden berücksichtigt.
- PDF-Komprimierung mit verlustfreier Optimierung und zwei Bildqualitätsstufen,
  Größenvergleich vor dem Speichern und tatsächlicher Größe nach dem Export.
- Passwortgeschützte PDFs öffnen und neue Dateien mit AES-256 schützen.
  Der normale Export bleibt ausdrücklich unverschlüsselt.
- Normale AcroForms mit Textfeldern, Kontrollkästchen und Einfachauswahl
  ausfüllen. Undo, Seitensortierung, Duplikate und erneutes Öffnen sind geprüft.
  XFA, Signaturfelder, Skripte und weitere Spezialfälle werden abgewiesen.

## 0.5.3

- Die Texterkennung gibt temporäre PDF-Dateien vor dem Aufräumen vollständig
  frei. Das verhindert Dateisperren beim OCR-Export unter Windows.
- Vorübergehende Serverfehler beim Erstellen der Release-Pakete werden begrenzt
  erneut versucht. Versions-, Größen- und Prüfsummenkontrollen bleiben verbindlich.

## 0.5.2

- Windows-Sprachlisten mit CRLF-Zeilenenden werden korrekt gelesen.
- Release-Prüfungen verlangen OCR schon beim Test des Quellcodes und geben die
  erkannte Engine, Sprachen und Schriftdatei an.
- Kleine Abnahme- und Buildberichte liegen neben den fertigen Downloads.

## 0.5.1

- Tesseract-Versionen mit `v` vor der Versionsnummer werden unter Windows erkannt.
- Plattformtests berücksichtigen Dateisperren und Windows-Pfadaliase.
- Die Abnahme der Ubuntu-Bibliotheksquellen fragt unabhängige Metadaten parallel
  ab und zeigt den Fortschritt; dieselben Versions- und Prüfsummenkontrollen gelten weiter.

## 0.5.0

Eigene Inhalte ergänzen und direkt auf der Seite bearbeiten:

- Textfelder mit mehreren Zeilen, Schriftgröße, Farbe und Ausrichtung.
- PNG-/JPEG-Bilder, Bildstempel, Häkchen und Kreuze.
- Auswahlwerkzeug mit Verschieben, Eckgriffen, Duplizieren und Löschen.
  Esc verwirft den Entwurf; eine fertige Bewegung ist ein Undo-Schritt.
- Strg + F durchsucht PDFs und eigene Textfelder im Hintergrund.
- Bilder als neue A4-, Letter- oder Bildformat-Seiten übernehmen.
- Optionale lokale Texterkennung mit Tesseract: Eine zusätzliche PDF erhält
  eine unsichtbare Textschicht. Die vorhandenen Seiteninhalte bleiben erhalten.

Die Textschrift ist im Programm und im PDF enthalten. Eigene Objekte bleiben
nach dem Speichern und erneuten Öffnen bearbeitbar. OCR braucht eine separate
Tesseract-Installation; fehlende deutsche und englische Sprachpakete können
im Dialog ausdrücklich heruntergeladen werden.

## 0.4.0

Die Oberfläche gibt es jetzt auch in Dunkel. Unter **Ansicht → Darstellung**
stehen System, Hell und Dunkel zur Wahl. Die App merkt sich die Einstellung;
bei System folgt sie einem Wechsel des Betriebssystems auch im offenen Fenster.

Dialoge, Miniaturen, Symbole und Statusanzeigen passen sich an. Die PDF-Seite,
Markierungen und Unterschriften behalten ihre Farben. Zoom, Scrollposition und
ungespeicherte Änderungen bleiben beim Umschalten erhalten.

## 0.3.0

Die erste Version zum Herunterladen für Windows und Linux.

- PDFs zusammenfügen und Seiten gemeinsam oder einzeln exportieren.
- Seiten sortieren, drehen, duplizieren und entfernen.
- Kommentare, Textmarkierungen und gezeichnete Unterschriften hinzufügen.
- Eigene Anmerkungen nach dem Speichern wieder öffnen, bearbeiten und löschen.
- Miniaturen als Raster oder Liste, Vorschau mit Zoom und Seitenbreitenansicht.
- Seitliches Mausrad, Touchpad und Umschalt+Mausrad zum horizontalen Scrollen;
  die mittlere Maustaste verschiebt die Ansicht in jedem Werkzeug.

Originaldateien bleiben unverändert. Bestehende Ausgabedateien werden nicht
überschrieben. Formulare, verschlüsselte PDFs und Zertifikatssignaturen werden
derzeit abgewiesen. macOS hat noch keinen geprüften Download.
