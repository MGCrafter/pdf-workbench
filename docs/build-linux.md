# Pakete bauen

Für die fertige App ist keine Python-Installation nötig. Wer selbst bauen
möchte, braucht Python 3.14.8 und baut direkt auf dem Zielsystem; PyInstaller
übersetzt keine Windows-App unter Linux oder umgekehrt.

## Release-Build

Die gleichen Versionen werden im [Release-Workflow](../.github/workflows/release.yml)
auf Windows Server 2022 und Ubuntu 22.04 verwendet:

```sh
python -m pip install -r packaging/requirements-release.txt
python -m unittest discover -s tests -v
python -m pip uninstall --yes reportlab Pillow
python packaging/collect_notices.py --output build/release-notices
python packaging/build.py --release --notices build/release-notices
```

Die beiden Testbibliotheken werden nur aus der dafür angelegten Buildumgebung
entfernt. Eine virtuelle Umgebung verwenden, nicht die eigenen Systempakete.

Unter Linux anschließend in einer grafischen Sitzung oder mit Xvfb:

```sh
xvfb-run -a dist/PDFWorkbench/PDFWorkbench --self-test --output build/frozen-smoke
python packaging/collect_notices.py --output build/release-notices --verify-bundle dist/PDFWorkbench
python packaging/release.py --bundle dist/PDFWorkbench --notices build/release-notices --test-results build/frozen-smoke --output release-assets
```

Unter Windows wird `PDFWorkbench.exe` gestartet. Der Workflow wartet ausdrücklich
auf dessen Ende und prüft den gespeicherten Testbericht, bevor er das ZIP erstellt.

Das Archiv enthält den gesamten Ordner `PDFWorkbench/`. `_internal` gehört zur
App und darf nicht vom Programm getrennt werden. `BUILD-INFO.json` nennt den
Quellcommit und die verwendeten Versionen, `START_HERE.txt` erklärt den Start.

## Plattformen

Windows-Downloads sind für x86_64. Sie sind derzeit nicht signiert.
Linux-Downloads entstehen auf Ubuntu 22.04 mit glibc 2.35. Ältere glibc-Versionen
und musl-Systeme wie Alpine werden damit nicht unterstützt. Andere Distributionen
benötigen passende Grafik-/X11-/Wayland-Bibliotheken; eine vollständige Liste
aller Linux-Distributionen wird nicht als getestet ausgegeben.

Für einen neuen Release die Projektversion und die Änderungen aktualisieren,
committen und einen dazu passenden Tag, beispielsweise `v0.3.1`, pushen.
Erst wenn beide nativen Builds, Tests und die Sammlung der Drittanbieterquellen
erfolgreich sind, veröffentlicht der Workflow die Downloads und `SHA256SUMS`.
Die vollständigen Quellen der gebündelten Abhängigkeiten stehen als eigene
Release-Dateien bereit; sie werden zum normalen Start der App nicht benötigt.

Für einen lokalen Entwicklungsbuild ohne Distributionspaket genügt
`python packaging/build.py`. Weitere Hinweise stehen in
[distribution.md](distribution.md).
