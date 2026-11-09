# v1.22.5 – Mehrere EMS-ESP-Gateways sauber getrennt (08.11.2026)

Panel 2027.01.14 (unverändert) · baut auf v1.22.4 auf · betrifft nur Anlagen mit 2+ EMS-ESP-Gateways

- **Zwischenspeicher je Gateway:** Werteliste (60 s) und erkannte Bus-Geräte (10 min) werden pro Gateway gespeichert. Vorher konnte Gateway A die Geräteliste von B bekommen (falsche „Erkannte Anlage“, falsche Zuordnung, falscher Kopf in der Bus-Log-Datei).
- **Angelegte Werte („In Home Assistant anlegen“) gehören einem Gateway:** Neue Werte merken sich ihr Gateway. Ältere Einträge werden beim ersten Start automatisch dem ersten Gateway zugeordnet; die Doppelten der anderen Gateways (z. B. `sensor.boiler_x_2`) werden entfernt und nicht mehr abgefragt. Beim Löschen eines Werts wird er bei allen Gateways entfernt.
- **GitHub nur einmal:** Die Versionsliste wird für alle Gateways nur einmal abgefragt und geteilt („Updates prüfen“ aktualisiert alle Gateways).

Bei nur einem Gateway ändert sich nichts.

Geändert: `entity_map.py`, `extra_values.py`, `coordinator.py`, `manifest.json`.
Python-Syntax geprüft – in HA noch nicht getestet (kein Testsystem mit zwei Gateways).
