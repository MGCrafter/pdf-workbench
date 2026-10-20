# Scans durchsuchbar machen

Unter **Datei → Durchsuchbare PDF erstellen (OCR) …** erkennt PDF Workbench
Text auf gescannten Seiten. Das Ergebnis ist eine zusätzliche PDF mit einer
unsichtbaren Textschicht. Die vorhandenen Seiteninhalte und eigenen Anmerkungen
bleiben erhalten. Es wird kein Dokument hochgeladen.

## Einmal einrichten

Die fertige App enthält Tesseract nicht. Benötigt wird eine lokale Installation
von Tesseract 4.1 oder neuer einschließlich `pdf.ttf`.

- **Windows:** Die [offizielle Tesseract-Anleitung](https://tesseract-ocr.github.io/tessdoc/Installation.html)
  verlinkt die Windows-Installer von UB Mannheim. Die App sucht im Suchpfad und
  im üblichen Ordner `Programme\Tesseract-OCR`. Über **Wählen …** kannst du
  `tesseract.exe` selbst auswählen; die Auswahl wird gespeichert.
- **Ubuntu/Debian:** `sudo apt install tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng`
  installiert Engine und Sprachpakete. Andere Distributionen haben entsprechende
  Tesseract-Pakete.

Der Dialog prüft die Installation im Hintergrund. Falls Deutsch oder Englisch
fehlen, kannst du die ausgewählten Sprachpakete ausdrücklich herunterladen:
Deutsch etwa 1,5 MB, Englisch etwa 4,1 MB. Die Dateien kommen aus einem festgelegten
Stand des offiziellen `tessdata_fast`-Projekts und werden anhand ihrer Prüfsummen
geprüft. Ein Abbruch hinterlässt kein halbfertiges Sprachpaket. Bereits fertige
Pakete bleiben im lokalen Cache, zusammen mit Lizenz und Herkunft.

## Benutzen

1. PDFs öffnen oder Bilder als PDF-Seiten importieren.
2. **Durchsuchbare PDF erstellen (OCR) …** öffnen und alle oder ausgewählte
   Seiten sowie die passenden Sprachen wählen.
3. Einen neuen PDF-Dateinamen angeben und **Durchsuchbare PDF erstellen** drücken.
4. Die gespeicherte PDF öffnen. Jetzt funktionieren Suche, Textauswahl und
   Textmarkierungen auch auf den erkannten Scanseiten.

Die Erkennung läuft lokal. Nach der Einrichtung wird dafür kein Internet
benötigt. Seiten mit bereits vorhandener Textschicht werden übersprungen.
Die Seiten im Arbeitsbereich bleiben unverändert; das Ergebnis wird separat
exportiert. Vorhandene Ausgabedateien werden nicht überschrieben. Während des
Exports kannst du über **Abbrechen** stoppen.

Erkannter Text kann Fehler enthalten, insbesondere bei Handschrift, schiefen
oder unscharfen Scans. Die Erkennung ersetzt keine Sichtprüfung. Die App fügt
Text hinzu; sie korrigiert oder bearbeitet vorhandenen PDF-Text nicht.
