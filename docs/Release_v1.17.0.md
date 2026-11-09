# EMS-ESP Manager v1.17.0

Firmware-Update für den ESP-Bus-Adapter direkt aus dem Manager

**Neu: ESP-Bus-Adapter → Adapter → „Firmware des Adapters“**
- Firmware-Datei (.bin im **OTA-Format**, z. B. `firmware.ota.bin` aus dem ESPHome Builder: ⋮ → Herunterladen → „OTA format“) hochladen und mit **Installieren** über WLAN aufspielen – ohne ESPHome Builder.
- Prüfung beim Hochladen: ESP32-App-Firmware (Factory-/Bootloader-Dateien und .gz werden abgelehnt), Version (aus `esphome: project:` bzw. Dateiname), ESPHome-Version, Chip, Build-Datum.
- Passt die Datei zum Adapter? Gerätename (z. B. `bsb-heizung`) und API-Schlüssel werden in der Datei gesucht; fehlt etwas, gibt es eine deutliche Warnung vor dem Installieren.
- Fortschrittsbalken (Verbinden, Speicher vorbereiten, Übertragen in %, Prüfen, Neustart); danach wartet der Manager, bis sich der Adapter zurückmeldet, und zeigt die neue Version.
- OTA-Passwort (falls `ota: password` gesetzt) und OTA-Port (Standard 3232) einstellbar; das Passwort wird nicht an das Panel zurückgegeben.
- Die letzten 6 Dateien bleiben gespeichert (`/config/ems_esp_manager/adapter_firmware/`), einzeln löschbar.
- Während des Updates pausieren Bus-Abfrage, Scan und Erkennung. Bricht die Übertragung ab, bleibt die bisherige Firmware aktiv.

**Technik**
- Neues Modul `esp_ota.py`: natives ESPHome-OTA-Protokoll (Version 1/2, Anmeldung SHA256/MD5, 8-KB-Blöcke mit Quittung), ohne Zusatzpakete.
- Neuer Websocket-Befehl `ems_esp_manager/panel/bsb_fw` (list, put, install, delete; nur Administratoren). Upload in 384-KB-Stücken.
- Noch nicht unterstützt: verschlüsseltes OTA (`ota: encryption:`) – der Manager meldet das verständlich.
