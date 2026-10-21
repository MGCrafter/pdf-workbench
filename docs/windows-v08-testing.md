# Windows-Abnahme für 0.8

Stand: 21.10.2026, abschließender Lauf `v08-final02`, Appdateien aus Commit
`8778f1258b43785b62e290dc64446f08653f8ba2`.

Die eigene persistente Windows-11-Pro-VM (10.0.26200) verwendet Python 3.14.8,
Qt 6.11.2 und PyQt 6.11.0. Alle Oberflächenprüfungen laufen mit dem echten
`windows`-Plug-in auf dem interaktiven Desktop als normaler Benutzer.
Vorhandene Runtime und Caches wurden wiederverwendet.

| Prüfung | Ergebnis |
| --- | --- |
| Gesamte Quellsuite | 454 gemeldete Tests, keine Fehler, 74 Skips/Subfälle, 74,10 s |
| Quellcode-Selbsttest | 23 PDF-Kerntests, 44 GUI-Nachweise, bestanden |
| Tatsächlich gebaute EXE | 23 PDF-Kerntests ohne Skips, 44 GUI-Nachweise, bestanden |
| Eingebettete Appquellen | Alle 51 Source-/Font-/Lizenzhashes stimmen mit `8778f12` überein |
| Darstellung | 14 native Hell-/Dunkel-Screens; Workspace, Lesezeichen, Crop/Resize bei exakt 800×560; Passwort, Wiederherstellung und Updater |
| Zweiter PDF-Renderer | Tatsächliche Windows-EXE-Exporte auf dem Host mit Poppler erneut geprüft |
| Echter Update-Austausch | Private alte EXE → unveränderte neue EXE; PID-Warten, Sicherung, aktuelle Fremddateien und Projektneustart bestanden |
| Echter fehlgeschlagener Neustart | Wiederherstellung der alten EXE und erneutes Öffnen des Projekts bestanden |

Die neue EXE hat SHA-256
`df70c7302ebd3b5b4f6af546db38ff5a24eac661538a2d9bf1510caf6ee0c15c`.
Sie ist ein lokaler Entwicklungsbuild; sein `source_commit` ist mangels Git im
VM-Build null. Der vollständige Quellenhashvergleich bindet ihn an den genannten
Commit. Öffentliche Pakete werden separat von nativen GitHub-Buildern erzeugt
und geprüft. AVIF- und `_imagingft`-Module sind im lokalen Bundle nicht enthalten;
der Dateinachweis `BUNDLE-MANIFEST.json` ist vorhanden.

Die VM hat kein Poppler und Tesseract. 60 der ausgewiesenen Skips betreffen
Poppler einschließlich 52 Geometrie-Subfällen; hinzu kommen sieben fehlende
Symlinkrechte, eine Tesseractprüfung und sechs POSIX-Prozessfixtures.
Die echten EXE-Austauschfälle ergänzen diese POSIX-Fixtures. Die GUI-Abnahme
enthält hier keinen echten OCR-Nachweis. macOS wurde nicht getestet.

Poppler bestätigt Reihenfolge `A2, A1, A3, B1, B2` und den 90°-Orientierungsmarker;
Kapitel 2 vor Kapitel 1 mit richtigen Lesezeichenzielen; Formatänderung nur an
der ausgewählten Kopie mit Größen `300×500`, `792×612`, `300×500` Punkt und
erhaltenem Inhalt. Die fünf neuen Lesezeichen-/Formatraster sind pixelgleich zum
bereits unabhängig geprüften Vorlauf. Die nativen visuellen Crop-/Resize-Exporte
wurden ebenfalls unabhängig mit Poppler geprüft. Originaldateien bleiben bytegleich.

## Reale Update- und Rückfallprüfung

Ausschließlich eindeutig eigene temporäre Installationskopien wurden verwendet.
Eine private Version `0.7.99` enthält einen zusätzlichen Test-CLI-Einstieg, der
die echten Funktionen `prepare_install` und `launch_install` aufruft. Die neue
Version 0.8.0 bleibt unverändert. Bestehende Installationen, Tags und Nutzerdaten
werden nicht aktualisiert. Projektpasswörter stehen weder im Plan noch in der CLI.

Beide abschließenden Fälle laufen unter `Cases Ä $(literal)`: Umlaute,
Leerzeichen und Dollar-/Klammerzeichen bleiben wörtlich erhalten. Der Helfer
wartet auf das tatsächliche Ende der alten EXE. Ein erst nach der Vorbereitung
gespeichertes Projekt mit zwei Seiten und 90°-Drehung sowie eine zuletzt geänderte
Fremddatei bleiben erhalten. Die neue GUI öffnet das Projekt automatisch;
ein erfolgreicher Austausch behält die vollständige alte Programmkopie als Backup.

Für den zweiten Fall fehlt absichtlich `qwindows.dll` im neuen Testpaket; sein
Dateinachweis wird passend neu geschrieben. Die echte Probe ohne GUI besteht,
der tatsächliche GUI-Start scheitert. Der Helfer stellt daraufhin die alte
Programmkopie wieder her und startet sie mit demselben Projekt. Status
`rolled_back`, sichtbarer Projekttitel, beide Seiten/Drehung, Fremddatei und
Original-SHA sind geprüft. Der vollständige Harness endet mit 0; der fehlgeschlagene Neustart ist dabei
ein erwarteter Prüffall.

## Bestätigte Grenze bei Windows-Programmpfaden

Sechs echte EXE-Proben trennen den Fall: ASCII, nur Umlaut, nur Leerzeichen und
nur `$()` starten mit Exit 0 und gültigem Receipt. Ein Semikolon im Runtimepfad
führt bereits vor Python zu einem DLL-Ladefehler des Windows-Bootloaders.
Deshalb prüft der Updater den Cache vor dem Download sowie Programm- und
Helferpfade vor Vorbereitung beziehungsweise Start. Er meldet diesen konkreten
Fall verständlich, bevor er Programmdateien austauscht.
PDF-/Projektnamen und Neustartargumente werden damit nicht allgemein gefiltert.
Die enge Korrektur ist separat unabhängig geprüft; 26 Updater-/Dialogtests
bestehen, einschließlich unveränderter Original- und Planbytes bei Abweisung.

## Rohbelege und Wiederholung

Die üblichen Prüfungen sind `python -m unittest discover -s tests -v`,
`python packaging/build.py --distpath … --workpath …` und
`PDFWorkbench.exe --self-test --output …`. Der native Harness setzt die
Quell-/Testpfade und `QT_QPA_PLATFORM=windows` ausdrücklich und verwendet die
vorhandene Runtime. Ein lediglich konfigurierter Plattformbuild zählt nicht als Test.

Lokale Belege außerhalb des Quellpakets:

- `work/pdf-vm/shared/v08-20261021/source-v08-final02-manifest.json`
- `work/pdf-vm/shared/v08-20261021/results-v08-final02-suite/result.json`
- `work/pdf-vm/shared/v08-20261021/results-v08-final02-frozen/run-48osqu08/result.json`
- `work/pdf-vm/shared/v08-20261021/results-v08-final02-smoke/`
- `work/pdf-vm/shared/v08-20261021/results-v08-final02-visual/`
- `work/pdf-vm/shared/v08-20261021/results-v08-final02-updatesfinal/result.json`
- `work/pdf-vm/shared/v08-20261021/final01-pathmatrix-result.json`
- `work/pdf-app/v08/windows/frozen-final02-source-commit-verification.json`
- `work/pdf-app/v08/windows/poppler-final02-order/result.json`
- `work/pdf-app/v08/windows/poppler-final02-v08/result.json`

Alle eigenen abgeschlossenen Testaufgaben wurden entfernt; keine eigene Test-EXE
läuft weiter. Prüfkopien, Sicherungen und Belege bleiben zur Nachprüfung erhalten. Kein laufender Testagent ist eine dauerhafte
Hintergrundautomation.
