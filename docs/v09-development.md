# Version 0.9: Funktionen und Grenzen

Die neuen Werkzeuge arbeiten lokal. Installation, native Pakete und Updates
sind in der [README](../README.md) beschrieben.

## Dokument-Tabs

**Datei → Neuer Dokument-Tab** oder **Strg+T** öffnet einen unabhängigen
Arbeitsbereich. **Strg+Tab** und **Strg+Umschalt+Tab** wechseln die Dokumente,
**Strg+W** schließt den aktuellen Tab. **Neuer Arbeitsbereich** setzt weiterhin
nur den aktiven Arbeitsbereich nach einer Speicherabfrage zurück.

Jeder Tab besitzt eigene Seiten, Objekte, Lesezeichen, Undo-/Redo-Schritte,
Projektdatei, Projektpasswort und automatische Sicherung. **Projekt in neuem
Tab öffnen** erhält den bisherigen Arbeitsbereich. Die Befehle **Auswahl in
anderen Tab kopieren/verschieben** übertragen ausgewählte Seiten einschließlich
Drehung, eigener Objekte und der zugehörigen Lesezeichen. Rückgängig wirkt in
beiden Tabs unabhängig. Das Fenster fragt beim Schließen alle ungespeicherten
Tabs ab; ein Programmupdate kann mehrere gespeicherte Projekte wieder öffnen.
Projektpasswörter werden danach erneut abgefragt.

## Scans, Export und Stapelverarbeitung

Das Untermenü **Werkzeuge → Scans, Export und Stapelverarbeitung** enthält:

- **Scans aufbereiten:** Auswahl oder alle Seiten begradigen, nach optionaler
  lokaler Tesseract-Ausrichtung drehen und Hintergrund aufhellen. Der Dialog
  nennt ausdrücklich die Umwandlung in Bildseiten: vorhandener Text, Formular-
  und Objektbearbeitung sowie Links können dadurch verloren gehen. Die Änderung
  bleibt im Projekt rückgängig machbar; die Originale werden nicht verändert.
- **Leere und doppelte Seiten prüfen:** visuell erkannte Kandidaten zur manuellen
  Kontrolle. Keine Seite wird automatisch zum Löschen ausgewählt. Ein Scan mit
  schwachen Inhalten kann leer wirken; die Auswahl liegt beim Nutzer.
- **Vorder-/Rückseiten verschachteln:** zwei Seitenbereiche abwechselnd anordnen,
  Rückseiten bei Bedarf umkehren und drehen. Eine Vorschau zeigt die neue Folge.
- **Seiten als PNG/JPEG exportieren:** Auswahl oder alle sichtbaren Seiten mit
  Drehung, Beschnitt und Anmerkungen; Auflösung und JPEG-Qualität einstellen.
- **Text exportieren:** auslesbaren PDF-Text und eigene Textobjekte in eine neue
  UTF-8-Datei schreiben. Bildseiten benötigen vorher OCR.
- **Klickbares Inhaltsverzeichnis exportieren:** Lesezeichen mit Unterkapiteln
  in vorgeschaltete Inhaltsseiten mit internen Zielen umwandeln.
- **Interne Links exportieren:** Bereiche auf einer Vorschau oder über genaue
  Koordinaten setzen und auf andere Arbeitsseiten verlinken. Einträge lassen
  sich vor dem Export ändern oder löschen. Diese Links gehören zum neuen Export,
  nicht zu den gespeicherten Projektobjekten. Erneutes Importieren und allgemeine
  spätere Seitenumbauten erhalten interne Links nicht garantiert.
- **Stapelverarbeitung und Vorlagen:** mehrere PDF-Dateien mit wiederverwendbaren
  Einstellungen bearbeiten. Die Vorlage umfasst Komprimierung, Drehung,
  Wasserzeichen, Seitennummern, Scan-Aufbereitung und optionale deutsche/englische
  OCR. Vorlagen lassen sich benennen, lokal speichern und als JSON importieren
  oder exportieren. Bestehende Zielnamen werden abgewiesen; ein Abbruch entfernt
  nur unveränderte eigene Ausgaben und nennt zurückbehaltene Dateien.

## Text, Schwärzung, Signaturen und Archivierung

Das zweite neue Untermenü unter **Werkzeuge** enthält:

- **Inhalte dauerhaft schwärzen:** Bereiche pro Seite markieren, Auflösung und
  Datenschutzbestätigung wählen, als neue PDF speichern. Die Ausgabe wird aus
  den sichtbaren geschwärzten Bildseiten neu aufgebaut. Dadurch enthält sie
  keine ursprüngliche Textschicht, Formulare, Kommentare, Anhänge, Metadaten oder
  alten PDF-Revisionen. Suchbarkeit und Interaktivität gehen verloren; OCR kann
  später auf der bereits geschwärzten Datei neu ausgeführt werden. Originale,
  Projekte, Autosaves und Undo-Historie bleiben unverändert und enthalten die
  ursprünglichen Informationen. Zuschneiden bleibt eine andere Funktion.
- **Bestehenden PDF-Text bearbeiten:** einen unterstützten Textabschnitt der
  aktiven Seite auswählen und seinen Inhalt ersetzen. Der alte Abschnitt wird
  entfernt und der Ersatz als Seiteninhalt eingefügt. Unterstützt werden
  geeignete horizontale Abschnitte mit bekannten PDF-Basisschriften; komplexe
  Codierungen, Überlappungen, Transformationen oder zu breite Ersatztexte werden
  ausdrücklich abgewiesen. Seiten mit Formularfeldern sowie Quellen mit internen
  oder benannten Links werden vor der Änderung zurückgewiesen, damit diese
  Strukturen erhalten bleiben. Das ist keine vollständige Textverarbeitung und keine
  Datenschutzbereinigung: Original und Undo-Historie behalten den alten Inhalt.
- **Mit Zertifikat signierte PDF speichern:** ein eigenes PFX-/P12-Zertifikat
  mit privatem Schlüssel entsperren und eine neue PDF unsichtbar kryptografisch
  signieren. Das Zertifikat muss zeitlich gültig sein und eine passende
  Schlüsselnutzung erlauben. Anschließend wird die Signaturintegrität geprüft.
  Passwort und Schlüssel werden nicht in Projekten oder Einstellungen gespeichert.
- **Zertifikatssignaturen einer Datei prüfen:** die unveränderte Originaldatei
  offline prüfen; optional eigene PEM-Vertrauenszertifikate wählen. Der Bericht
  trennt kryptografische Integrität, Änderung nach der Signatur und Vertrauen.
  Ohne explizite Vertrauensbasis wird kein vertrauenswürdiger Unterzeichner
  behauptet. Sperrstatus, vertrauenswürdiger Zeitstempel und qualifizierte
  elektronische Signaturen werden nicht bestätigt. Vorhandene Zertifikatssignaturen
  bleiben bei normaler PDF-Bearbeitung und Neu-Export nicht gültig erhalten.
- **Validierte PDF/A-2b speichern:** lokal installiertes Ghostscript und die
  veraPDF-CLI auswählen; veraPDF benötigt eine passende lokale Java-Laufzeit.
  Die Konvertierung kann Formulare verflachen, Schriften ersetzen und Farben
  verändern. Die neue Ausgabe wird erst gespeichert, wenn der tatsächliche
  veraPDF-Prüfbericht PDF/A-2b bestätigt und die Seitenanzahl stimmt. Ein normaler
  PDF-Export bietet diese Konformitätszusage nicht.

## Lokale Abhängigkeiten und Verteilung

Die Python-Installation übernimmt zusätzlich PyMuPDF und pyHanko; unter Windows
wird die Zeitzonendatenbank tzdata benötigt. Tesseract bleibt für OCR und die
automatische Scanausrichtung optional. Ghostscript, veraPDF und Java werden
nicht heimlich heruntergeladen oder zusammen mit Dokumenten an Cloud-Dienste
geschickt. Die App arbeitet weiter lokal und ohne Konto.

PyMuPDF/MuPDF wird unter GNU AGPLv3 eingesetzt. Die Lizenzhinweise beschreiben
die Bedingungen des kombinierten freien Programms. Die Release-Nachweise
verlangen die Originalhinweise und passenden Quellen der tatsächlich
gebündelten nativen Laufzeitkomponenten; die alten 0.8-Manifeste genügen dafür nicht.

macOS ist weiterhin nicht nativ getestet.
