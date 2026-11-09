# EMS-ESP Manager v1.15.0

Eigenes Dashboard **EMS-BSB** für Brötje / Siemens-BSB – wie EMS-EHS (Samsung) und EMS-ESP (Bosch)

- Neuer Seitenleisten-Eintrag **„EMS-BSB“**, sobald das Gerät „BSB-Adapter“ eingerichtet ist (abschaltbar unter *Konfigurieren*).
- Reiter wie bei Samsung:
  - **System & Panel:** Regler, Gerätefamilie, Adapter und Firmware, Favoriten mit Bus-Abfrage, Knopf „BSB-Panel öffnen“.
  - **Einstellungen:** schreibbare Werte des Adapters (Zahlen, Auswahl, Schalter) nach Heizkreis 1/2, Trinkwasser, Solar … gruppiert, mit Suche.
  - **Messwerte:** alle Sensoren des Adapters, gruppiert.
  - **BSB-Adapter:** Werte & Favoriten, Scan & Anlernen, Adapter, **Verbindung (neu)**, Info & Log.
  - **Info & Log:** Verbindung im Aufbau wie EHS Sentinel (Adresse, Integrationsstatus, Verbindung, Firmware, bsb_get, Bus-Abfrage, Mitschnitt, Log-Abo; Neu verbinden, ESPHome, Web-Oberfläche) und BSB-Log.
  - **Heizstab**, falls eingerichtet.
- „Neu verbinden“ lädt das ESPHome-Gerät in Home Assistant neu und abonniert das Log wieder.

Geändert: ehs.py, bsb.py, panel.py, config_flow.py, manifest.json, README.md.
