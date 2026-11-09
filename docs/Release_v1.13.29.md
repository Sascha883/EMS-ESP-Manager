# EMS-ESP Manager v1.13.29

## EMS-EHS: Reiter „Verbindung & Log“
- „EHS-Log“ und „Verbindung“ sind ein Reiter, aufgebaut wie der Heizstab:
  - Oben „EHS Sentinel“ mit Adresse, Version und Kennzeichen (Verbunden, Schreibmodus, Polling) sowie „Neu verbinden“
  - Fünf Kacheln: Pakete gelesen, verarbeitet, angefordert, fremde Pakete, Warteschlange
  - Hinweise nur bei Bedarf, mit dem nächsten Schritt: nicht eingerichtet, nicht geladen, getrennt, Schreibmodus aus, volle Warteschlange
- Auswahl darunter:
  - **Verbindung**: Daten der Integration und Protokollstufe
  - **EHS-Log**: Log wie bisher, mit Paket-Log, Filtern und Kopieren
- Alte Links auf #log öffnen direkt das EHS-Log.

Datei: ehs.py, manifest.json. Home Assistant neu starten, EMS-EHS mit Strg+F5 neu laden.

## Handy (iPhone/Android)
- Die Zeilen brechen um, statt sich zu quetschen. Auswahllisten und Eingabefelder stehen in voller Breite unter der Bezeichnung, die ± Felder bleiben daneben.
- Die Erklärtexte trennen keine Wörter mehr mitten im Wort.
- Kopfkarte (Heizstab / EHS Sentinel): Titel, Kennzeichen und Knopf untereinander, Kacheln zweispaltig.
- Die Auswahl (Regelung / MQTT / Info, Verbindung / EHS-Log) bleibt beim Scrollen oben stehen und lässt sich seitlich wischen.
- Größere Tippflächen (Felder 44 px), Abstand zur Home-Leiste des iPhones, Log-Zeilen umbrechend.
