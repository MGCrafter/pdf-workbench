# Windows-Abnahme des lokalen 0.9-Entwicklungsstands

Stand: 22.10.2026, abschließender Lauf `final04`. Geprüfter Codecommit:
`1c84ecc408c17aadb391b5b4c89d622f6e56e287`.
Dieser Bericht beschreibt einen lokalen Entwicklungsbuild. Die öffentliche
Release-Abnahme wird getrennt auf den regulären Buildern durchgeführt.

Die bestehende dedizierte Windows-11-Pro-VM (10.0.26200) wurde wiederverwendet.
Die Prüfungen laufen als normaler Benutzer auf dem echten interaktiven Desktop
mit Qt-Plattform `windows`: Python 3.14.8, Qt 6.11.2/PyQt 6.11.0, PyMuPDF 1.28.2
und pyHanko 0.38.0. Eine eigene Runtimekopie mit `tzdata` bleibt getrennt von
vorherigen Programmordnern; es wurden keine fremden VM-Disks verändert.

| Prüfung | Tatsächliches Ergebnis |
| --- | --- |
| Vollständige Quellsuite | 534 Tests, 0 Fehler, 0 Fehlermeldungen, 79 erwartete Skips/Subfälle, 88,039 s |
| Unabhängige neue PDF-Abnahme | 11 echte PDF-/Abbruch-/Datenverlustfälle bestanden, keine Skips |
| Vollständiger Quellcode-Selbsttest | 34 PDF-Fälle ohne Skips und 59 native GUI-Nachweise bestanden |
| Tatsächlich gebaute EXE | Derselbe vollständige Selbsttest: 34 PDF-Fälle ohne Skips und 59 native GUI-Nachweise bestanden |
| Unabhängiger zweiter Renderer | Sieben Prüfgruppen mit Host-Poppler an tatsächlichen Windows-EXE-Ausgaben bestanden |
| Quellenbindung | Alle 62 Source-/Font-/Lizenzhashes entsprechen dem Codecommit; alle 58 tatsächlich eingebetteten Appmodule unabhängig als Codeobjekte mit frischer Kompilierung des Git-Sources verglichen |
| Visuelle Kontrolle | Native Hell-/Dunkelansichten sowie neue Dialoge bei 800×560 geprüft; Scrollbereiche, Bestätigungen und Speicherschaltflächen erreichbar |
| Private Entwicklungs-ZIP | SHA-256, Größe, ZIP-CRC, eingebettete Berichte und EXE-Hash unabhängig auf dem Host erneut geprüft |

Die 79 Skips/Subfälle entfallen auf 62 Popplerfälle (kein Poppler im Gast),
acht fehlende Symlinkrechte, sechs POSIX-Prozessfixtures und drei optionale
Tesseract-/OSD-Fälle. Der zweite Renderer wurde auf dem Linux-Host an den echten
Windows-Ausgaben ausgeführt. Auf diesem Windows-Gast war kein Tesseract vorhanden;
die Abnahme enthält deshalb keinen tatsächlichen OCR-/OSD-Lauf und einen
GUI-Nachweis weniger als Linux. Vorhandene eigene Windows-Testordner, Hostcaches
und der übliche Tesseract-Installationspfad wurden geprüft; kein neuer OCR-Download
wurde gestartet. macOS bleibt ausdrücklich ungetestet.

## Tatsächliche neue Abläufe

Die nativen Dialoge und MainWindow-Jobs prüfen unabhängige Tabs mit eigenen
Projekten, Zoom und Undo/Redo, reversibles Verschieben, Tab-Sperren bei laufenden
Jobs und Abbrechen beim Schließen. Weitere Prüfungen verwenden Textbearbeitung,
explizit bestätigte Schwärzung mit echter Seitenvorschau, PNG-/UTF-8-Export,
Scanaufbereitung nur der Auswahl, Duplexfolge, manuelle Duplikatkandidaten,
Inhaltsverzeichnis und interne Links sowie gemerkte Stapelvorlagen.

Ein echtes eigenes Test-PFX signiert neue PDFs; die Integrität und getrennte
Vertrauensaussage erscheinen im nativen Prüfbericht. Ein absichtlich veränderter
signierter Inhalt besteht die kryptografische Prüfung nicht. Poppler `pdfsig`
bestätigt zusätzlich vollständige signierte Dateiabdeckung. Selbstsignierte
Testzertifikate sind keine Bestätigung einer externen Identität, eines
Widerrufsstatus oder einer qualifizierten Signatur.

PDF/A-2b wurde sowohl aus Source als auch aus der gebauten EXE tatsächlich
konvertiert und validiert: privat extrahiertes Ghostscript 10.08.0, veraPDF 1.30.3
mit seiner ausführbaren CLI-JAR und portable Java 8u504. Die offiziellen
Downloads wurden einmalig zwischengespeichert und anhand veröffentlichter Hashes
beziehungsweise der veraPDF-GPG-Signatur geprüft. Kein Systeminstaller oder
Registrywechsel war notwendig. Diese optionalen Hilfsprogramme sind nicht Teil
der App-ZIP; Nutzer wählen ihre eigene lokale Installation.

## Nachprüfung wesentlicher Korrekturen

Der native Vorlauf deckte einen tatsächlichen Timingfehler beim modalen
Tab-Schließen auf: Ein später Zoom-/Auswahlcallback konnte einen zuvor gestoppten
Autosave-Timer neu starten. Die finale Fassung sperrt Autosave verschachtelbar
während der gesamten Speicherfrage, einschließlich ihrer modalen Unterdialoge.
Der deterministische Test liefert zusätzlich späte Session- und direkte
Autosave-Callbacks in einer echten verschachtelten Qt-Ereignisschleife;
Verwerfen startet keinen Writer, Abbrechen nimmt die Sicherung wieder auf.
Dieser Fall besteht in der finalen vollständigen Windows-Suite.

Die unabhängige Reviewrunde prüfte außerdem die tatsächliche Erhaltung fremder
Änderungen am selben Inode bei PDF- und Projekt-Fallbackfehlern. Cleanup verlangt
nun bestätigte eigene Bytes beziehungsweise Größe und SHA-256 zusätzlich zur
Dateiidentität. Fremde Änderungen bleiben erhalten und werden im Fehler genannt.
Details und weitere korrigierte Befunde stehen in der lokalen Reviewdokumentation.

## Artefakt und Wiederholung

EXE im Gast:
`C:\PDFWorkbenchV09-20261022\results-final04\build\dist\PDFWorkbench\PDFWorkbench.exe`.
SHA-256:
`d579913a1d548a7e7d97c322f5a6f7f4d846971b6d04006fcbe1c68887946043`.

Mangels Git im Gast ist `source_commit` der nativen Build-Provenance null.
Das vollständige Quellenmanifest und der tatsächliche Codeobjektvergleich binden
den Build unabhängig an den oben genannten Commit; es wird kein Git-Nachweis
im Gast erfunden.

Die private Datei
`PDFWorkbench-v0.9.0-development-1c84ecc-windows-x86_64.zip` enthält den vollständigen
Programmordner, Entwicklungshinweise, Anleitung und die Quellen-/Frozen-Belege:
79.066.771 Bytes, SHA-256
`48dadaafe579afbdc8a3a7ea989619bbd7c4c0c4b50e04606d0ab93c685895d8`.
Sie ist ein Testartefakt, kein öffentliches Release. Vor einer Veröffentlichung
bleibt die vollständige erneute Drittanbieter-/Corresponding-Source-Prüfung für
die neuen Laufzeitabhängigkeiten erforderlich.

Lokale Belege außerhalb des Sourcepakets:

- `work/pdf-vm/shared/v09-20261022/source-final04-manifest.json`
- `work/pdf-vm/shared/v09-20261022/results-final04/suite/result.json`
- `work/pdf-vm/shared/v09-20261022/results-final04/smoke/run-xk8blv7b/result.json`
- `work/pdf-vm/shared/v09-20261022/results-final04/frozen/run-iw3iswxq/result.json`
- `work/pdf-vm/shared/v09-20261022/results-final04/source-binding.json`
- `work/pdf-app/v09/poppler-windows-frozen-final04/result.json`
- `work/pdf-vm/shared/v09-20261022/development-final04-result.json`
- `work/pdf-vm/shared/v09-20261022/task-cleanup-final04.json`

Wiederholung: `python -m unittest discover -s tests -v`,
`python packaging/build.py --distpath … --workpath …` und
`PDFWorkbench.exe --self-test --output …`. Die tatsächliche externe PDF/A-Abnahme
verwendet `PDFWB_TEST_VERAPDF` und `PDFWB_TEST_GHOSTSCRIPT` mit den vorhandenen
lokalen Hilfsprogrammen; ohne sie wird der betreffende Fall ausdrücklich
übersprungen und besteht keine Veröffentlichungsfreigabe.

Alle 16 eigenen abgeschlossenen Testaufgaben wurden entfernt; keine eigene
Test-EXE läuft weiter. RDP und Webkonsole sind weiterhin nur an `127.0.0.1`
gebunden. Ein Testagent ist keine dauerhafte Hintergrundautomation.
