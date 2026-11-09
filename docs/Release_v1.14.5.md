# EMS-ESP Manager v1.14.5

Dashboard EMS-EHS: Reiter **BSB-Adapter** im Aufbau der Heizstab-Seite (statt eingebettetem Panel)

- Kopf mit Status-Kennzeichen (Adapter verbunden, Abfrage bsb_get, Bus-Abfrage, Mitschnitt), „Regler erkennen“ und Kacheln (Regler, Parameter, zwei Favoriten-Werte, Telegramme bzw. Fortschritt eines laufenden Scans).
- Hinweise mit nächstem Schritt (kein Adapter, offline, Firmware ohne bsb_get, Regler nicht erkannt, kein Scan, Bus-Abfrage ohne Favoriten).
- Unterseiten:
  - **Werte & Favoriten:** Bus-Abfrage ein/aus, Intervall, Favoriten, Parameterliste mit Suche, Bereichsfilter, neu abfragen und ☆/★.
  - **Scan & Anlernen:** Parameter-Scan mit Fortschritt und Protokoll, ID-Suchlauf (fertige Bereiche oder eigener Bereich), unbekannte Werte mit Name und Datentyp anlernen.
  - **Adapter:** gewählter Adapter, Suche (Home Assistant / Heimnetz / BSB-LAN-Adresse), Übernehmen, Werte des ESPHome-Adapters direkt bedienen.
  - **Info & Log:** Regler-Daten, Bus-Teilnehmer, BSB-Log mit Mitschnitt-Schalter, Filter, Pause, Herunterladen, Kopieren, Leeren.

Geändert: ehs.py, manifest.json.
