# EMS-ESP Manager v1.15.4

Dashboard heißt nach dem Hersteller – „Brötje EHS“, „Elco EHS“ …

- Das Dashboard (bisher „EMS-BSB“) heißt in der Seitenleiste jetzt **„<Hersteller> EHS“**, Vorgabe **„Brötje EHS“** – wie „Bosch EHS“ und „Samsung EHS“.
- Neu unter **EMS-Regelung → SAR-Adapter → Dashboard → Hersteller**: Auswahl des Herstellers. „Automatisch“ nimmt den Hersteller, den der Regler meldet (sonst Brötje).
  Zur Wahl stehen alle Hersteller mit Siemens-BSB/LPB-Regler, die der SAR-Adapter abfragen kann: Brötje, Elco, Atlantic, Alarko, ATAG, Austria Email, Baxi, Bösch, Chappée, CTA, CTC, De Dietrich, Ferroli, Fröling, Fujitsu Waterstage, Geminox, Gorenje, Grünenwald, Hansa, Herz, Ideal, MHG, Oilon, Olymp, Potterton, Sieger, Strebel, Thermital, Viadrus, Weishaupt, Viessmann (nur Vitotwin 300-W).
- Der Titel in der Seitenleiste ändert sich sofort nach der Auswahl. Auch in der Tabellenansicht (Reiter „Brötje“ → „Elco“ usw.) und dort unter SAR-Adapter → Adapter wählbar.
- Der eigene Seitenleisten-Eintrag **„SAR-Adapter“ entfällt** – eingerichtet wird der Adapter im EMS-ESP Manager, die Adapter-Seite liegt im Dashboard unter EMS-Regelung → SAR-Adapter. Die Adressen /sar-adapter und /bsb-adapter bleiben erreichbar.
- Panel 2026.12.29.

Geändert: bosch-ui800.html, bsb.py, ehs.py, panel.py, manifest.json, README.md.
