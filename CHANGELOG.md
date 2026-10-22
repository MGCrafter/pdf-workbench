# Änderungen

## 0.9.0

- Getrennte Dokument-Tabs mit eigener Projektidentität, Undo-Historie,
  geschützten automatischen Sicherungen und reversibler Seitenübertragung.
- Stapelverarbeitung mit benannten/importierbaren Vorlagen für Komprimierung,
  Drehung, Wasserzeichen, Seitennummern, Scan-Aufbereitung und lokale OCR.
- Scans begradigen, Hintergrund bereinigen und Ausrichtung erkennen;
  leere/doppelte Seiten vor einer manuellen Löschung prüfen.
- Getrennte Vorder-/Rückseitenfolgen verschachteln, PNG/JPEG und UTF-8-Text
  exportieren, klickbare Inhaltsverzeichnisse und interne Linkbereiche erzeugen.
- Dauerhafte Schwärzung als vollständig neu aufgebaute Bild-PDF ohne
  ursprüngliche Textschicht, Metadaten, Anhänge oder PDF-Revisionen.
- Bestehende unterstützte PDF-Textabschnitte isoliert und rückgängig ersetzbar.
- Eigene PFX/P12-Zertifikate zum kryptografischen Signieren neuer Exporte;
  Offline-Prüfbericht trennt Integrität, spätere Änderungen und Zertifikatsvertrauen.
- Lokale PDF/A-2b-Konvertierung mit Ghostscript; Speicherung erst nach echter
  veraPDF-Validierung der tatsächlich ausgegebenen Daten.
- Neue native Dialoge mit hellem/dunklem Theme, Tastaturbedienung und
  sichtbaren Grenzen für Bildumwandlung, einfache Textkorrektur und Signaturen.
- Original- und Fremddateischutz bei Stapelabbruch, stimmige Linkkoordinaten
  bei Rotation/Beschnitt und getrennte Speicherentscheidungen vor einem Neustart.
- Native Release-Abnahme und Quellnachweise umfassen die neuen Laufzeitkomponenten
  und tatsächliche PDF/A-Konvertierung; macOS bleibt ungetestet.

## 0.8.0

- Verschachtelte Lesezeichen importieren, gestalten und mit stabilen Arbeitsseitenzielen
  exportieren; umsortieren, kopieren und entfernen mit gemeinsamem Undo.
- Seiten mit echter Vorschau und ziehbarem Rahmen zuschneiden oder proportional
  auf A4, Letter und eigene Formate setzen; Millimeterwerte und Tastaturbedienung.
- `.pdfwb`-Projekte mit eingebetteten Originaldaten, editierbaren Objekten,
  Lesezeichen, Auswahl, Zoom und vollständiger Undo-/Redo-Historie.
- Automatische lokale Sicherungen mit zwei Generationen und Wiederherstellungsdialog.
  Entsperrte PDF-Quellen verlangen ein geschütztes Projekt und geschützte Sicherungen.
- Integrierter Updater für portable Windows-/Linux-Pakete: tägliche Versionsprüfung,
  expliziter Download/Neustart, SHA-256- und Dateinachweise, Sicherung und Rücknahme
  bei fehlgeschlagenem Programmstart. Erste Einrichtung durch manuelles Entpacken von 0.8.

## 0.7.0

- Zwei PDF-Versionen lokal vergleichen: Textänderungen, sichtbare Unterschiede,
  zugeordnete Seiten und gekoppelte Vorschauen mit gemeinsamer Navigation.
- Interaktive Formulare visuell gestalten: Textfelder, Kontrollkästchen,
  Dropdowns und Optionsgruppen mit Position, Größe, Feldreihenfolge und Eigenschaften.
- Bestehende Optionsgruppen ausfüllen und bei Seitenauswahl korrekt exportieren.
- Wasserzeichen und Seitennummern mit echter PDF-Vorschau, Seitenbereich,
  eigener Vorlage, Farbe, Transparenz und Drehung ergänzen.
- Änderungen an Formularen und Seitenmarkierungen als gemeinsame Undo-Schritte;
  Originaldateien und andere Kopien bleiben geschützt.
- Native Release-Abnahme auf 18 PDF-Prüfungen und zusätzliche GUI-Nachweise
  für Vergleich, Formulargestaltung und Seitenmarkierungen erweitert.

## 0.6.0

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
- Release-Prüfungen verlangen die neuen Werkzeuge auch innerhalb der fertigen
  Windows-/Linux-App. Bildbibliotheken und Verschlüsselung haben eigene
  versionsgebundene Lizenz- und Quellnachweise für beide Plattformen.

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
