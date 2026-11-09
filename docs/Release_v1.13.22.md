# EMS-ESP Manager v1.13.22

## Seite „EMS-EHS“: neue Reiter „Heizstab“ und „Heizstab-Log“
Die beiden Reiter erscheinen, sobald ein Heizstab im Manager eingerichtet ist. Gestaltung wie die übrigen Reiter: Karten, Zeilen, Schalter, ± Felder.

### Reiter „Heizstab“ (drei Spalten)
- **Heizstab**:
  - Verbindungsstatus MQTT und Web-Oberfläche, Broker im Gerät mit Hinweis bei novolto-Cloud
  - Status, Leistung, Wassertemperatur, Netzspannung, Energie heute/gestern, Gerätedaten
  - Knöpfe „Verbindung prüfen“ und „Heizstab-Log“, Links zu Experten-Seite und Startseite
- **Gerät**: Web-Adresse eingeben und prüfen, MQTT-Topic wählen, Suche im Heimnetz.
- **Regelung durch Home Assistant**: alle Einstellungen der Heizstab-Regelung, verschoben aus dem Panel-Menü. Dazu gehören Regelung an/aus, Betriebsart, Leistung, Überschuss-Sensor, Vorzeichen, Reserve, Mindest- und Höchstleistung, Abschaltverzögerung, Intervall, Freigabe, Akku, Maximaltemperatur und Hysterese.
- **Geräte-Einstellungen**: live aus dem Heizstab gelesen und direkt dorthin geschrieben (`/api/unstable/settings/get|set`). Bereiche:
  - MQTT-Verbindung, WLAN, Heizen
  - Karte „MQTT umstellen“: auf Home Assistant oder zurück zur Cloud

### Reiter „Heizstab-Log“ (nur für den Heizstab)
- Zeichnet auf:
  - jeden gesendeten MQTT-Befehl (`settings/set`, `settings/get`) mit Inhalt
  - jeden Schreib- und Lesezugriff auf die Web-API
  - Antworten des Heizstabs (`settings/reply`), Statuswechsel (`heating/status`), online/offline (`mqtt/status`)
  - Messwerte 1× pro Minute
  - Regelentscheidungen und geänderte Einstellungen
- Verbindungsabfrage: Im Hintergrund jede Minute (MQTT-Messwerte und Web-API). Ins Log kommt nur eine Änderung. Mit „Verbindung prüfen“ gibt es ein vollständiges Ergebnis mit Antwortzeit und Broker im Gerät, inklusive Warnung bei novolto-Cloud.
- Filter: Alle, Gesendet, Empfangen, Verbindung, Web-API, Regelung. Dazu Pause, Kopieren und Leeren.
- Speicher: höchstens 1500 Einträge, nur bis zum Neustart. Passwörter werden als *** geloggt.
- Neuer WebSocket-Befehl `ems_esp_manager/panel/heater_log` mit den Aktionen `check` und `clear` (nur Administratoren).

### Panel
- Samsung: Der Menüpunkt „Heizstab-Einstellungen & Log“ öffnet direkt die Seite EMS-EHS im Reiter Heizstab.

Panel-Version 2026.12.19. Dateien: heater.py, panel.py, ehs.py, bosch-ui800.html, manifest.json. Home Assistant neu starten, Seite mit Strg+F5 neu laden.
