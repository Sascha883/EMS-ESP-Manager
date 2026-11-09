# EMS-ESP Manager v1.13.35

## Alle Logs ein-/ausschaltbar, Standard: aus
- **Bus-Log (Bosch/EMS-ESP)**:
  - Neuer Schalter **„Aufzeichnung“** in der Bus-Log-Karte des Dashboards „EMS-ESP“.
  - Bei ausgeschalteter Aufzeichnung werden keine Bus-Meldungen gespeichert (Speicher und Download) und keine Statuszeilen geschrieben. Der Sensor „Bus-Log“ zeigt dauerhaft „Aufzeichnung aus“. Damit entfallen auch die bisher ca. 86.000 Datenbank-Einträge pro Tag.
  - Die Zeitprogramm-Erkennung und das Anlernen bekommen die Bus-Meldungen trotzdem weiter.
  - Neue WebSocket-Befehle: `ems_esp_manager/buslog/enable`. `buslog/info` meldet `enabled`.
- **EHS-Log (Samsung)**: Standard ist jetzt **aus**. Der Schalter wird gespeichert und bleibt auch nach einem Neustart.
- **Heizstab-Log**: war schon Standard aus und gespeichert.
- Die Schalter liegen in `.storage/ems_esp_manager.log_flags`.

Dateien: buslog.py, log.py, __init__.py, sensor.py, ehs.py, manifest.json. Home Assistant neu starten.
