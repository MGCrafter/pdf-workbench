# Tests

Die Tests erzeugen ihre PDFs selbst. Sie prüfen vor allem Dinge, bei denen ein
Fehler Dokumente beschädigen oder Änderungen verlieren könnte: Originalschutz,
Exportreihenfolge, Drehung, Wiederöffnen und das Löschen von Anmerkungen.

```sh
python -m pip install '.[test]'
python -m unittest discover -s tests -v
```

Auf Linux ohne grafische Sitzung lässt sich davor `QT_QPA_PLATFORM=offscreen`
setzen. Mit installiertem Poppler prüft `pdftoppm` zusätzlich die Darstellung
von Anmerkungen vor und nach dem Wiederöffnen für alle 16 Kombinationen aus
ursprünglicher und zusätzlicher Seitendrehung.

Die GitHub-Workflows führen die Tests auf Windows und Ubuntu aus. Beim Release
wird anschließend das fertig gebaute Programm selbst gestartet:

```sh
PDFWorkbench --self-test --output test-results
```

Unter Windows heißt die Datei `PDFWorkbench.exe`. Dieser zusätzliche Durchlauf
verwendet keine Entwicklungsabhängigkeiten: Er prüft PDF-Operationen, die echte
Qt-Oberfläche, Scrollereignisse sowie Export, Wiederöffnen, Bearbeiten und
Löschen von Anmerkungen. Das Ergebnis und ein Screenshot landen im angegebenen
Ordner. Ein fehlgeschlagener Test verhindert die Veröffentlichung.

Die [GitHub-Actions-Läufe](https://github.com/MGCrafter/pdf-workbench/actions)
zeigen den Stand für jeden Commit und jedes Release. macOS gehört noch nicht
zur Testmatrix; ein Windows- oder Linux-Ergebnis ist kein macOS-Nachweis.

## Neue Werkzeuge und OCR

Die Objekt-Tests vergleichen Vorschau und echten PDF-Export, prüfen alle
Rotationskombinationen mit versetztem Beschnitt, Unicode, Transparenz,
Schriftressourcen und Wiederöffnen. Echte Qt-Eingaben prüfen Verschieben,
Größenänderung, einen Undo-Schritt pro Geste, Esc und den Tastaturfokus.
Suche und Bildimport haben zusätzliche Prüfungen für veraltete Suchergebnisse,
Thread-Ende, unveränderte Exportauswahl, Originalschutz und sehr kleine Bilder.

Die Release-Abnahme verlangt ab 0.4 die fünf benannten Theme-Prüfungen und ab
0.5 zusätzlich Objekte, eingebettete Schrift, Suche, Bildimport und echten
OCR-Export. Berichte müssen zur tatsächlichen ausführbaren Datei, Version und
zum Quellstand einschließlich Schrift- und Lizenzdaten passen.

Für die optionalen echten OCR-Tests muss Tesseract mit Englisch und `pdf.ttf`
installiert sein. Ohne diese Voraussetzungen werden sie ausdrücklich
übersprungen; Tests laden selbst keine Sprachpakete herunter. Ein vorbereiteter
Cache lässt sich mit `PDF_WORKBENCH_OCR_TEST_TESSDATA` angeben. Die nativen
Release-Builder bereiten eine Engine vor und setzen
`PDF_WORKBENCH_REQUIRE_OCR=1`, sodass fehlendes OCR dort fehlschlägt. Unter Linux
prüft zusätzlich Poppler, dass die neue unsichtbare Textebene die sichtbare
Seite nicht verändert. Die Test-Engine wird nicht ins App-Paket aufgenommen.
