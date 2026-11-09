# v1.19.7 – Energie: Woche, Monat, Jahr unter „Mehr…“ (06.11.2026)

Panel 2027.01.12 · gilt für Bosch EHS, Samsung (EMS-EHS) und Brötje EHS

**Energie Monitoring (Startseite → Energie)** – Register wie bisher: Heute · Gesamt · Heizen · Warmwasser · Kühlen.
- „Kühlen“ erscheint nur bei aktivierter Kühlfunktion (Schalter „Kühlfunktionen anzeigen“, Kühl-Zähler vorhanden, Bosch: Betriebsart nicht „Nur Heizen“).

**Mehr… → Energie – Details (neu aufgebaut)**
- Zeitraum auswählen: Woche · Monat · Jahr (mit Kurzwerten Strom/Wärme), darunter „Alle Zählerstände“ (bisherige Liste).
- Woche, Monat und Jahr je als eigene Ansicht im Stil des Energie Monitoring (Ring + Legende), Register Gesamt · Heizen · Warmwasser · Kühlen, unten ‹ › zu früheren Wochen (KW), Monaten oder Jahren blättern, darunter Vergleich mit dem Zeitraum davor.
- Werte aus der HA-Langzeitstatistik (`recorder/statistics_during_period`), Ersatz HA-Verlauf.
- Brötje: Solarertrag je Woche/Monat/Jahr, auch über das Solar-Menü erreichbar.

**Fehler behoben (Samsung):** Strom verbraucht war 0 kWh. Liefert der Stromzähler des Außengeräts 0, wird die Summe Heizen + Warmwasser genommen; „Heute“ nutzt die Tageszähler von EHS Sentinel.

Geändert: `ems-panel.html`, `manifest.json`.
Im Browser (Demo Bosch/Samsung/Brötje) geprüft – in HA noch nicht getestet.
