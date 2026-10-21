# Bedienung

## Dateien und Seiten

**PDFs hinzufügen …** öffnet eine oder mehrere Dateien. Du kannst PDFs auch
direkt ins Fenster ziehen. Die Seiten erscheinen zunächst in Importreihenfolge.
Über den Raster- und Listenschalter wechselst du zwischen den Miniaturansichten.

Der hervorgehobene Rahmen markiert die aktuelle Vorschauseite. Häkchen und getönte Fläche
zeigen die Auswahl für die Werkzeuge. Mit Strg und Umschalt lassen sich mehrere
Seiten auswählen. **Bereich …** versteht beispielsweise `1-3, 5, 8-10`; die
Nummern beziehen sich auf die aktuelle Reihenfolge. Wiederholte Angaben werden
entfernt, die Auswahl bleibt in Dokumentreihenfolge.

Zum Sortieren eine Seite oder Auswahl an die gewünschte Stelle ziehen. Eine
Einfügelinie zeigt das Ziel. Am oberen oder unteren Rand scrollt die Liste
weiter. Alternativ verschieben die Pfeile in der Werkzeugleiste oder
Alt + Pfeil hoch / runter die Auswahl.

**Drehen**, **Duplizieren** und **Entfernen** wirken auf die ausgewählten Seiten.
Eine Kopie steht direkt hinter ihrer Originalseite und übernimmt deren Drehung
und Anmerkungen. Kopien lassen sich anschließend getrennt bearbeiten.

## Vorschau

Mit **+**, **-**, dem Prozentfeld, **Seite** und **Breite** passt du die Ansicht an.
Strg + Mausrad zoomt. Seitliches Mausrad, Touchpad oder Umschalt + Mausrad
scrollen horizontal. Das Handwerkzeug verschiebt die Ansicht mit gedrückter
linker Maustaste. Die mittlere Maustaste funktioniert zum Verschieben in jedem
Werkzeug, auch während du Kommentare oder Markierungen ergänzt.

**Vorschau** blendet den rechten Bereich aus oder wieder ein, wenn du mehr
Platz für die Seitenübersicht brauchst.

## Kommentare, Markierungen und Unterschriften

Die Werkzeuge dafür findest du unter den Zoomknöpfen und im Menü **Anmerkungen**.

- **Kommentar:** Das Werkzeug wählen, auf eine Stelle der Seite klicken und
  den Text eingeben.
- **Text markieren:** Über vorhandenen PDF-Text ziehen. Die Markierung folgt
  den ausgewählten Textzeilen. Scans ohne Textschicht lassen sich so nicht
  markieren. Mit der optionalen Texterkennung lässt sich zuerst eine
  durchsuchbare PDF erstellen und anschließend öffnen.
- **Gezeichnete Unterschrift:** Im Dialog mit gedrückter linker Maustaste zeichnen.
  **Neu zeichnen** leert die Fläche. Danach auf die PDF klicken, um die Zeichnung
  in Standardgröße zu platzieren, oder einen Bereich für die gewünschte Größe
  ziehen. **Abbrechen** übernimmt nichts.

Eine gezeichnete Unterschrift ist eine sichtbare Zeichnung. Sie ist keine
kryptografische Zertifikatssignatur und bestätigt die Echtheit der Datei nicht.

**Anmerkungen (Anzahl) …** zeigt die eigenen Einträge auf der aktuellen Seite.
**Bearbeiten …** ändert Kommentar- oder Markierungstext beziehungsweise öffnet
die Zeichenfläche für eine neue Unterschrift. **Löschen** entfernt den gewählten
Eintrag. Die Änderungen sind sofort in Vorschau und Miniaturen sichtbar.

Eigene Anmerkungen bleiben nach dem Speichern und erneuten Öffnen bearbeitbar.
Die Anmerkungsliste verwaltet nur eigene Einträge. Anmerkungen aus anderen
PDF-Programmen bleiben im Dokument und werden beim Export übernommen.

## Speichern und Rückgängig

**PDF speichern …** speichert alle Seiten in ihrer aktuellen Reihenfolge,
einschließlich zusätzlicher Drehungen und Anmerkungen, als neue PDF.
**Auswahl …** speichert nur die ausgewählten Seiten gemeinsam.
**Einzelseiten …** erstellt für jede ausgewählte Seite eine nummerierte PDF in
einem neuen Unterordner. Der Dialog zeigt vorab den Zielpfad.

Die Originaldateien bleiben unverändert. Bestehende Ziele werden nicht
überschrieben; zum Speichern immer einen neuen Dateinamen wählen.

Bis zu 50 Bearbeitungsschritte können rückgängig gemacht oder wiederholt werden.
Der Stern im Fenstertitel zeigt Änderungen, die noch nicht vollständig exportiert
wurden. Eine gespeicherte PDF enthält die Seiten und Anmerkungen, aber keine
Workbench-Sitzung mit Undo-Verlauf oder der ursprünglichen Aufteilung in Quelldateien.

## Tastenkürzel

| Aktion | Tastenkürzel |
| --- | --- |
| PDFs hinzufügen | Strg + O |
| Neuer Arbeitsbereich | Strg + N |
| Alles auswählen | Strg + A |
| Bereich auswählen | Strg + G |
| Seiten verschieben | Alt + Pfeil hoch / runter |
| Links / rechts drehen | Strg + L / Strg + R |
| Duplizieren | Strg + D |
| Entfernen | Entf |
| Rückgängig / wiederholen | Strg + Z / Strg + Umschalt + Z |
| Vergrößern / verkleinern | Strg + Plus / Strg + Minus |
| Ganze Seite / Seitenbreite | Strg + 0 / Strg + 1 |

Die Menüs zeigen die auf deinem Betriebssystem verwendeten Tastenkürzel an.

## Darstellung

Unter **Ansicht → Darstellung** gibt es **System**, **Hell** und **Dunkel**.
Die App speichert die Auswahl. Mit **System** folgt die Oberfläche dem hellen
oder dunklen Erscheinungsbild des Betriebssystems, auch wenn es während der
Benutzung wechselt. Ein manueller Wechsel verändert weder die PDF-Farben noch
Zoom, Scrollposition oder den Verlauf deiner Änderungen.

**Ansicht → Bewegung reduzieren** schaltet die kurzen Übergänge ab. Diese
Einstellung bleibt gespeichert. Unter Windows und GNOME berücksichtigt die App
auch erkannte Systemeinstellungen für reduzierte Bewegung.

## Komprimieren und Passwortschutz

Unter **Werkzeuge → PDF komprimieren …** stehen drei Stufen bereit:
verlustfreie Optimierung sowie **Ausgewogen** und **Klein**. Die beiden letzteren
können normale RGB- und Graustufenbilder verkleinern und als JPEG neu speichern;
die längste Kante ist dann höchstens 2.200 beziehungsweise 1.400 Pixel lang.
Text und Vektoren werden nicht gerastert. Maskierte, farbprofilabhängige und
andere spezielle Bilder bleiben unverändert. Das kann die Ersparnis begrenzen.

Der Größenvergleich berechnet einen Probeexport im Arbeitsspeicher. Er vergleicht
die aktuelle Seitenanordnung mit und ohne Komprimierung, nicht die Summe der
Quelldateigrößen. Dateiname und Metadaten können die endgültige Größe geringfügig
ändern. Nach dem Speichern zeigt die Statuszeile die tatsächlichen Größen.
Prüfe bei Bildkomprimierung das Erscheinungsbild vor der Weitergabe. Die App
speichert das kleinere Ergebnis und erstellt immer eine neue Datei.

Bei einer geschützten PDF fragt die App nach dem Öffnungspasswort. Ein falsches
Passwort lässt sich erneut eingeben; Abbrechen lässt bereits importierte Dateien
im Arbeitsbereich. Die entschlüsselten Dokumentdaten liegen während der Sitzung
im Speicher. Das Passwort wird nicht in Einstellungen oder Quelldaten-Snapshots
gespeichert.

**Werkzeuge → Mit Passwort geschützt speichern …** speichert alle Arbeitsseiten mit
AES-256-Verschlüsselung in einer neuen PDF. Das Passwort muss zweimal eingegeben
werden. Normales Speichern, Einzelseiten, Komprimieren und OCR erzeugen
unverschlüsselte Dateien; der Passwortschutz einer Quelle wird nicht automatisch
übernommen. Es werden keine getrennten Druck- oder Bearbeitungsrechte vergeben.

## Vorhandene PDF-Formulare ausfüllen

Eine Seite der gewünschten Quelldatei aktivieren und **Werkzeuge → Formular
ausfüllen …** wählen. Der Dialog zeigt alle unterstützten Felder dieser Quelle:
normale Textfelder, Kontrollkästchen und Einfachauswahl. Die Änderung gilt auch
für ihre duplizierten Arbeitsseiten und lässt sich gemeinsam rückgängig machen.
Erst **PDF speichern …** schreibt sie in eine neue Datei. Unterstützte Felder
bleiben nach Export und Wiederöffnen ausfüllbar; bei zusammengefügten Formularen
werden kollidierende Namen eindeutig umbenannt.

Die App erzeugt beim Ausfüllen beziehungsweise bei einer angeforderten
Neudarstellung eigene Feldansichten aus den Formularwerten. Normale
Hintergrundfarben und rechteckige Rahmen bleiben erhalten; aufwendige
Dekorrahmen oder spezielle Schrifteffekte können vereinfacht werden.

Schreibgeschützte Felder und Längenbegrenzungen werden berücksichtigt. Wenn die
PDF-Schrift ein eingegebenes Zeichen nicht darstellen kann, lehnt die App die
Änderung ab. XFA, Signaturfelder, Skripte und Berechnungen, Optionsschalter,
Aktionsschaltflächen, Passwort- und Richtextfelder, editierbare Auswahl und
Mehrfachauswahl sowie mehrdeutige oder nicht seitengebundene Felder gehören noch
nicht zum unterstützten Umfang. Das Erstellen neuer interaktiver Felder ist
ebenfalls noch nicht verfügbar.

## Textfelder, Bilder und Stempel

Unter **Einfügen** in der Vorschau wählst du Textfeld, Bild, Bildstempel,
Häkchen oder Kreuz. Ein Klick setzt einen Standardrahmen, Ziehen legt den
gewünschten Bereich fest. Textfelder unterstützen mehrere Zeilen, Schriftgröße,
Farbe, Ausrichtung und Drehung. Nicht unterstützte Zeichen oder zu kleine
Rahmen werden im Dialog gemeldet. Liberation Sans ist bereits enthalten.

Das Pfeilwerkzeug wählt eigene Objekte aus. Ziehen verschiebt sie, die vier
Eckgriffe ändern ihre Größe. Bilder behalten dabei ihre Proportionen; mit
Umschalt kannst du sie frei strecken. Doppelklick, F2 oder Eingabe öffnen die
Eigenschaften. Entf löscht das ausgewählte Objekt, Strg + D erzeugt eine Kopie.
Die Pfeiltasten verschieben um einen Punkt, mit Umschalt um zehn Punkte.
Ein laufender Entwurf wird mit Esc verworfen; die fertige Bewegung lässt sich
mit einem Schritt rückgängig machen. Das Kontextmenü und das Menü
**Anmerkungen** bieten dieselben Bearbeitungsaktionen.

## Suchen und Bildseiten

**Strg + F** sucht in der aktuellen Seitenreihenfolge. Eingabe und Umschalt +
Eingabe springen durch die Treffer, Esc schließt die Suchleiste. Die Suche
berücksichtigt vorhandenen PDF-Text und eigene Textfelder. Bei einem Textfeld
wird dessen Rahmen hervorgehoben. Die Seitenauswahl für den Export bleibt beim
Springen erhalten. Scans benötigen zuerst eine Textschicht.

Unter **Datei → Bilder zu PDF-Seiten …** wählst du PNG- oder JPEG-Dateien,
Seitenformat, Ausrichtung und Rand. Die Bilder werden als neue Seiten in den
Arbeitsbereich übernommen und können dort sortiert werden. Ein gemeinsamer
Import ist ein Undo-Schritt. Sehr kleine Bilder können für das Bildformat
zu klein sein; A4 und Letter funktionieren auch dann.

**Datei → Durchsuchbare PDF erstellen (OCR) …** ist in der
[OCR-Anleitung](ocr.md) beschrieben.
