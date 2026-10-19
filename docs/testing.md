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
