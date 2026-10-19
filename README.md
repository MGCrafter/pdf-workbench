# PDF Workbench

PDFs zusammenfügen, Seiten sortieren und schnell etwas dazuschreiben.
PDF Workbench ist eine freie Desktop-App für Windows und Linux. Sie arbeitet
lokal, ohne Konto und ohne Upload deiner Dokumente.

![PDF Workbench mit drei Seiten, einer Textmarkierung, einem Kommentar und einer gezeichneten Unterschrift](docs/assets/screenshot.png)

## Download

| System | Download für v0.3.0 |
| --- | --- |
| Windows 64-Bit | [ZIP herunterladen](https://github.com/MGCrafter/pdf-workbench/releases/download/v0.3.0/PDFWorkbench-v0.3.0-windows-x86_64.zip) |
| Linux 64-Bit | [tar.gz herunterladen](https://github.com/MGCrafter/pdf-workbench/releases/download/v0.3.0/PDFWorkbench-v0.3.0-linux-x86_64.tar.gz) |

Alle Versionen, Prüfsummen und Hinweise zu den Paketen findest du unter
[Releases](https://github.com/MGCrafter/pdf-workbench/releases).
macOS ist noch nicht getestet und hat derzeit kein Downloadpaket.

**Windows:** Das ZIP vollständig entpacken und im entpackten Ordner
`PDFWorkbench.exe` öffnen. Die mitgelieferten Dateien müssen im selben Ordner
bleiben.

**Linux:** Das Archiv entpacken und die App starten:

```sh
tar -xzf PDFWorkbench-v0.3.0-linux-x86_64.tar.gz
cd PDFWorkbench
./PDFWorkbench
```

Die fertigen Pakete enthalten Python und die benötigten Bibliotheken.
Du brauchst dafür keine eigene Python-Installation. Unterstützte und getestete
Linux-Distributionen stehen in den jeweiligen Release-Hinweisen.

## Was die App kann

- Mehrere PDFs in einer Datei zusammenfügen.
- Seiten sortieren, drehen, duplizieren oder entfernen.
- Eine Auswahl gemeinsam oder jede ausgewählte Seite einzeln speichern.
- Kommentare und Textmarkierungen hinzufügen, bearbeiten und löschen.
- Eine Unterschrift zeichnen und auf der Seite platzieren.
- Seiten als Miniaturen ansehen und in der Vorschau zoomen und verschieben.

## Loslegen

1. **PDFs hinzufügen …** anklicken oder Dateien ins Fenster ziehen.
2. Die gewünschten Seiten auswählen. Mit **Strg** und **Umschalt** kannst du
   mehrere auswählen; **Bereich …** versteht Angaben wie `1-3, 5, 8`.
3. Seiten mit der Maus oder den Pfeiltasten in der Werkzeugleiste anordnen.
   Drehen, Duplizieren und Entfernen wirken auf die Auswahl.
4. In der Vorschau Kommentare setzen, über Text ziehen, um ihn zu markieren,
   oder eine gezeichnete Unterschrift einfügen. Unter **Anmerkungen …** lassen
   sich diese Einträge später ändern oder löschen.
5. **PDF speichern …** speichert alle Seiten als neue PDF. **Auswahl …** speichert
   nur die ausgewählten Seiten; **Einzelseiten …** legt einzelne PDFs in einem
   neuen Unterordner an.

**Deine Originale bleiben unverändert.** Die App überschreibt auch keine
vorhandenen Ausgabedateien. Wähle zum Speichern einen neuen Dateinamen.
Eigene Anmerkungen bleiben nach dem Speichern und erneuten Öffnen bearbeitbar.

Der blaue Rahmen zeigt die Seite in der Vorschau, die Häkchen zeigen die
Auswahl für die Werkzeuge. Mehr zur Bedienung steht in der
[kurzen Anleitung](docs/usage.md).

## Maus und Tastatur

| Aktion | Bedienung |
| --- | --- |
| Ansicht verschieben | Handwerkzeug und linke Maustaste; mittlere Maustaste in jedem Werkzeug |
| Horizontal scrollen | Seitliches Mausrad, Touchpad oder Umschalt + Mausrad |
| Zoomen | Strg + Mausrad oder Strg + `+` / `-` |
| Ganze Seite / Seitenbreite | Strg + `0` / Strg + `1` |
| Seiten verschieben | Alt + Pfeil hoch / runter |
| Links / rechts drehen | Strg + `L` / Strg + `R` |
| Duplizieren / entfernen | Strg + `D` / Entf |
| Rückgängig | Strg + `Z` |

## Grenzen

Die App richtet sich an normale Dokumentseiten. Gezeichnete Unterschriften
sind sichtbare Zeichnungen, keine kryptografischen Zertifikatssignaturen.

- Verschlüsselte PDFs, interaktive Formulare und mit Zertifikaten signierte
  Dateien werden derzeit abgewiesen.
- Textmarkierungen brauchen eine vorhandene Textschicht. OCR und das Bearbeiten
  bestehender PDF-Texte sind nicht enthalten.
- Dokumentweite Lesezeichen, Anhänge, Metadaten und Tags werden beim Export
  nicht übernommen. PDF/A-Konformität ist nicht garantiert.
- Vorhandene Seitenanmerkungen werden übernommen. Interne Verweise auf entfernte
  oder umsortierte Seiten sind nicht vollständig unterstützt.

Die Oberfläche ist derzeit auf Deutsch. Welche Plattformen und Funktionen
getestet werden, steht unter [Tests](docs/testing.md).

## Aus dem Quellcode starten

Benötigt werden Python 3.11 oder neuer, PyQt6 und pypdf. Im Projektordner:

**Linux:**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python run-local.py
```

**Windows, PowerShell:**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe run-local.py
```

Nach der Installation steht auch der Befehl `pdf-workbench` in der virtuellen
Umgebung zur Verfügung. PDF-Dateien können beim Start als Argumente mitgegeben
werden.

Für die Entwicklung:

```sh
python -m pip install '.[test,build]'
python -m unittest discover -s tests -v
python packaging/build.py
```

Hinweise zum [Bauen unter Linux](docs/build-linux.md) und zu den
[Tests](docs/testing.md) stehen in der Dokumentation.

## Fehler melden

Bei Problemen hilft ein [Issue](https://github.com/MGCrafter/pdf-workbench/issues)
mit Betriebssystem, App-Version und den Schritten zum Nachstellen. Bitte keine
vertraulichen PDFs hochladen; ein kleines Beispieldokument reicht meist aus.

## Lizenz

PDF Workbench steht unter [GPL-3.0-or-later](LICENSE).
Die Lizenzen von PyQt6, Qt, pypdf und weiteren mitgelieferten Komponenten stehen
in den [Drittanbieterhinweisen](THIRD_PARTY_NOTICES.md) und unter [licenses/](licenses/).
Das Projekt ist unabhängig von Adobe.
