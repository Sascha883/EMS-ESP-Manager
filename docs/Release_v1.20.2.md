# v1.20.2 – Energie: Zähler in Wh richtig umrechnen (08.11.2026)

Panel 2027.01.14 · baut auf v1.20.1 auf · gilt für Bosch, Samsung und Brötje

**Fehler behoben:** Im Energie Monitoring (und unter Mehr… → Woche / Monat / Jahr) wurden Energiezähler ohne Blick auf die Einheit addiert. Liefert eine zugeordnete Entität ihren Wert in **Wh** (z. B. eigene Zuordnung, Template- oder Verbrauchszähler-Helfer, andere Integration), wurde er wie kWh gezählt – ein Zuheizer mit 4,0 kWh erschien als 4000 kWh, Zuheizer-Anteil und Arbeitszahl waren falsch (Meldung Bosch 6800i mit UI800, Warmwasser: 94 % Zuheizer, Arbeitszahl 0,1).

- Alle Energiezähler werden jetzt anhand von `unit_of_measurement` auf kWh umgerechnet (Wh, kWh, MWh, GWh).
- Langzeitstatistik wird von HA direkt in kWh angefordert (`units: {energy: kWh}`), Ersatzweg über den HA-Verlauf rechnet ebenfalls um.

Geändert: `ems-panel.html` (PANEL_VERSION 2027.01.14), `manifest.json`.
Syntax geprüft, Umrechnung mit Testwerten geprüft (4000 Wh → 4 kWh) – in HA noch nicht getestet.
