# EMS-ESP Manager v1.13.21

## Heizstab (novolto): Geräte-Einstellungen direkt aus Home Assistant
- Neue eigene Seite **„Heizstab-Einstellungen“**, erreichbar über EMS-Regelung bzw. Zusatzfunktionen → Heizstab-Einstellungen und Heizstab → Gerät → Heizstab-Einstellungen (Gerät).
- Die Seite liest alle Einstellungen der Expert-Seite live aus dem Heizstab und schreibt sie zurück. Bereiche:
  - MQTT: Broker, Topic, Benutzer, Passwort, Discovery, Intervalle
  - WLAN: SSID, Passwort
  - Heizen: Betriebsart, Zieltemperatur, Hysterese und deren Lage, Leistung, „Nur mit MQTT heizen“
- Die Werte prüft der Manager vor dem Senden anhand der Grenzen, die der Heizstab selbst meldet (Typ, min/max, erlaubte Werte).
- Passwörter werden nie angezeigt und bleiben unverändert, wenn das Feld leer bleibt.
- Schnellfunktionen:
  - **„MQTT auf Home Assistant umstellen“**: IP von Home Assistant ist vorbelegt, Benutzer und Passwort für Mosquitto eingeben.
  - **„Zurück zur novolto-Cloud“**
- Schnittstelle (Firmware 2026):
  - `GET http://<Heizstab>/api/unstable/settings/get`
  - `POST http://<Heizstab>/api/unstable/settings/set` mit `{Bereich: {Einstellung: Wert}}`
- Heimnetz-Suche erkennt den Heizstab jetzt sicher an dieser Einstellungs-API.
- Nur Administratoren können die Seite öffnen und Werte ändern. Neuer WebSocket-Befehl: `ems_esp_manager/panel/heater_dev`.

Panel-Version 2026.12.18. Dateien: heater.py, panel.py, bosch-ui800.html, manifest.json. Home Assistant neu starten, Panel mit Strg+F5 neu laden.
