# EMS-ESP Manager v1.13.39

- EMS-EHS → Heizstab: Der Hinweis „Weboberfläche im MQTT-Betrieb nicht erreichbar“ steht nicht mehr in der Hinweis-Karte, sondern direkt in der Karte „Heizen im Gerät“ (bzw. „Einstellungen im Gerät“).
- Text überarbeitet:
  - Überschrift „Im MQTT-Betrieb gesperrt“
  - Hinweis, dass Regelung und Messwerte normal laufen
  - Zwei nummerierte Wege: Heizstab kurz vom Strom → „Erneut versuchen“ oder AP-Modus → http://192.168.2.1/expert
- Knöpfe „Erneut versuchen“ und „AP-Seite öffnen“.
- Die technische Meldung ist eingeklappt („Technische Meldung“).
- Kommen auch keine MQTT-Daten, erscheint eine Warnung mit dem Hinweis auf die IP.

Datei: ehs.py, manifest.json.
