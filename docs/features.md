# Seitenauswahl und Export

**Duplizieren:** Jede ausgewählte Arbeitsseite erhält eine eigene neue ID. Ihre
Kopie folgt unmittelbar auf das Original und übernimmt Quellseite und zusätzliche
Drehung. Eine Mehrfachauswahl wird in einem Schritt rückgängig gemacht; Undo und
Redo erhalten die IDs. Wiederholte Quellseiten bekommen beim PDF-Export eigene
Seiten- und Annotationobjekte mit passendem `/P`-Bezug.

**Seitenauswahl:** `1-3, 5` bezieht sich auf die aktuelle Arbeitsreihenfolge.
Leerraum, `;` als zusätzlicher Trenner und typografische Striche (`1–3`, `1—3`)
werden unterstützt. Doppelte Nummern werden entfernt; das Ergebnis folgt der
Arbeitsreihenfolge. Null, absteigende Bereiche und nicht vorhandene Seiten liefern
verständliche Fehler.

**Einzel-PDFs:** Für die ausgewählten Arbeitsseiten wird ein ausdrücklich neuer
Unterordner angelegt. Ein vorhandener Ordner oder Link wird immer abgewiesen.
Nummerierte Dateinamen halten die Arbeitsreihenfolge fest und nennen Quelle und
Originalseite. Jede Datei enthält genau eine Seite einschließlich Beschnitt und
Drehung. Alle Referenzen und Zielnamen werden vor dem Anlegen des Ordners geprüft.

Bei Abbruch oder Fehler versucht der Export, ausschließlich seine eigenen,
unveränderten neuen Dateien zu entfernen. Verzeichnisse werden nur mit `rmdir`
entfernt, niemals rekursiv. Fremde oder inzwischen ersetzte Dateien bleiben
unverändert. Wenn eigene Dateien gesperrt bleiben, nennt die Fehlermeldung ihre
Anzahl und Pfade sowie einen nicht vollständig bereinigten Ordner. Originaldateien
werden weiterhin ausschließlich als unveränderliche Byte-Snapshots gelesen.
Wenn der Zielordner währenddessen verschoben oder ersetzt wurde, wird der
Verbleib der bereits erzeugten Dateien ausdrücklich als unbekannt mit den
zuletzt bekannten Pfaden angegeben; der Ersatzordner wird nicht bereinigt.

Die bestehenden Grenzen gelten weiter: verschlüsselte PDFs, Formulare und
digitale Zertifikatssignaturen werden abgewiesen. Dokumentweite Lesezeichen, Anhänge, PDF/A-Konformität
und interne Verweise über neu zusammengesetzte oder getrennte Seiten werden nicht
als vollständig erhalten zugesagt. Seiten mit nichtpositiven oder nichtendlichen
MediaBox-Abmessungen werden vor dem Import abgewiesen; normale abweichende
CropBox-Werte bleiben erlaubt.

Die Regressionen in `tests/test_features.py` prüfen reale PDFs, getrennte
Annotationobjekte, Quellrotation plus Kopienrotation, Aliasse, Originalschutz,
Schreibfehler und Abbruch. Wenn Poppler installiert ist, prüfen sie zusätzlich
Text, sichtbare Annotationen und die tatsächlichen gerenderten Crop-Abmessungen
unabhängig von pypdf.
