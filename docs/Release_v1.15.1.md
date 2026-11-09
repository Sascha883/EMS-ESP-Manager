# EMS-ESP Manager v1.15.1

**EMS-BSB** (Brötje / Siemens-BSB) sieht jetzt aus wie das Bosch-Bedienfeld

- Die Seite **„EMS-BSB“** zeigt das Bedienfeld im Bosch-Stil (Panel 2026.12.27) mit Startseite aus Kacheln:
  - **Wärmeerzeuger** (Flamme): Status Kessel, Brennermodulation, Datum/Uhrzeit → Übersicht Wärmeerzeuger.
  - **Heizkreis 1 / Heizkreis 2**: Betriebsart, großer Komfortsollwert, Raum- und Vorlauf-Soll.
    Detailseite mit Reitern **Schutz · Auto · Reduziert · Komfort** und Rolle für den Komfortsollwert.
    „Mehr…“: Reduziertsollwert, Heizkennlinie, Vorlauf, Status, Pumpe.
  - **Trinkwasser**: Betrieb Aus/Ein/Eco, Nennsollwert als Rolle, gemessene Temperatur (oben/unten), Status.
  - **Solar** (Kollektor, Ertrag, Pumpe), **BSB-Adapter**, **Parameter**, **EMS-Regelung**.
- Kopfzeile wie Bosch: Status-Symbol und Außentemperatur.
- Die Zuordnung erfolgt automatisch über das Namensende der ESPHome-Entitäten (z. B. `sensor.heizung_brotje_aussentemperatur`), egal welches Präfix; eigene Zuordnungen bleiben möglich.
- Die bisherige Tabellenansicht (Einstellungen/Messwerte/Info & Log) ist weiter erreichbar: Wärmeerzeuger → „Alle Einstellungen & Messwerte (Tabellen)“.
- Seitenleiste „BSB-Adapter“ öffnet weiterhin direkt die Adapter-Seite.
- Demo: `06_BSB-Adapter/EMS-BSB_Demo.html` (im Browser öffnen, läuft ohne Home Assistant).

Geändert: bosch-ui800.html, ehs.py, panel.py, manifest.json.
