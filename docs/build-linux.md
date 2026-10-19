# Pakete unter Linux bauen

Der [Release-Workflow](../.github/workflows/release.yml) baut mit Python 3.14.8
auf Ubuntu 22.04 und Windows Server 2022. Die folgenden Schritte entsprechen
dem Linux-Build. PyInstaller muss auf dem Zielsystem laufen; ein Windows-Paket
lässt sich damit nicht unter Linux bauen.

## Vorbereitung

Auf Ubuntu 22.04 werden diese Pakete für Qt, die PDF-Prüfungen und Xvfb benötigt:

```sh
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  xvfb xauth poppler-utils libegl1 libgl1 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-render-util0 libxcb-shape0 libxcb-xinerama0
```

Python 3.14.8 muss bereits installiert sein. Im frisch ausgecheckten Projekt
eine eigene Buildumgebung anlegen. Quellcode, Paketversion und Buildskripte
müssen committed sein; der Release-Build lehnt nicht committete Änderungen an
diesen Dateien ab.

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install -r packaging/requirements-release.txt
```

Wie im Workflow vorab prüfen, ob das X11-Plugin alle Systembibliotheken findet:

```sh
plugin=$(python -c "from PyQt6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath) + '/platforms/libqxcb.so')")
ldd "$plugin"
if ldd "$plugin" | grep -q 'not found'; then exit 1; fi
```

## Bauen und prüfen

Zuerst die Quelltests ausführen. Anschließend werden die Testbibliotheken nur
aus dieser virtuellen Umgebung entfernt, damit sie nicht im App-Paket landen.

```sh
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
python -m pip uninstall --yes reportlab Pillow
python packaging/collect_notices.py --output build/release-notices
python packaging/build.py --release --notices build/release-notices
```

Nach dem Build werden die tatsächlich gebündelten Linux-Bibliotheken ihren
Quellpaketen zugeordnet. Das aktualisierte Lizenzverzeichnis muss danach auch
ins App-Paket kopiert werden, bevor dessen Prüfung und Abnahme laufen:

```sh
python packaging/collect_notices.py --output build/release-notices --native-toc build/PDFWorkbench/Analysis-00.toc
python -c "import shutil; shutil.copytree('build/release-notices', 'dist/PDFWorkbench/_internal/licenses', dirs_exist_ok=True)"
python packaging/collect_notices.py --output build/release-notices --verify-bundle dist/PDFWorkbench
xvfb-run -a dist/PDFWorkbench/PDFWorkbench --self-test --output build/frozen-smoke
python packaging/release.py --bundle dist/PDFWorkbench --notices build/release-notices --test-results build/frozen-smoke --output release-assets
```

Das Paketieren verlangt genau einen erfolgreichen nativen Testbericht für den
aktuellen Build. Ein Bericht für eine andere Programmdatei oder einen anderen
Quellstand wird abgewiesen. Für einen neuen Versuch frische Build- und
Testverzeichnisse verwenden.

Das Archiv enthält den gesamten Ordner `PDFWorkbench/` einschließlich
`_internal`. `BUILD-INFO.json` nennt Quellcommit und verwendete Versionen.
Ein lokaler Entwicklungsbuild ohne Release-Paket ist mit
`python packaging/build.py` möglich.

## Plattformen und Veröffentlichung

Linux-Pakete werden auf Ubuntu 22.04 mit glibc 2.35 gebaut. Ältere glibc-Versionen
und musl-Systeme wie Alpine werden damit nicht unterstützt. Andere Distributionen
benötigen passende Grafik-/X11-/Wayland-Bibliotheken. Windows-Pakete sind für
x86_64 und derzeit nicht signiert; der Windows-Workflow prüft die native
`PDFWorkbench.exe`, bevor er das ZIP erstellt.

Für einen Release die Projektversion und Änderungen committen und einen passenden
Tag, beispielsweise `v0.3.1`, pushen. Erst nach beiden nativen Builds, Tests und
der Sammlung der Drittanbieterquellen veröffentlicht der Workflow die App-Pakete,
Anwendungs- und Abhängigkeitsquellen sowie `SHA256SUMS`. Weitere Hinweise zur
Quellbereitstellung stehen in [distribution.md](distribution.md).
