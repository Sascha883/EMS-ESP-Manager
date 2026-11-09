# EMS-ESP Manager v1.24.0

Panel **2027.01.16** (unverändert) · Home Assistant ab 2025.2

Umbau-Version ohne neue Funktionen: die Grundlage, damit sich die Geräte im Manager (Bosch, Samsung, Brötje/ESP-Bus-Adapter, Heizstab) nicht mehr in die Quere kommen – und damit Viessmann und Vaillant (ESP32) später sauber dazukommen.

## Geändert
- **Hersteller-Register** (`vendors/base.py`): jeder Hersteller ist genau einmal beschrieben (Name, Seitenleiste, Adresse, frühere Adressen, Anbindung, Speicherbereich). Seitenleiste, Kiosk-Link, „Installierte Geräte“ und „Panel öffnen“ lesen daraus. Viessmann und Vaillant sind vorgemerkt (Anbindung ESP32), aber noch nicht wählbar. Doppelte Adressen oder Speicherschlüssel fallen schon beim Laden auf.
- **Gemeinsamer Kern wird beim Start einmal angelegt** (Panel-Verwaltung, Speicher, Seiten, Websocket) – vor allen Geräten. Die Startreihenfolge der Geräte spielt keine Rolle mehr (Ursache des Fehlers aus v1.23.1).
- **Eigener Speicherbereich je Hersteller:** `.storage/ems_esp_manager.panel` hat jetzt für jeden Hersteller einen eigenen Block (`vendors.bosch`, `vendors.samsung`, `vendors.heater`, `vendors.bsb`). Bosch-Zuordnung, Heizkreisnamen, Warmwasseroptimierung, Leistungsbegrenzung, Heizstab- und Adapter-Einstellungen lagen bisher gemischt auf oberster Ebene.
  - Der Umzug passiert **automatisch beim ersten Start**. Vorher wird der alte Stand als `.storage/ems_esp_manager.panel_vor_v1.24` gesichert.
- Automatische Tests (`tests/`): Umzug des Speichers, alle Hersteller gleichzeitig in zufälliger Reihenfolge, Samsung + Heizstab + Adapter als Einträge.

## Hinweise
- **Zurück auf v1.23.x:** Zuordnung und Einstellungen erscheinen dann leer. Lösung: HA stoppen, `ems_esp_manager.panel_vor_v1.24` nach `ems_esp_manager.panel` kopieren, alte Version starten.
- Update: kompletten Ordner `custom_components/ems_esp_manager` ersetzen (enthält jetzt den Unterordner `vendors/`) und **HA neu starten**.
