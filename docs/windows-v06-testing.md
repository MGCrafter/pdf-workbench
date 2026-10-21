# Windows-Abnahme für den lokalen Stand 0.6.0

Am 21.10.2026 lief der unveröffentlichte Arbeitsstand in der vorhandenen,
eigenen Windows-VM: **Windows 11 Pro x64, 10.0.26200**, Python 3.14.8,
PyQt 6.11.0 / Qt 6.11.2 mit dem nativen Qt-Plug-in `windows`.
Dies war ein echter Lauf im angemeldeten Desktop, einschließlich einer dort
gebauten und gestarteten `PDFWorkbench.exe`.

Die vorhandenen Python-/Qt-Archive wurden wiederverwendet. Nur kleine zusätzliche
Windows-Wheels für Pillow, AES-Unterstützung, Tests und den Build wurden geladen.
Alle Runtime-Archive wurden vor dem Entpacken per Größe und SHA-256 geprüft.
Die VM-Zugangsdaten wurden weder ausgegeben noch in Projektdateien übernommen.

## Ergebnisse

| Prüfung | Ergebnis |
| --- | --- |
| Gesamte Test-Suite, natives Windows-Qt | 266 gemeldete Tests, 0 Fehler, 11 ausgewiesene Skips |
| Neue GUI-Abläufe | Passwort-Retry/Abbruch, AES-Export, Komprimierungsvergleich/-abbruch, Formular-Export/Undo, kompakte Darstellung und reduzierte Bewegung bestanden |
| Selbsttest aus Quellstand | 15 PDF-Prüfungen ohne Skip sowie native GUI-Abnahme bestanden |
| Selbsttest der gebauten EXE | 15 PDF-Prüfungen ohne Skip sowie native GUI-Abnahme bestanden |
| Neue Bibliotheken in der EXE | AES-256, Pillow-Bildkomprimierung und AcroForm-Ausfüllen/Export/Wiederöffnen tatsächlich ausgeführt |
| Oberfläche | Hell/Dunkel, leer und mit Dokument, tatsächliche 800×560, 1160×780 und 1440×900 Pixel geprüft; Schaltflächen im Fenster |
| Bewegung | Willkommensanimation bei etwa 100 ms mit Zwischenwert 0,50 beobachtet, Endwert 1,0; gespeicherte reduzierte Bewegung erreicht Endwert sofort |
| Zweiter Renderer | Unter Windows exportierte PDFs aus Quellstand und EXE mit Poppler auf dem Host gerendert; Reihenfolge und 90°-Drehung korrekt |
| Archiv | 201 Einträge, vollständiger Programmordner; CRC, EXE-Prüfsumme, Quellenbezug sowie Pillow-/cryptography-Metadaten geprüft |

Die elf Skips der großen Suite betreffen acht Poppler-Fälle ohne Poppler in der
VM, zwei Tests ohne Windows-Symlinkprivileg und die optionale echte
Tesseract-Testklasse. Die OCR-Engine wurde für diesen Windows-Lauf nicht
installiert; ein neuer nativer Windows-OCR-Nachweis wird daher nicht behauptet.
Die Poppler-Abnahme der tatsächlich unter Windows erzeugten Exporte lief
zusätzlich auf dem Host: Seiten `A2, A1, A3, B1, B2`, Seite `A1` im Querformat
mit korrekt gedrehtem rotem Orientierungsmarker. Qt/PDFium wurde zusätzlich im
nativen Windows-Selbsttest für Vorschau und gerenderte Exportvergleiche verwendet.

Für die Prüfung des großen Fensters wurde der VM-Desktop vorübergehend von
1280×800 auf 1920×1080 gesetzt und danach erfolgreich zurückgestellt. 13 finale
Screenshots dokumentieren die Zustände einschließlich einer bereits vorhandenen,
selbst erzeugten Beispiel-Projektmappe. Alle einmaligen Testaufgaben wurden
anschließend entfernt; es bleibt keine laufende Testautomation zurück.
RDP und Webkonsole blieben ausschließlich an `127.0.0.1` gebunden.

## Lokale Belege und Entwicklungsbuild

Der abschließende Quell-Snapshot heißt `final06`. Alle Quelldateien, Fonts,
Build-/Testskripte und Tests wurden gegen ihren SHA-256-Inhaltsnachweis geprüft.
Die Laufdaten liegen außerhalb des Repositorys unter:

- `../../work/pdf-vm/shared/upgrade-v06-20261021/source-final06-manifest.json`
- `../../work/pdf-vm/shared/upgrade-v06-20261021/results-final06/suite/`
- `../../work/pdf-vm/shared/upgrade-v06-20261021/results-final06/build/`
- `../../work/pdf-vm/shared/upgrade-v06-20261021/results-final06/smoke/run-3ki80uv_/`
- `../../work/pdf-vm/shared/upgrade-v06-20261021/results-final06/frozen/run-hj1lorcn/`
- `../../work/pdf-vm/shared/upgrade-v06-20261021/results-final06/visual/`
- `../../work/pdf-vm/shared/upgrade-v06-20261021/results-final06/poppler-source/`
- `../../work/pdf-vm/shared/upgrade-v06-20261021/results-final06/poppler-frozen/`

Das vollständige portable Windows-Programm liegt als **unveröffentlichter
Entwicklungsbuild**, ausdrücklich kein freigegebener Release, unter:

`../../work/pdf-app/upgrade-v06/PDFWorkbench-0.6.0-dev-windows-x86_64.zip`

Größe: 51.818.376 Bytes. SHA-256:
`1f8aab209180c32cd6f39a14a57178c237e47a3c78cea408afbb3eaefcecac8b`.

Die darin tatsächlich getestete EXE hat SHA-256
`0b908009c71165a1c0d698762e7d79ebab7e890b1fdb761f32816573ae03798c`.
`windows-development-artifact.json` und `SHA256SUMS-windows-dev` im selben Ordner
halten den Inhaltsbezug fest. Im Archiv weist `DEVELOPMENT_BUILD.txt` den Status
aus. Der gesamte Ordner `PDFWorkbench` mit `_internal` muss zusammenbleiben.
Der Binärbuild wurde nicht öffentlich hochgeladen; bestehende Releases blieben
unverändert. Der zugehörige Quellcode wird separat über einen GitHub-Branch
und Pull Request bereitgestellt.

## Erneut prüfen

Mit installierten Entwicklungsabhängigkeiten im Windows-Projektordner:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
python -m unittest discover -s tests -v
python packaging/smoke.py --output <neuer-testordner>
python packaging/build.py --distpath <neuer-distordner> --workpath <neuer-buildordner>
& <neuer-distordner>\PDFWorkbench\PDFWorkbench.exe --self-test --output <neuer-exe-testordner>
```

Über den QEMU Guest Agent starten Prozesse zunächst in Session 0. Für die
Oberflächenabnahme wurde deshalb wie bei früheren Prüfungen eine einmalige
geplante Aufgabe mit `InteractiveToken` des bereits angemeldeten Testkontos
verwendet; die Helfer und Aufgabenbelege liegen beim Snapshot außerhalb des
Repositorys. Ein bloßer Build oder ein `offscreen`-Lauf ersetzt diese Abnahme nicht.
