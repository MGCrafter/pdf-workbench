# Native Laufzeitbibliotheken von 0.6.0: Windows-Audit

Diese Prüfung vom 21.10.2026 trennt installierte Pakete, tatsächlich gebündelte
Dateien und deklarierte optionale Abhängigkeiten. Die vollständigen Ergebnisse
und unveränderten Originaltexte liegen außerhalb des Repositorys unter
`../../work/pdf-vm/shared/upgrade-v06-20261021/runtime-audit/`.
Die Prüfung ergänzt die vorhandene Qt-/CPython-Abnahme; sie ersetzt diese nicht.

## Beobachtete Windows-Dateien

Geprüft wurden die offiziellen Windows-Wheels für Pillow 12.3.0 und
cryptography 50.0.1 aus dem vorhandenen Cache sowie der tatsächlich gebaute
Entwicklungsstand von `PDFWorkbench.exe`. Die Archive wurden zusätzlich mit
REA inventarisiert, ohne sie als Programm auszuführen:

- Pillow: SHA-256 `fdafc9cce40277e0f7a0feabce0ee50dd2fa1800f3b38015e51296b5e814048d`,
  Evidence `ev_839eafdc0af6637a28e8131ccfb0f477941dec41ad00c0112a2340b3cee60c84`.
- cryptography: SHA-256 `aed8db4f6d71c51efb89530e12d9464e7bf2923d46c3205dc794a2a93f8c0648`,
  Evidence `ev_85380d25111124854a6718308f35b7e635bc6b3f76c4241656d5e398e1b7f561`.

Das ursprüngliche Windows-Entwicklungspaket enthielt `PIL._avif`, `_imaging`,
`_imagingcms`, `_imagingmath`, `_imagingtk` und `_webp`. `_imagingft` und
`_imagingmorph` waren trotz Installation nicht gebündelt. Die PE-Importtabellen
der Pillow-Erweiterungen nennen Python, Microsofts Laufzeit und Windows-APIs;
die folgenden Bildbibliotheken sind in den Erweiterungen statisch enthalten.
Maschinenlesbare Datei- und Importnachweise:
`windows-runtime-observed.json` und `windows-runtime-manual-audit.json`.

## Pillow: verwendete und optionale Komponenten

Nach dem ausdrücklich beschlossenen Ausschluss von `PIL.AvifImagePlugin` und
`PIL._avif` bleiben für das Windows-Pillow diese sieben nativen Quellenpakete:

| Komponente | Version | Versionsnachweis |
| --- | --- | --- |
| libjpeg-turbo | 3.1.4.1 | native Pillow-Abfrage und Build-Rezept |
| libtiff | 4.7.1 | native Pillow-Abfrage und Build-Rezept |
| libwebp einschließlich SharpYUV | 1.6.0 | native Pillow-Abfrage und Build-Rezept |
| OpenJPEG | 2.5.4 | native Pillow-Abfrage und Build-Rezept |
| zlib-ng | 2.3.3 | native Pillow-Abfrage und Build-Rezept |
| XZ/liblzma | 5.8.3 | Windows-TIFF-Build-Rezept und Originaltext im Wheel |
| Little CMS | 2.19.1 | Originaltext und Build-Rezept; native API gibt verkürzt `2.19` zurück |

Das vollständige originale `pillow-12.3.0.dist-info/licenses/LICENSE` enthält
bereits die Lizenztexte dieser Komponenten. Die sieben vollständigen
Quellarchive werden zusätzlich erfasst, damit das Projektversprechen zu
vollständigen Bibliotheksquellen auch die statisch gebündelten Codecs umfasst.
Die genaue Plattformversion stammt aus den gepinnten
[Pillow-Abhängigkeiten](https://github.com/python-pillow/Pillow/blob/12.3.0/.github/dependencies.json)
und dem passenden [Windows-Build-Rezept](https://github.com/python-pillow/Pillow/blob/12.3.0/winbuild/build_prepare.py).
Ein Linux-Systempaket mit anderer Version ist kein Ersatz für diese Quellen.

Die generische Pillow-SBOM ist eine größere Menge möglicher Abhängigkeiten.
Sie nennt unter anderem FriBiDi 1.0.16, Raqm, FreeType und libimagequant.
Das allein belegt keinen Windows-Lieferumfang. FriBiDi/Raqm/libimagequant/XCB
waren im nativen Windows-Test deaktiviert; kein FriBiDi-DLL war im Wheel oder
Entwicklungsbundle enthalten. Der
[offizielle Windows-Wheel-Workflow](https://github.com/python-pillow/Pillow/blob/12.3.0/.github/workflows/wheels.yml)
schließt FriBiDi ausdrücklich aus dem Wheel aus und verteilt es separat.
Die FreeType-Erweiterung wurde ebenfalls nicht gebündelt. HarfBuzz, Brotli und
libpng aus deren statischem Build werden daher nicht als zusätzliche
Pillow-Laufzeitdateien dieses App-Pakets behauptet. Unveränderte Wheel-Texte
bleiben als konservative Obermenge erhalten.

Ein zusätzlicher nativer TIFF-Test bestätigte LZMA, WebP, JPEG und Deflate
jeweils durch Schreiben und erneutes Dekodieren. Zstd ist in diesem Windows-TIFF
nicht konfiguriert. Ein isolierter Versuch, den nicht konfigurierten Zstd-
Schreiber aufzurufen, endete in der Fremdbibliothek hart; die App schreibt keine
TIFF-/Zstd-Dateien. Daraus wird kein unterstützter Codec abgeleitet.

## Warum AVIF ausgeschlossen wird

`PIL._avif.codec_versions()` meldete im tatsächlichen Windows-Wheel
`dav1d [dec]:1.5.3-0-gb546257, aom [enc]:3.14.1`. Diese statischen Komponenten
werden von der generischen Pillow-SBOM nicht vollständig beschrieben.
Das ursprüngliche Wheel-Lizenzaggregat enthält auch nicht die vollständigen
separaten AOM- und dav1d-Distributionstexte.

Die Abhängigkeiten lassen sich aus libavif 1.4.2 nachvollziehen:
[AOM 3.14.1](https://github.com/AOMediaCodec/libavif/blob/v1.4.2/cmake/Modules/LocalAom.cmake),
[dav1d 1.5.3](https://github.com/AOMediaCodec/libavif/blob/v1.4.2/cmake/Modules/LocalDav1d.cmake),
[libyuv mit festem Commit](https://github.com/AOMediaCodec/libavif/blob/v1.4.2/cmake/Modules/LocalLibyuv.cmake)
und [SharpYUV aus libwebp 1.6.0](https://github.com/AOMediaCodec/libavif/blob/v1.4.2/cmake/Modules/LocalLibsharpyuv.cmake).
Die gewählte Implementierung benötigt Pillow-AVIF nicht. Der Release-Build
schließt deshalb Plugin und native Erweiterung ausdrücklich aus. Der finale
Bundle-Nachweis muss deren Abwesenheit prüfen; das frühere Entwicklungspaket
allein ist dafür kein Beleg.

## cryptography und CFFI

Das Windows-Wheel von cryptography 50.0.1 enthält statisches **OpenSSL 4.0.2**;
das bestätigten seine SHA-256-haltige SBOM und die native Abfrage
`OpenSSL 4.0.2 25 Aug 2026`. Die Rust-Erweiterung importiert kein externes
OpenSSL-DLL. Das separate `libcrypto-3.dll` von CPython ist ein anderer Provider.
Die OpenSSL-Quelle und deren Originaltexte werden versionsgenau erfasst.

Die Cargo-SBOM nennt sieben interne cryptography-Workspace-Komponenten und
32 Registry-Crates. Die internen Quellen liegen im cryptography-Sdist.
Für die Registry-Crates werden die vollständigen `.crate`-Archive mit den
SBOM-Prüfsummen und ihre originalen Lizenz-/Notice-Dateien aufgenommen.
Besondere Ausdrücke wie `Apache-2.0 WITH LLVM-exception` bei target-lexicon
und `Unicode-3.0` bei unicode-ident dürfen nicht auf eine pauschale
MIT-/Apache-Angabe verkürzt werden.

Beide nativen Rust-Erweiterungen nennen außerdem Compiler-Commit
`88d9e12ae178fab0fb5cc050a94da85685d449ea`. Das offizielle Rust-1.98.0-
Kanalmanifest bindet den passenden `rust-src`-Standardbibliotheksquellstand
an Commit und SHA-256. Dieser Quellstand, die ursprünglichen Rust-Lizenzen
und Copyright-Texte sowie die passenden x86_64-Linux-/MSVC-Abhängigkeiten
der Standardbibliothek werden zusätzlich aufgenommen. Die Originaltexte
von compiler-builtins, libm, stdarch und backtrace bleiben einzeln erhalten;
Compiler-/LLVM-Buildwerkzeuge werden nicht als App-Laufzeit behauptet.

Die Rust-Standardbibliothek ist ein weiterer statischer Bestandteil, den die
32 Registry-Einträge allein nicht erfassen. Sowohl das Windows- als auch das
Linux-Wheel enthalten Pfade mit dem Compiler-Commit
`88d9e12ae178fab0fb5cc050a94da85685d449ea`. Der automatische Audit gleicht diesen
Commit mit dem offiziellen Rust-1.98.0-Quellenmanifest ab und erhält dessen
`rust-src`, die originalen Texte `LICENSE-MIT`, `LICENSE-APACHE`, `COPYRIGHT`
sowie die externen Quellen aus dem zugehörigen Standardbibliotheks-`Cargo.lock`.
Beide Plattformen verwenden damit denselben nachgewiesenen Quellenstand.

CFFI 2.1.1 hat im Windows-Wheel nur einen MIT-0-Text. Sein passendes `setup.py`
kompiliert jedoch die geänderten Quellen unter `src/c/libffi_x86_x64/` und
`win64.obj` statisch in `_cffi_backend`. Deren Originalköpfe enthalten normale
MIT-Bedingungen und eigene Red-Hat-/Autoren-Copyrights, die der Wheel-Text nicht
ersetzt. Deshalb werden diese Originaldateien aus dem verifizierten CFFI-Sdist
zusätzlich erhalten. Der genaue veränderte Code ist bereits in diesem Sdist;
ein fremdes neueres libffi-Archiv wäre kein passender Ersatz.

Das überprüfte CFFI-Sdist hat 530.807 Bytes und SHA-256
`dd31f52ea1086513bb9df30f8fcee9b8918323ae067a3d5b78bc826a000712be`.
`cffi-vendored-libffi-notices.json` bindet die aufgenommenen Originaldateien
an ihre einzelnen SHA-256-Werte. Das zusätzlich gebündelte `libffi-8.dll` stammt
von CPythons ctypes-Laufzeit und wird separat durch die Python-Abnahme behandelt.

## Weiterverwendung der Nachweise

`windows-runtime-manual-audit.json` enthält native Dateipfade, Hashes,
Importtabellen, die originalen SBOM-/Lizenzdateien, 37 bereits SHA-256-gebundene
Quellenreferenzen sowie die sieben Windows-Codec-Rezepte. Die Codec-Rezepte
standen beim manuellen Audit noch vor der Cloud-Quellenbeschaffung; deren
fehlende Archivprüfsummen werden nicht als abgeschlossener Download ausgegeben.
Der endgültige maschinenlesbare Release-Nachweis entsteht auf dem jeweiligen
Builder durch `packaging/runtime_wheels.py` und wird von der Quellenjob-
Zusammenführung beider Plattformen konsumiert.

Der erweiterte Quell-Selbsttest lief anschließend erneut im nativen Windows-Qt.
Seine neuen benannten Prüfungen für Formular-Export/Undo, gemessene
Komprimierung, Passwort-Retry/Abbruch/AES und reduzierte Bewegung bestanden.
Beleg: `../../work/pdf-vm/shared/upgrade-v06-20261021/results-release-pre01/smokeretry01/run-i2y_4ep7/result.json`.
Danach wurde der aktuelle Stand mit frischen, exakt gepinnten Windows-
Abhängigkeiten erneut nativ gebaut und als echte EXE geprüft. Alle 15 PDF-
Fälle ohne Skip und die vier neuen GUI-Abläufe bestanden. Der eingebettete
Python-Modulindex und der native Dateibaum enthielten weder das AVIF-Plugin
noch `_avif` oder `_imagingft`. Auch die Seitenreihenfolge und Drehung der
Exports dieser EXE wurden nochmals mit Poppler bestätigt.

Dieser zusätzliche Build bleibt lokal und unveröffentlicht; die vorherige
Entwicklungs-ZIP wurde dafür nicht ersetzt. Der getrennte öffentliche Release-
Builder muss seine vollständigen Notices und Quellenmanifeste selbst erzeugen
und den entsprechenden finalen Release-Selbsttest bestehen.

- EXE SHA-256: `1b60878cb1866ce458dac51c2a9712ddebca78931e202771b28583d03b0bba0b`.
- Nativer Bericht: `../../work/pdf-vm/shared/upgrade-v06-20261021/results-release-final02/frozen/run-biul4__d/result.json`.
- Ausschlussnachweis: `../../work/pdf-vm/shared/upgrade-v06-20261021/runtime-audit/release-final02-bundle-verification.json`.
- Aktueller Quellenabgleich: `../../work/pdf-vm/shared/upgrade-v06-20261021/results-release-final02/current-source-verification.json`.
