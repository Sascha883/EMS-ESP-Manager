# EMS-ESP Manager v1.13.33

## Zahlenfelder (+ / −) repariert
**Ursache:** Die Seite lud sich alle 4 s neu, sobald nach einem Klick der +/−-Knopf den Fokus hatte. Dabei wurde das Feld mit dem alten Wert vom Server überschrieben, bevor der neue Wert nach 0,7 s gespeichert war. Das Speichern lief dann über das schon ersetzte Feld und ging teilweise verloren. Das gab das Hoch- und Runterspringen und die nicht übernommenen Werte.

**Neu:**
- **Zwischenstand:** +/− ändern nur einen Zwischenstand. Jedes Neuzeichnen zeigt diesen Zwischenstand, nichts springt mehr zurück. Das Feld ist bis zum Speichern blau umrandet.
- **Speichern:** passiert 0,8 s nach dem letzten Klick, mit dem Wert aus dem Zwischenstand und nicht aus dem Feld. Wer schnell mehrmals klickt, löst nur einen Speichervorgang aus.
- **Aktualisierung:** Die Seite zeichnet sich nicht neu, solange ein Wert offen ist, gerade geklickt wurde (2,5 s) oder getippt wird.
- **Werte:** werden auf Grenzen und Schrittweite ab dem Minimum gerundet. Getippte Werte werden nur auf die Grenzen begrenzt, nicht auf die Schrittweite. Komma als Dezimaltrennzeichen geht.
- Das gilt für die Heizstab-Regelung und für die Einstellungen im Heizstab.
- **Reiter „Einstellungen“ (Wärmepumpe):** Die Live-Aktualisierung überschreibt ein gerade geändertes Feld 6 s lang nicht mehr.

Getestet in der Demo:
- 5× schnell + → ein Speichervorgang, der Wert bleibt
- 6× langsam über die Aktualisierung hinweg → alle übernommen
- Tippen und Enter
- Einstellung im Gerät

Datei: ehs.py, manifest.json. Home Assistant neu starten, EMS-EHS mit Strg+F5 neu laden.
