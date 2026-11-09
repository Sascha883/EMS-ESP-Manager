# EMS-ESP Manager v1.15.3

„BSB-Adapter“ heißt jetzt **„SAR-Adapter“**

- Umbenannt überall in der Oberfläche: Seitenleiste, Menü *EMS-Regelung → SAR-Adapter*, Kachel auf der EMS-BSB-Startseite, Reiter in EMS-EHS/EMS-BSB, Einrichtung (Konfigurieren), Gerätename unter *Geräte & Dienste*, Meldungen und Texte.
- Neue Adresse der Seitenleiste: **/sar-adapter**. Die alte Adresse /bsb-adapter funktioniert weiter (ausgeblendet).
- Bestehende Entitäts-IDs (z. B. `sensor.bsb_adapter_…`) bleiben unverändert, damit Automationen weiterlaufen.
- Der Bus selbst heißt weiter BSB (Protokoll von Siemens/Brötje); „BSB-LAN“ und „BSB-Atom“ bleiben als Fremd-/Platinenbezeichnung.
- Panel 2026.12.28.

Geändert: bosch-ui800.html, bsb.py, bsb_params.py, bsb_proto.py, bsb_sensor.py, __init__.py, config_flow.py, const.py, panel.py, ehs.py, dashboard_builder.py, strings.json, translations/de.json, translations/en.json, manifest.json, README.md.
