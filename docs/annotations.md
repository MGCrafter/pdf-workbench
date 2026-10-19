# Anmerkungen und gezeichnete Unterschriften

PDF Workbench speichert Kommentare als PDF-Textanmerkungen (`/Text`),
Textmarkierungen als `/Highlight` mit den tatsächlich ausgewählten
Textrechtecken und gezeichnete Unterschriften als `/Ink`. Jede Anmerkung hat
einen eigenen sichtbaren Appearance-Stream. Die Darstellung benötigt deshalb
keine externen Schriftarten, Bilder oder zusätzlichen Dateien.

Eine gezeichnete Unterschrift ist eine sichtbare Zeichnung. Sie ist keine
kryptografische Zertifikatssignatur und bestätigt weder Identität noch
Unverändertheit des Dokuments. PDFs mit digitalen Zertifikatssignaturen,
interaktiven Formularfeldern oder Verschlüsselung werden weiterhin abgelehnt.

## Bearbeiten und Wiederöffnen

Hinzufügen, Bearbeiten und Löschen sind rückgängig und wiederherstellbar.
Beim Duplizieren einer Seite erhalten ihre Anmerkungen neue Kennungen und
lassen sich unabhängig bearbeiten. Die Quelldatei und bestehende Ausgaben
werden niemals überschrieben.

Eigene Exporte enthalten zusätzlich versionierte Modellinformationen im
Anmerkungsdictionary. Ein SHA-256-Fingerabdruck bindet das Modell an sämtliche
Anmerkungsfelder, die sichtbaren Appearance-Streams und ihre Ressourcen.
Objektnummern, Stream-Kompression und der beim Kopieren wechselnde
Seitenverweis `/P` werden dabei als reine Speicherungsdetails behandelt.
Beim Wiederöffnen erkennt Workbench vollständig
verstandene eigene Anmerkungen und übernimmt sie in das bearbeitbare Modell.
Ihre PDF-Darstellung wird nur im eingelesenen Basissnapshot entfernt, damit
Vorschau und nächster Export jede Anmerkung genau einmal enthalten. Hierfür
werden sowohl die abgeflachte pypdf-Seitenansicht als auch die zugrunde
liegende indirekte Seitendictionary bereinigt; die Datei auf der Platte bleibt
unverändert.

Der Fingerabdruck dient nur zur Erkennung unverstandener externer Änderungen;
er ist keine authentifizierte digitale Signatur. Ändert ein anderes Programm
Striche, Markierungsrechtecke, Erscheinungsbild, Ressourcen oder private
Modellinformationen, bleibt diese Anmerkung im Originalzustand als fremde
Anmerkung erhalten. Das verhindert, dass ein veraltetes Modell die Änderung
ersetzt. Bei fehlendem Fingerabdruck wird ebenfalls nichts entfernt.

Bereits vorhandene fremde PDF-Anmerkungen verbleiben im Basissnapshot und im
Export. Sie werden derzeit nicht als bearbeitbare Workbench-Anmerkungen
angeboten. Unbekannte Versionen, beschädigte Modellinformationen und doppelte
private Kennungen bleiben ebenfalls erhalten, statt durch eine unsichere
Modellrekonstruktion verloren zu gehen. Fremde Programme können die privaten
Informationen entfernen; danach bleibt die Anmerkung als fremde Anmerkung
sichtbar, ist aber nicht mehr über das Workbench-Modell bearbeitbar.

## Koordinaten und Rotation

Das Modell verwendet normierte Koordinaten der sichtbaren Schnittfläche aus
CropBox und MediaBox, nach der ursprünglichen PDF-Seitenrotation. Zusätzliche
Drehungen im Arbeitsbereich verändern die gespeicherte Geometrie zunächst
nicht. Beim Export wird sie in PDF-Koordinaten abgebildet; die privaten
Modellinformationen werden in die endgültige Seitenorientierung gedreht.
Auch lokale Strichpunkte einer Unterschrift werden dabei gedreht. Dadurch
bleiben Platzierung und Form nach Export und Wiederöffnen erhalten.

## Verifikation

`tests/test_annotations.py` prüft echte Dateien: Unicode-Kommentare,
mehrzeilige QuadPoints, InkList, eigenständige sichtbare Appearances,
Seitenverweise, Bearbeiten/Löschen nach dem Wiederöffnen und Erhalt fremder
Anmerkungen sowie extern veränderte Geometrie, Appearances, Ressourcen und
Modelldaten. Reine Kompression, geänderte Objektnummern und eine andere
Dictionary-Reihenfolge erhalten die Bearbeitbarkeit. Je vier ursprüngliche
und zusätzliche Drehungen ergeben 16
Kombinationen mit versetzter CropBox. Ein unabhängiger Poppler-Renderer
vergleicht für alle 16 Kombinationen den Export vor und nach dem
Wiederöffnen pixelgenau und bestätigt, dass Löschen auch die Darstellung
entfernt. Der Poppler-Test wird nur ausgeführt, wenn `pdftoppm` installiert
ist; die übrigen Core-Tests benötigen keine grafische Oberfläche.
