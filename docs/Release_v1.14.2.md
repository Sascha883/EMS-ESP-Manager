# EMS-ESP Manager v1.14.2

Panel 2026.12.24 – BSB-Adapter wie der Heizstab installieren und im Manager anzeigen

- *Gerät hinzufügen → „BSB-Adapter (ESP/BSB-Atom oder BSB-LAN)“* legt den Adapter als eigenes Gerät an (wie „Heizstab novolto“).
- Danach erscheint **„BSB-Adapter“ in der Seitenleiste** (abschaltbar unter *Konfigurieren*).
- Neuer Reiter **„BSB-Adapter“** im Dashboard **EMS-EHS** (Samsung) und eigene Ansicht im Dashboard **EMS-ESP** (Bosch), sobald das Gerät eingerichtet ist.
- Menüpunkt **EMS-Regelung → BSB-Adapter** jetzt auch im Samsung-Panel (war dort ausgeblendet).

Geändert: config_flow.py, panel.py, ehs.py, dashboard_builder.py, bosch-ui800.html, manifest.json, strings.json, translations/de.json, translations/en.json.
