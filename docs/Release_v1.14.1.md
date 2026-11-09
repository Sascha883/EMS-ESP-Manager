# EMS-ESP Manager v1.14.1

Panel 2026.12.23 – **EMS-Regelung → BSB-Adapter** (Siemens BSB/LPB: Brötje, Elco, Baxi, Atlantic, Weishaupt …)

- **Neues Gerät „BSB-Adapter“** (*Gerät hinzufügen → BSB-Adapter*): sucht ESPHome-Adapter (BSB-Atom), BSB-LAN aus der HA-Integration „BSB-LAN“ und optional im Heimnetz; automatischer Vorschlag unter „Entdeckt“.
- **Regler-Erkennung** über 6224/6225/6226.
- **Eigene Parametertabelle** (bsb_params.py, ca. 90 Werte, mit Ausweich-IDs; die passende ID je Anlage wird gemerkt) und **Parameter-Scan**.
- **ID-Suchlauf** (fertige Bereiche oder eigener Bereich) und **Anlernen unbekannter Werte** (Datentyp-Vorschlag, Name) – angelernte Werte verhalten sich wie die Tabelle.
- **Favoriten und Bus-Abfrage** (von Hand), Sensoren „Regler“ und je Favorit.
- **Bus-Mitschnitt & Log** (von Hand): eigene Dekodierung aller Telegramme, Bus-Teilnehmer.
- Schreiben: BSB-LAN direkt, ESPHome über die Entitäten des Adapters.
- Code-Überarbeitung gegenüber dem Entwurf 1.14.0: Bus-Abfragen nacheinander (keine Kollisionen zwischen Scan, Abfrage und Panel), Log-Abo nur bei neuem/verlorenem Adapter-Client erneuern, BSB-LAN-Werte vereinfacht, unbekannte Werte werden gespeichert.
- Ohne fremde Parametertabelle: der Entwurf 1.14.0 enthielt eine aus BSB-LAN (Lizenz CC BY-NC-SA 4.0) erzeugte Tabelle – ersetzt.

Neue Dateien: bsb.py, bsb_proto.py, bsb_params.py, bsb_sensor.py.
Geändert: __init__.py, const.py, config_flow.py, panel.py, sensor.py, bosch-ui800.html, manifest.json, strings.json, translations/de.json, translations/en.json, README.md.

Firmware: BSB-Atom 1.2 (bsb-heizung.yaml) – Aktion `bsb_get`, Schalter „BSB-Bus-Mitschnitt“, Logger mit `initial_level: INFO`.
