## v1.11.11 – Performance & robustness / Leistung & Stabilität

### 🇬🇧 English

Includes the fixes of v1.11.10 (no more "blocking call" and "device_registry.devices" warnings in the HA log).

**Improved**

* **Less load on Home Assistant with a busy EMS bus:** new bus-log lines now only update the bus-log sensor. Before, every entity of the integration was recalculated up to once per second.
* **Smaller recorder database:** the 60 log lines of the bus-log sensor and the device list of the "Detected plant" sensor are no longer written to the history database.
* **Bosch panel faster on wall tablets:** the panel only redraws when one of its own entities changes, not on every state change in Home Assistant (lights, other sensors, bus log …).
* **Panel closes cleanly:** when the panel is closed or reloaded, its subscription on the Home Assistant connection is removed. Before, it stayed active in the HA frontend until the browser was reloaded.
* **Bus-log card:** the telegram count is kept up to date while lines arrive, instead of scanning up to 20,000 lines every 10 seconds.
* **Ready for future Home Assistant versions:** the device and entity registries are only read the way Home Assistant recommends (new module `registry.py`). Old and new Home Assistant versions both work, without deprecation warnings.

**Fixed**

* **Panel loads quickly when EMS-ESP is offline:** before, opening the panel could take minutes (up to 12 requests × 15 s timeout). Now the manager stops after the first failure and waits 60 s before asking the gateway again.
* **Hot-water optimisation:**
  * If the stop temperature or the mode entity is unavailable at the end (EMS-ESP restart, MQTT gone), the previous values are no longer dropped. The optimisation stays "active" and writes them back as soon as the entities are available again. Before, the tank could stay at the high temperature.
  * If starting fails halfway (mode switched, temperature not), the mode is set back at once.
  * Time window shown correctly (e.g. 12:00 instead of 11:60).
* **Bus-log stream after a token change:** with HTTP 401/403 the manager signs in again. Before, it retried with the old token every 10 s.
* **Switch programs:** stored programs are loaded completely before the bus log starts, so a telegram arriving at startup can no longer be overwritten by the old copy.

**Update**

Update via HACS (or replace the folder `custom_components/ems_esp_manager`), **restart Home Assistant** and reload the panel once (Ctrl+F5). Your settings are kept.

**Panel version:** 2026.11.21

---

### 🇩🇪 Deutsch

Enthält die Korrekturen aus v1.11.10 (keine Warnungen „blocking call“ und „device_registry.devices“ mehr im HA-Protokoll).

**Verbessert**

* **Weniger Last für Home Assistant bei viel Busverkehr:** Neue Bus-Log-Zeilen aktualisieren jetzt nur noch den Bus-Log-Sensor. Vorher wurden alle Entitäten der Integration bis zu einmal pro Sekunde neu berechnet.
* **Kleinere Recorder-Datenbank:** Die 60 Log-Zeilen des Bus-Log-Sensors und die Geräteliste des Sensors „Erkannte Anlage“ werden nicht mehr in die Verlaufsdatenbank geschrieben.
* **Bosch-Panel schneller auf Wandtablets:** Das Panel zeichnet nur noch neu, wenn sich eine seiner eigenen Entitäten ändert, nicht bei jeder Zustandsänderung in Home Assistant (Lichter, andere Sensoren, Bus-Log …).
* **Panel schließt sauber:** Beim Schließen oder Neuladen des Panels wird sein Abo auf der Home-Assistant-Verbindung abgemeldet. Vorher blieb es in der HA-Oberfläche bis zum nächsten Neuladen des Browsers aktiv.
* **Bus-Log-Karte:** Die Anzahl der Telegramme wird beim Eintreffen der Zeilen mitgezählt, statt alle 10 Sekunden bis zu 20.000 Zeilen zu durchsuchen.
* **Bereit für kommende Home-Assistant-Versionen:** Geräte- und Entitätsregistrierung werden nur noch so gelesen, wie Home Assistant es vorgibt (neues Modul `registry.py`). Alte und neue Home-Assistant-Versionen funktionieren, ohne Veraltet-Warnungen.

**Behoben**

* **Panel lädt schnell, wenn der EMS-ESP offline ist:** Vorher konnte das Öffnen des Panels Minuten dauern (bis zu 12 Anfragen × 15 s Zeitlimit). Jetzt bricht der Manager nach dem ersten Fehler ab und fragt das Gateway erst nach 60 s wieder.
* **Warmwasseroptimierung:**
  * Sind Stopptemperatur oder Betriebsart am Ende nicht verfügbar (EMS-ESP-Neustart, MQTT weg), werden die vorherigen Werte nicht mehr verworfen. Die Optimierung bleibt „aktiv“ und schreibt sie zurück, sobald die Entitäten wieder da sind. Vorher konnte der Speicher auf der hohen Temperatur bleiben.
  * Schlägt der Start halb fehl (Betriebsart umgestellt, Temperatur nicht), wird die Betriebsart sofort zurückgestellt.
  * Zeitfenster richtig angezeigt (z. B. 12:00 statt 11:60).
* **Bus-Log-Verbindung nach Token-Wechsel:** Bei HTTP 401/403 meldet sich der Manager neu an. Vorher versuchte er es alle 10 s mit dem alten Token.
* **Zeitprogramme:** Die gespeicherten Programme sind vollständig geladen, bevor das Bus-Log startet. Ein Telegramm direkt beim Start kann nicht mehr von der alten Kopie überschrieben werden.

**Update**

Update über HACS (oder den Ordner `custom_components/ems_esp_manager` ersetzen), **Home Assistant neu starten** und das Panel einmal neu laden (Strg+F5). Deine Einstellungen bleiben erhalten.

**Panel-Version:** 2026.11.21
