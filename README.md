# PDF Workbench

PDFs zusammenfügen, Seiten sortieren und schnell etwas dazuschreiben.
PDF Workbench ist eine freie Desktop-App für Windows und Linux. Sie arbeitet
lokal, ohne Konto und ohne Upload deiner Dokumente.

**Version 0.9.0** ergänzt getrennte
Dokument-Tabs mit Seitenübertragung, Stapelvorlagen, Scan-Aufbereitung,
Prüfung leerer/doppelter Seiten, Vorder-/Rückseiten-Sortierung, Bild-/Textexport,
klickbare Inhaltsverzeichnisse und interne Links. Dazu kommen dauerhafte
Schwärzung als neue Bild-PDF, begrenzte Bearbeitung bestehenden PDF-Texts,
Offline-Zertifikatssignaturen und mit veraPDF validierter PDF/A-2b-Export.
Bedienung und Grenzen stehen in [Ausbau 0.9](docs/v09-development.md).

## Download

| System | Download für v0.9.0 |
| --- | --- |
| Windows 64-Bit | [ZIP herunterladen](https://github.com/MGCrafter/pdf-workbench/releases/download/v0.9.0/PDFWorkbench-v0.9.0-windows-x86_64.zip) |
| Linux 64-Bit | [tar.gz herunterladen](https://github.com/MGCrafter/pdf-workbench/releases/download/v0.9.0/PDFWorkbench-v0.9.0-linux-x86_64.tar.gz) |

Alle Versionen, Prüfsummen und Hinweise zu den Paketen findest du unter
[Releases](https://github.com/MGCrafter/pdf-workbench/releases).
Quellen und Prüfberichte liegen gebündelt im
[getrennten Quellenarchiv](https://github.com/MGCrafter/pdf-workbench/releases/tag/source-archives).
macOS ist noch nicht getestet und hat derzeit kein Downloadpaket.

**Windows:** Das ZIP vollständig entpacken und im entpackten Ordner
`PDFWorkbench.exe` öffnen. Die mitgelieferten Dateien müssen im selben Ordner
bleiben.
Unter Windows darf der Programmordner kein Semikolon (`;`) enthalten;
Umlaute und Leerzeichen funktionieren.

**Linux:** Das Archiv entpacken und die App starten:

```sh
tar -xzf PDFWorkbench-v0.9.0-linux-x86_64.tar.gz
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
- Eigene Textfelder, Bilder, Stempel, Häkchen und Kreuze einfügen.
- Objekte auswählen, verschieben, vergrößern, kopieren und wieder bearbeiten.
- Nach Text suchen und PNG-/JPEG-Bilder in PDF-Seiten umwandeln.
- Scans mit optionalem Tesseract lokal durchsuchbar machen.
- Seiten als Miniaturen ansehen und in der Vorschau zoomen und verschieben.
- Helle oder dunkle Oberfläche; auf Wunsch passend zur Systemeinstellung.
- Ab 0.6: Komprimieren mit Größenvergleich, geschützte PDFs öffnen und mit
  AES-256 speichern, normale AcroForms ausfüllen.
- Ab 0.7: PDF-Versionen mit Text- und Bildänderungen nebeneinander vergleichen.
- Interaktive Textfelder, Kontrollkästchen, Dropdowns und Optionsgruppen gestalten.
- Wasserzeichen und Seitennummern mit Vorschau auf alle oder ausgewählte Seiten setzen.
- Kapitel als verschachtelte Lesezeichen verwalten, auch nach dem Umsortieren.
- Sichtbare Seitenränder zuschneiden oder proportional auf A4, Letter und eigene Formate setzen.
- Den gesamten Arbeitsbereich als `.pdfwb`-Projekt speichern, inklusive eingebetteter
  PDFs, eigener Objekte, Lesezeichen und Undo-/Redo-Historie; automatische lokale Sicherungen.
- Neue Programmversionen täglich prüfen und nach Bestätigung herunterladen und installieren.
- Mehrere Dokumente in getrennten Tabs bearbeiten und Seiten zwischen Tabs übertragen.
- Stapelvorlagen für wiederkehrende Aufgaben speichern und austauschen.
- Scans aufbereiten, leere/doppelte Seiten manuell prüfen und Duplexscans sortieren.
- Seiten als PNG/JPEG und auslesbaren Text als UTF-8 exportieren.
- Klickbare Inhaltsverzeichnisse und interne Linkbereiche für den Export erstellen.
- Einfache vorhandene Textabschnitte ersetzen und sensible Bereiche dauerhaft schwärzen.
- Neue PDFs mit eigenem PFX/P12-Zertifikat signieren und Originalsignaturen offline prüfen.
- PDF/A-2b mit lokalem Ghostscript erzeugen und vor dem Speichern mit veraPDF validieren.

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

**Deine Originale bleiben unverändert.** PDF-Exporte und neue Projekte brauchen
einen neuen Dateinamen. Ein geöffnetes Projekt lässt sich mit **Strg+S** aktualisieren;
zwischenzeitlich extern veränderte Dateien werden dabei geschützt.
Eigene Anmerkungen bleiben nach dem Speichern und erneuten Öffnen bearbeitbar.

Neue Textfelder und Bilder findest du in der Vorschau unter **Einfügen**.
Mit dem Pfeilwerkzeug kannst du eigene Objekte auswählen und verschieben;
die Eckpunkte ändern ihre Größe. Ein Doppelklick öffnet die Eigenschaften.
**Strg + F** öffnet die Suche. **Datei → Bilder zu PDF-Seiten …** übernimmt Fotos
als neue Seiten; **Datei → Durchsuchbare PDF erstellen (OCR) …** startet die
optionale Texterkennung. Die [OCR-Anleitung](docs/ocr.md) erklärt die Einrichtung.

Der hervorgehobene Rahmen zeigt die Seite in der Vorschau, die Häkchen zeigen die
Auswahl für die Werkzeuge. Mehr zur Bedienung steht in der
[kurzen Anleitung](docs/usage.md).

Unter **Ansicht → Darstellung** wählst du **System**, **Hell** oder **Dunkel**.
Die Auswahl bleibt beim nächsten Start erhalten. Die PDF-Seiten behalten ihre
Originalfarben.

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
| Projekt speichern / öffnen | Strg + `S` / Strg + Alt + `O` |
| Lesezeichen bearbeiten | Strg + Alt + `B` |
| Text suchen | Strg + `F`; Eingabe / Umschalt + Eingabe zum nächsten / vorigen Treffer |
| Eigenes Objekt bearbeiten | Doppelklick, F2 oder Eingabe im Pfeilwerkzeug |
| Objekt verschieben / abbrechen | Pfeiltasten; Umschalt für größere Schritte / Esc |

## Grenzen

Die App richtet sich an normale Dokumentseiten. Gezeichnete Unterschriften
sind sichtbare Zeichnungen, keine kryptografischen Zertifikatssignaturen.
Für kryptografische Signaturen gibt es einen getrennten PFX/P12-Befehl.
Bearbeitung und normaler Neu-Export erhalten vorhandene Zertifikatssignaturen
nicht gültig; die Offline-Prüfung verwendet deshalb die unveränderte Datei.

- PDFs mit Öffnungspasswort lassen sich entsperren und als neue Datei mit
  AES-256 schützen. Normale Exporte, Einzelseiten und OCR bleiben unverschlüsselt.
  Passwörter werden nicht in Einstellungen oder Projektdateien gespeichert. Geschützte
  Projekte behalten ihr Passwort bis zum Schließen nur im Arbeitsspeicher.
- Normale AcroForms mit Textfeldern, Kontrollkästchen, Einfachauswahl und
  Optionsgruppen werden unterstützt. XFA, Zertifikatssignaturen, Aktionsschaltflächen,
  Skripte, Berechnungen, Felder mit NoRotate-/NoZoom-Sonderverhalten sowie mehrdeutige
  oder nicht seitengebundene Felder werden
  abgewiesen. Geänderte und neue Felder verwenden schlichte Feldansichten;
  Pflichtfelder kennzeichnen keine geprüfte inhaltliche Vollständigkeit.
- Der Vergleich prüft sichtbare Seiten und auslesbaren Text, mit höchstens
  300 Seiten je Datei und 1.000 Pixeln an der längsten Kante. Er erkennt keine
  beliebig kleinen Änderungen und prüft keine Metadaten oder Zertifikate. Scans
  erhalten dabei keine automatische OCR.
- Wasserzeichen und Seitennummern werden zum Seiteninhalt. Nach erneutem Öffnen
  haben sie keine eigenen Bearbeitungsgriffe; vor dem Speichern gibt es Undo.
- Bildkomprimierung kann Details verlieren; spezielle Bildformate und Masken
  bleiben unverändert. Es gibt keine garantierte Größenersparnis.
- Textmarkierungen brauchen eine vorhandene Textschicht. Die optionale OCR kann
  sie für Scans ergänzen. Ab 0.9 lassen sich unterstützte bestehende Textabschnitte
  ersetzen; komplexe Schriften und Layouts werden abgewiesen. Neue Textfelder
  bleiben eigene Objekte.
- Sichere Lesezeichen auf enthaltene Seiten werden übernommen. Externe Aktionen,
  Skripte und genaue ursprüngliche Zoompositionen werden nicht kopiert. Anhänge,
  dokumentweite Metadaten und Tags werden nicht übernommen. Normale Exporte
  prüfen keine PDF/A-Konformität.
- Zuschneiden blendet Inhalte aus und ist **keine sichere Schwärzung**. Angeschnittene
  eigene Objekte können zu normalen PDF-Anmerkungen ohne Workbench-Bearbeitungsgriffe werden.
- Dauerhafte Schwärzung erzeugt eine neue Bild-PDF ohne ursprüngliche Textschicht
  oder Interaktivität. Originale, Projekte und Sicherungen enthalten weiterhin
  die ungeschwärzten Informationen. Scan-Aufbereitung wandelt Seiten ebenfalls in Bilder um.
- PDF/A-2b erfordert eigene lokale Installationen von Ghostscript, veraPDF und Java.
  Die Konvertierung kann Formulare verflachen und das Aussehen verändern;
  nur der ausdrücklich validierte PDF/A-Export prüft die Konformität.
- Vorhandene Seitenanmerkungen werden übernommen. Interne Verweise auf entfernte
  oder umsortierte Seiten sind nicht vollständig unterstützt.

Die Oberfläche ist derzeit auf Deutsch. Welche Plattformen und Funktionen
getestet werden, steht unter [Tests](docs/testing.md).

## Aus dem Quellcode starten

Benötigt wird Python 3.11 oder neuer. Die Installation übernimmt PyQt6, pypdf,
Pillow, cryptography, PyMuPDF und pyHanko samt Abhängigkeiten. Im Projektordner:

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
