# EMS-ESP Manager v1.14.3

Panel 2026.12.25

- **„Bosch HMI 800“ heißt jetzt „BH-800“** – Seitenleiste, Panel-Titel, Konfigurieren-Dialog, Dashboard, README.
- Neue Adresse des Panels: **/bh-800**. Die alte Adresse /bosch-hmi800 bleibt ohne Seitenleisten-Eintrag erreichbar (Lesezeichen, Wand-Tablets laufen weiter). Kiosk-Adressen ändern sich nicht.
- Panel-Upload: Dateien werden an „BH-800“ oder „HMI 800“ erkannt (ältere Panel-Dateien bleiben gültig).
- BSB-Adapter: geprüftes Gerät korrigiert – Gerätefamilie 134 / Variante 100 (RVS43.345/100), laut Adapter-Log.

Hinweis: In den Einstellungen unter „Konfigurieren“ heißen die Felder jetzt „BH-800: …“; die gespeicherten Werte bleiben erhalten.

Geändert: __init__.py, const.py, config_flow.py, panel.py, dashboard_builder.py, ehs.py, bsb_params.py, bosch-ui800.html, manifest.json, strings.json, translations/de.json, translations/en.json, README.md.
