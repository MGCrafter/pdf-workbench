# Tests

## Lokale Entwicklung 0.9

Der eingefrorene Quellstand `1c84ecc408c17aadb391b5b4c89d622f6e56e287`
besteht unter Linux **540 Tests ohne Fehler oder Skips** (72,416 Sekunden).
Das daraus tatsächlich gebaute Entwicklungsprogramm besteht nativ unter Wayland
**34 PDF-Kerntests ohne Skips und 60 GUI-Nachweise**, einschließlich realer OCR,
Zertifikatssignatur und tatsächlicher Ghostscript-/veraPDF-PDF/A-Konvertierung.
Alle 62 eingebetteten App-/Font-/Lizenzhashes stimmen mit dem Gitbaum überein.
Sieben zusätzliche unabhängige Poppler-Prüfungen bestätigen Reihenfolge,
Rotation, Bildexport, echte Schwärzung, Textaustausch, Linkziele, PDF/A-Darstellung
und kryptografische Signatur der tatsächlich aus dem gebauten Programm
geschriebenen Dateien. Die Prüfung verwendet auch `pdftotext` und `pdfsig`.

Unter Windows 11 bestehen **534 gemeldete Tests ohne Fehler**, mit 79 ausdrücklich
ausgewiesenen Skips/Subfällen (88,039 Sekunden). Quellcode und fertig gebaute
Windows-EXE bestehen nativ je 34 PDF-Fälle ohne Skips und 59 GUI-Nachweise.
In dieser VM fehlt Tesseract; die bestehende OCR-GUI-Prüfung ist dort ausgenommen.
Alle 15 neuen GUI-Abläufe einschließlich echter Zertifikatssignatur und
Ghostscript-/veraPDF-PDF/A-Konvertierung bestehen. Die Windows-EXE enthält
58 tatsächlich ausgelesene Appmodule und 62 Quell-/Font-/Lizenzrecords, die
unabhängig mit demselben Gitbaum abgeglichen wurden. Das VM-Quellarchiv besitzt
keine Git-Metadaten; die Bindung an den Commit beruht auf diesem externen
Code-/Hashvergleich, nicht auf einem behaupteten eingebetteten Commitwert.
Auch die tatsächlichen Windows-EXE-Exporte bestehen die sieben zusätzlichen
Poppler-Prüfungen. Details und erwartete Ausnahmen stehen im
[Windows-Bericht für 0.9](windows-v09-testing.md).

Die neuen Regressionen prüfen getrennte Tab-Projekte und Passwörter,
Wiederöffnung mehrerer Projekte, Undo nach Seitenübertragung, reine Historie,
Sicherung in inaktiven Tabs und das Schließen während einer modalen
Speicherabfrage. Manipulierte Zwischen-/Zieldateien werden erhalten und benannt;
sie dürfen weder als eigene Ausgaben übernommen noch als erfolgreich geprüft
gemeldet werden. PDF/A wird erst nach einem vollständigen veraPDF-Bericht für
genau die ausgegebenen Daten gespeichert. Signaturtests trennen Integrität,
Vertrauen und nicht bestätigten Sperrstatus.

Weitere echte PDF-Fälle umfassen alle Drehungen mit CropBox/UserUnit,
PDF-konforme indirekte interne Linkziele, maximal tiefe Lesezeichen und
Schriftzeichengrenzen, Erhalt bearbeitbarer Objekte/Formularwerte beim
Inhaltsverzeichnis sowie lokale OCR vor neuen Seitennummern. Unabhängige
Review-Runden wurden vor und nach wesentlichen Korrekturen durchgeführt;
die konkrete interne Chronik steht in `docs/review.md`.

Diese Ergebnisse betreffen lokale Entwicklungsprogramme. Der Linux-Build
entsteht auf dem aktuellen Host und ist kein auf Ubuntu 22.04 geprüfter
öffentlicher Release. Neue Laufzeitkomponenten benötigen vor Veröffentlichung
erweiterte Lizenz-/Quellnachweise; die öffentlichen Downloads bleiben 0.8.0.
macOS ist weiterhin ungetestet. Funktionen und Grenzen stehen unter
[Ausbau 0.9](v09-development.md).

## Lokale Abnahme 0.8

Der Appstand `8778f12` besteht unter Linux **460 Tests ohne Fehler oder Skips**
(60,345 Sekunden). Die daraus gebaute App besteht **23 PDF-Kerntests und
45 GUI-Nachweise**, nativ auf Wayland. Zusätzlich bestehen 69 Release-/
Publishingtests. Der Windows-Pfadschutz wird vor Netzwerk, Staging und
Helferstart geprüft; fünf Paketierungstests sichern interne Bibliothekskopien
und die Abweisung externer, zyklischer oder verzeichnisweiter Links ab.
Ein unabhängiger Stresstest hat 144 echte Dialogöffnungen, 312 globale
Themewechsel und 12 abgebrochene Schließversuche ohne Absturz durchlaufen.

Neue PDF-Fälle prüfen unter anderem alle 16 Kombinationen aus intrinsischer
und Arbeitsseitendrehung mit CropBox und UserUnit in Qt und Poppler,
Formularansichten nach Skalierung und erneutem Ausfüllen sowie ausgewählte
Kopien. Projekte werden mit echten eingebetteten PDFs, Objekten, Lesezeichen,
Undo-/Redo-Historie und Verschlüsselung wieder geöffnet. Beschädigte Projekte,
fehlende Originaldateien, parallele Zielersetzung, reine Undo-Sitzungen,
Autosave-Rennen und Cleanupfehler haben eigene Verlustschutzprüfungen.

Echte native Updateprozesse tauschen getrennte private Programmkopien von
`0.7.99` gegen die unveränderte gebaute `0.8.0` aus. `0.7.99` ist ausschließlich
ein Testfixture, keine veröffentlichte frühere Version. Die Abnahme prüft den
lebenden alten Prozess, dessen Ende, einen erst nach Vorbereitung gespeicherten
Projektstand mit zwei Seiten und 90°-Drehung sowie fremde Benutzerdateien.
Ein zweites neues Paket ohne Qt-Platformplugins besteht die Programmprobe,
scheitert aber beim echten GUI-Start: Der Helfer stellt dann die alte Kopie
und deren Arbeit wieder her. Beide Fälle bestehen auf Linux und Windows, unter
Windows auch aus einem Installationspfad mit Umlaut, Leerzeichen und wörtlichem
`$()`. Eine native Sechs-Pfade-Matrix grenzt den Windows-Bootloaderfehler auf
Semikolon in Runtimeordnern ein; der Updater weist diese vor Download und Übergabe ab.

Die Windows-VM besteht 454 gemeldete Tests mit 74 ausdrücklich ausgewiesenen
Skips/Subfällen; Quellcode und gebaute EXE bestehen je 23 Core- und 44 native
GUI-Nachweise. In dieser VM fehlen Poppler und Tesseract; sechs POSIX-
Executable-Fixtures und sieben Symlinkrechteprüfungen sind ebenfalls ausgenommen.
Echte Windows-EXE-Exporte wurden zusätzlich auf dem Host mit Poppler geprüft.
Die Plattformgrenzen bleiben im [Windows-Bericht für 0.8](windows-v08-testing.md)
sichtbar. macOS ist weiterhin ungetestet.

Diese lokalen Belege ersetzen nicht die separate native Abnahme der öffentlichen
Releasepakete. Releasegates verlangen für 0.8 alle bisherigen GUI-Nachweise
einschließlich echter OCR sowie die neuen Funktionen, 23 Coretests ohne Skips,
passende native Qt-Plattformen und identische Appquellen beider Builder.
Der vollständige [Releaseprobelauf für `8778f12`](https://github.com/MGCrafter/pdf-workbench/actions/runs/39657008581)
besteht beide nativen Builds und die vollständige Quellenprüfung. Seine vier
kleinen Builder-/Acceptance-Berichte wurden separat über begrenzte HTTP-Range-
Abrufe geprüft: je 23 Core- und 45 GUI-Nachweise einschließlich echter OCR,
native XCB-/Windows-Plattformen und alle 51 App-/Font-/Lizenzhashes passend zum
Gitbaum. Große Pakete wurden für diese Metadatenprüfung nicht erneut geladen.

## Vorherige Abnahmen

Version 0.6 ergänzt Tests für echte AES-256-Dateien,
Passwortwiederholung und Abbruch, Bildkomprimierung samt transparenten Bildern,
AcroForm-Werte und sichtbare Felddarstellungen. Sie prüfen auch wiederholtes
Ausfüllen nach Merge, Duplikat, Seitendrehung und Export sowie den Ausschluss
nicht exportierter Formularseiten und ihrer Daten. Poppler und Qt vergleichen
die Darstellung unabhängig von pypdf. Die neue GUI-Abnahme prüft die Dialoge,
800×560-Pixel-Fenster in beiden Themes und abschaltbare Animationen.

Der lokale Schlussstand 0.7 (`be75a71`, 21.10.2026) besteht auf Linux
373 Tests ohne Fehler oder Skips. Die gebaute App besteht zusätzlich 18
PDF-Kerntests und die native Wayland-GUI-Abnahme. Das ist ein lokaler
Entwicklungsbuild auf glibc 2.44; die veröffentlichten Linux-Pakete werden
separat auf Ubuntu 22.04 gebaut und getestet.

Unter Windows 11 bestehen 367 gemeldete Tests mit 14 ausdrücklich ausgewiesenen
Skips sowie der Quell- und EXE-Selbsttest mit je 18 PDF-Prüfungen und nativer
GUI-Abnahme. Details und Prüfgrenzen stehen im
[Windows-Testbericht für 0.7](windows-v07-testing.md). Der historische
[Bericht für 0.6](windows-v06-testing.md) bleibt erhalten.
Die vollständige lokale Reviewchronik wird in `docs/review.md` geführt und nicht
veröffentlicht. Die lokalen Nachweise beschreiben Entwicklungsbuilds. Für die
veröffentlichten Pakete prüft der jeweilige Release-Lauf zusätzlich die fertig
gebauten Windows- und Linux-Programme und archiviert ihre Berichte.

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

## Neue Funktionen in 0.7

Die Vergleichstests prüfen eingefügte und verschobene Seiten, Text- und reine
Bildänderungen, eigene Anmerkungen, Grenzwerte, Abbruch und unveränderte Originale.
GUI-Tests prüfen gemeinsame Navigation, Zoom, Scrollen und das Beenden der Worker.

Formulartests erstellen echte Text-, Checkbox-, Dropdown- und Radiofelder,
bearbeiten Eigenschaften und Geometrie und öffnen exportierte Dateien erneut.
Gezielte Fälle prüfen verschachtelte Felder, gelöschte Werte, Radio-Exportwerte,
Auswahlexporte und das Weglassen nicht exportierter Formularseiten.
Seitenmarkierungen werden bei allen 16 Kombinationen aus Quelldrehung und
Arbeitsdrehung mit versetztem Beschnitt durch Qt und Poppler gerendert.
Weitere Fälle prüfen Unicode, transparente Wasserzeichen, Vorlagen, getrennte
Seitenkopien, Originalschutz und einen gemeinsamen Undo-/Redo-Schritt.

Ab 0.7 sind im fertigen Programm mindestens 18 PDF-Kerntests ohne Skips sowie
drei benannte GUI-Nachweise für Vergleich, Formulargestaltung und Seitenmarkierungen
zusätzlich zu den bisherigen Release-Prüfungen erforderlich. Die Existenz dieser
Prüfungen ist noch kein bestandener Plattformnachweis; Ergebnisse werden erst
nach einem tatsächlichen Lauf dokumentiert.

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
Ab 0.6 sind außerdem 15 PDF-Kerntests und benannte Nachweise für AES-256,
Komprimierung, Formularbearbeitung mit Undo, Passwortwiederholung und Abbruch
sowie reduzierte Bewegung in beiden kompakten Themes erforderlich.

Für die optionalen echten OCR-Tests muss Tesseract mit Englisch und `pdf.ttf`
installiert sein. Ohne diese Voraussetzungen werden sie ausdrücklich
übersprungen; Tests laden selbst keine Sprachpakete herunter. Ein vorbereiteter
Cache lässt sich mit `PDF_WORKBENCH_OCR_TEST_TESSDATA` angeben. Die nativen
Release-Builder bereiten eine Engine vor und setzen
`PDF_WORKBENCH_REQUIRE_OCR=1`, sodass fehlendes OCR dort fehlschlägt. Unter Linux
prüft zusätzlich Poppler, dass die neue unsichtbare Textebene die sichtbare
Seite nicht verändert. Die Test-Engine wird nicht ins App-Paket aufgenommen.
