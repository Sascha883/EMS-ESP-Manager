# v1.22.8 – Bosch „System“ mit Kacheln und Ausklappmenü (09.11.2026)

Panel **2027.01.15** (unverändert) · baut auf v1.22.7 auf

- **EMS-EHS → Bosch → System:** eine Seite statt Umschalter. Oben vier Kacheln mit Status – **WP-Auswertung** (Arbeitszahl, Starts, kritische Hinweise), **Zuordnung**, **MQTT** (verbunden/getrennt, Broker), **Uhrzeit** (stimmt/Abweichung, Zeitserver). Kachel antippen öffnet den Bereich.
- Darunter **Ausklappmenü**: WP-Auswertung (komplett mit Zeitraum, PDF/CSV, Kennzahlen, Bewertung, Verläufen), Zuordnung, MQTT, Uhrzeit. Offene Bereiche werden gemerkt.
- **WP-Auswertung** nur bei Wärmepumpe am EMS-Bus; aus den Werkzeugen unter „Übersicht“ entfernt.
- Alte Links (`#bosch/zuordnung`, `/mqtt`, `/uhrzeit`, `/auswertung`) öffnen „System“ mit dem passenden Bereich.

Geändert: `ehs.py`, `wpa.py`, `dashboard_builder.py`, `manifest.json`. JS-Syntax geprüft, Seite mit nachgebildetem HA (Demo-Verlaufsdaten) im Browser getestet – in HA noch nicht getestet.
