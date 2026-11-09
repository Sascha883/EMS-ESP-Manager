# EMS-ESP Manager v1.13.26

## EMS-EHS → Heizstab: neue Aufteilung
- **Oben „Heizstab“**: Symbol (orange, solange er heizt), Seriennummer, Web-Adresse und Status-Kennzeichen (MQTT, Web, Regelung). Darunter fünf Kacheln (Status, Leistung, Wasser, Netzspannung, Energie heute) und der Knopf „Verbindung prüfen“.
- Darunter die **Hinweise**, nur wenn etwas zu tun ist.
- Dann die **Auswahl**:
  - **Heizstab-Regelung**: Regelung durch Home Assistant, „Speicher & Freigabe“ (Maximaltemperatur, Hysterese, Freigabe, Akku) und „Heizen im Gerät“.
  - **MQTT-Einstellungen**: „Verbindung zu Home Assistant“ (umstellen, Zugangsdaten, Zurück zur Cloud), „Gerät finden“ (Web-Adresse, Topic, Heimnetz-Suche), „MQTT im Gerät“ und „WLAN im Gerät“.
  - **Info & Log**: Gerätedaten (Seriennummer, Firmware, Hardware, API, Topic, Broker, Einstellung im Gerät, letzte Prüfung), Links zur Experten-Seite und Startseite, daneben das Heizstab-Log.
- Die gewählte Ansicht bleibt beim nächsten Öffnen erhalten. Die Hinweise verweisen auf die richtige Ansicht.

Datei: ehs.py, manifest.json. Home Assistant neu starten, EMS-EHS mit Strg+F5 neu laden.
