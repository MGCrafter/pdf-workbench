# Windows-Abnahme des Stands 0.7.0

Am 21.10.2026 wurde Commit `be75a71` in der vorhandenen eigenen Windows-VM
getestet: **Windows 11 Pro x64, 10.0.26200**, Python 3.14.8,
PyQt 6.11.0 / Qt 6.11.2, natives Qt-Plug-in **`windows`**.
Alle Python-/Qt-/Build-Abhängigkeiten kamen aus vorhandenen Caches;
es wurde kein neuer großer Download gestartet. Zugangsdaten wurden nicht ausgegeben.

| Prüfung | Ergebnis |
| --- | --- |
| Gesamte native Windows-Suite | 367 gemeldete Tests, 0 Fehler, 14 explizite Skips; 53,43 Sekunden |
| Selbsttest aus dem Quellstand | 18 PDF-Prüfungen ohne Skip und vollständige native GUI-Abnahme bestanden |
| Lokal gebaute `PDFWorkbench.exe` | 18 PDF-Prüfungen ohne Skip und vollständige native GUI-Abnahme bestanden; Prozess-Endcode 0 |
| Neue Funktionen in der EXE | PDF-Text-/Bildvergleich, Formularentwurf mit Radiofeldern, Wasserzeichen, Seitennummern, Vorschau, Export/Wiederöffnen und Rückgängig ausgeführt |
| Sichtbare native Fenster | Hell/Dunkel: Arbeitsbereich, Vergleich und Formulardesigner bei 800 × 560; Markierungsdialoge bei 760 × 480; Schaltflächen vollständig im Fenster |
| Vergleich | 200 % Zoom, Änderungsnavigation und synchronisierte Scrollpositionen geprüft |
| Formulare und Markierungen | Echte sichtbare Formularwerte in der Vorschau; nur ausgewählte Seite markiert; seitenübergreifende Radioauswahl bleibt erhalten; Original-SHA unverändert |
| Reduzierte Bewegung | Im nativen Fenster eingeschaltet und wirksam; die bestehenden Bewegungs-/Einstellungstests bestanden ebenfalls |
| Zweiter Renderer | Exporte aus Windows-Quellstand und Windows-EXE mit Poppler auf dem Host geprüft: Reihenfolge, 90°-Drehung, Nummern und nur das ausgewählte Wasserzeichen korrekt |
| Quellenbezug und Bundle | Alle 41 App-/Font-Dateihashes des eingebetteten Nachweises entsprechen dem Repository; neue Funktionsmodule tatsächlich im Python-Archiv enthalten |

Die 14 Skips betreffen elf Poppler-Fälle ohne Poppler in der VM, zwei Fälle
ohne Windows-Symlinkprivileg und die optionale echte Tesseract-Testklasse.
Poppler prüfte zusätzlich die tatsächlich unter Windows erzeugten Dateien auf
dem Host. Eine neue native Windows-OCR-Abnahme wird nicht behauptet.

Die Poppler-Prüfung bestätigt die Seitenfolge `A2, A1, A3, B1, B2` und den
gedrehten Orientierungsmarker von `A1`. Die neuen Nummern `V07-1/2` und
`V07-2/2` erscheinen ausschließlich im unteren Seitenbereich. Beim Wasserzeichen
unterscheiden sich nur die Pixel der ausgewählten Duplikatseite; die beiden
unmarkierten Seiten bleiben pixelgleich zu ihrem vorherigen Export.
Text- und Radiofelder sind in den exportierten PDFs weiterhin interaktiv.

Der zusätzliche Screenshot-Harness wurde nach seiner ersten funktional
erfolgreichen Runde um die vollständige Freigabe seiner direkt erzeugten
Qt-Dialoge ergänzt. Der zweite vollständige Bilderlauf endet ebenfalls mit
Prozess-Endcode 0. Source- und EXE-Selbsttest endeten bereits regulär mit 0.
Alle einmaligen Testaufgaben wurden anschließend entfernt; keine laufende
Testautomation bleibt zurück. RDP und Webkonsole bleiben an localhost gebunden.

## Belege außerhalb des Repositorys

Der finale Snapshot heißt `v07-final03`. Im bestehenden VM-Austauschordner
`../../work/pdf-vm/shared/upgrade-v06-20261021/` liegen:

- `source-v07-final03-manifest.json`: Commit und SHA-256 des kopierten Quellstands.
- `results-v07-final03/suite/`: vollständige Windows-Suite mit Ergebnis und Log.
- `results-v07-final03/build/`: Buildprotokoll und Ergebnis.
- `results-v07-final03/smoke/run-s8qq7rz6/`: Quell-Selbsttest und echte PDFs.
- `results-v07-final03/frozen/run-9dka575j/`: EXE-Selbsttest und echte PDFs.
- `results-v07-final03/visualsecond/`: zehn finale Hell-/Dunkel-Fensterbilder,
  Desktopbild und Ergebnisdatei.
- `results-v07-final03/poppler-source/` und `poppler-frozen/`: Seitenfolge/Drehung.
- `results-v07-final03/poppler-v07-source/` und `poppler-v07-frozen/`:
  Nummern, sichtbare Markierungen und unveränderte unmarkierte Seiten.
- `bundle-v07-final03.json`: Python-Archiv, native Module und Quellenhashes.
- `cleanup-v07-final03-*.json`: entfernte einmalige Aufgaben und Prozess-Endcodes.

Die tatsächlich getestete lokale Entwicklungs-EXE liegt im Gast unter
`C:\PDFWorkbenchUpgrade06-20261021\results-v07-final03\build\dist\PDFWorkbench\PDFWorkbench.exe`.
SHA-256:
`2451e8e9af9c2a879cb5a914058172b1aebbbfad22b2347dab16eccb06f9c422`.
Der Ordner `PDFWorkbench` einschließlich `_internal` muss zusammenbleiben.
Dies ist ein lokaler Entwicklungsbuild; Veröffentlichung und Prüfung der
öffentlichen Release-Artefakte sind ein separater Vorgang. Der Gast-Snapshot
enthält kein Git-Checkout; deshalb ist `source_commit` im Entwicklungsbundle
leer, während Snapshotmanifest und alle eingebetteten Dateihashes den Bezug
zum geprüften Repositorystand herstellen.

`PIL.AvifImagePlugin`, `PIL._avif` und `PIL._imagingft` fehlen nach Prüfung
sowohl im Python-Archiv als auch bei den entsprechenden nativen Erweiterungen.
Die vorhandenen fünf Pillow-Erweiterungen sind im Bundlenachweis erfasst;
Bildkomprimierung und AES-256 wurden im eingefrorenen Programm tatsächlich ausgeführt.

## Erneut prüfen

Mit den installierten Entwicklungsabhängigkeiten im Windows-Projektordner:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
python -m unittest discover -s tests -v
python packaging/smoke.py --output <neuer-testordner>
python packaging/build.py --distpath <neuer-distordner> --workpath <neuer-buildordner>
& <neuer-distordner>\PDFWorkbench\PDFWorkbench.exe --self-test --output <neuer-exe-testordner>
```

Die GUI-Läufe erfolgten im angemeldeten Desktop über einmalige Aufgaben mit
`InteractiveToken`. Ein bloßer Build oder ein Offscreen-Lauf ersetzt diese
native Abnahme nicht. macOS wurde in dieser Runde nicht getestet.
