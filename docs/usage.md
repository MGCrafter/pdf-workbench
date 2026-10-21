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

**Ansicht → Darstellung → Bewegung reduzieren** schaltet die kurzen Übergänge ab. Diese
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
normale Textfelder, Kontrollkästchen, Einfachauswahl und Optionsgruppen. Die Änderung gilt auch
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
Änderung ab. XFA, Signaturfelder, Skripte und Berechnungen,
Aktionsschaltflächen, Passwort- und Richtextfelder, editierbare Auswahl und
Mehrfachauswahl sowie mehrdeutige oder nicht seitengebundene Felder gehören
nicht zum unterstützten Umfang. Felder mit dem speziellen NoRotate- oder NoZoom-Verhalten
werden mit einer Erklärung abgewiesen, damit Vorschau und Export nicht
unterschiedlich ausgerichtet oder skaliert erscheinen.

## Interaktive Formulare gestalten

**Werkzeuge → Formularfelder bearbeiten …** (Strg + Alt + F) öffnet die
Quelldatei der aktuellen Seite im visuellen Editor. Ein Feldtyp und **Feld
hinzufügen** legen ein Textfeld, Kontrollkästchen, Dropdown oder eine Optionsgruppe
an. Felder lassen sich in der Vorschau auswählen, verschieben und über den
Eckgriff vergrößern. Pfeiltasten verschieben das ausgewählte Feld, Umschalt
vergrößert den Schritt. Position, Größe und Seite sind auch rechts einstellbar.

Feldname, Beschriftung, Anfangswert, Schreibschutz und Pflichtkennzeichnung
stehen in den Eigenschaften. **Eigenschaften übernehmen** prüft die Eingaben.
Bei Dropdowns bedeutet eine Zeile `wert=Beschriftung` eine Auswahloption;
der Anfangswert ist der Teil links vom Gleichheitszeichen. Eine Optionsgruppe
speichert genau eine gewählte Option. Über die Platzierungsauswahl lassen sich
ihre Schaltflächen einzeln positionieren. **Nach oben / Nach unten** ändern
die Feldreihenfolge für die Tabulatortaste.

**Formular übernehmen** überträgt alle Änderungen als einen Undo-Schritt.
Sie gelten für die ganze Quelldatei, auch für duplizierte Arbeitsseiten dieser
Quelle. **PDF speichern …** schreibt eine neue, weiterhin ausfüllbare PDF.
Beim Export einer Seitenauswahl werden ausgelassene Widgets und ihre Werte
nicht mitgenommen. Liegt die ausgewählte Radiooption nur auf einer ausgelassenen
Seite, wird die exportierte Gruppe geleert.

Neue und geänderte Feldansichten verwenden eine schlichte Standardschrift mit
Windows-1252-Zeichen. Nicht darstellbare Zeichen werden abgewiesen. Aufwendige
ursprüngliche Gestaltung kann sich beim Bearbeiten vereinfachen. Ein Pflichtfeld
wird als solches gekennzeichnet; die App prüft vor dem Export keine Vollständigkeit.
Das Löschen eines Felds ist keine allgemeine Schwärzungsfunktion für Dokumentinhalte.

## PDF-Versionen vergleichen

**Werkzeuge → PDF-Versionen vergleichen …** öffnet zwei PDFs unabhängig vom
Arbeitsbereich. Geschützte Dateien fragen nach ihrem Öffnungspasswort. Links
steht die erste, rechts die zweite Version. Der Vergleich ordnet gleiche Seiten
zu, sodass eine eingefügte Seite nachfolgende Seiten nicht pauschal als geändert
markiert. Verschobene Seiten erscheinen mit einem Verweis auf ihre Gegenstelle.

Die Änderungsliste zeigt eingefügte, entfernte und geänderte Seiten. Textänderungen
zeigen Vorher und Nachher; farbige Rahmen kennzeichnen sichtbare Unterschiede.
Ein Klick springt zur passenden Seite. Zoom und Scrollposition beider Vorschauen
laufen gemeinsam. **Nur Änderungen** blendet unveränderte Seiten aus. Abbrechen
oder Schließen verändert weder Arbeitsbereich noch Dateien.

Der Vergleich wertet auslesbaren Text und gerenderte Seiten mit bis zu
1.000 Pixeln an der längsten Kante aus. Er ist auf 300 Seiten je Datei begrenzt;
Seiten mit mehr als 100.000 ausgelesenen Textzeichen werden abgewiesen.
Pro Seite zeigt die Liste höchstens 60 Textänderungen; eine Kürzung wird angezeigt. Sehr kleine Bildänderungen können
unentdeckt bleiben. Scans werden visuell verglichen und bekommen dabei keine
OCR. Metadaten, versteckte Anhänge, Tags, Berechtigungen und die Echtheit digitaler
Signaturen sind nicht Teil des Vergleichs. Ein unverändertes Ergebnis bestätigt
keine Bytegleichheit oder vollständige inhaltliche Gleichheit.

## Wasserzeichen und Seitennummern

**Werkzeuge → Wasserzeichen …** setzt eigenen Text mit Farbe, Schriftgröße,
Position, Rand, Winkel und Deckkraft. **Werkzeuge → Seitennummern …** ergänzt
Nummern mit einer Vorlage wie `Seite {page} von {pages}`. Die Startnummer und
die Zählweise lassen sich ändern: durch das ganze Arbeitsdokument oder nur
durch die bearbeiteten Seiten.

Als Ziel stehen alle Seiten, die aktuelle Auswahl oder ein Bereich wie
`1-3, 5` bereit. Die Seitenzahlen beziehen sich auf die Arbeitsreihenfolge.
Die Vorschau zeigt die tatsächliche PDF-Darstellung mit der aktuellen Drehung.
Nach einer Eingabe wird erst nach erneuter Vorschau **Übernehmen** freigegeben.
Eine duplizierte Seite wird unabhängig von ihrer anderen Kopie bearbeitet.

Die Übernahme ist ein Undo-Schritt; gespeichert wird erst mit **PDF speichern …**.
Der Text wird normaler PDF-Seiteninhalt. Nach dem Wiederöffnen lässt er sich
nicht als eigenes Workbench-Objekt auswählen. Er schützt nicht vor Kopieren,
Entfernen oder Veränderungen und ersetzt keine vertrauliche Kennzeichnungskontrolle.

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
