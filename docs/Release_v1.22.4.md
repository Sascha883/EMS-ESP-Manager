# v1.22.4 – Log-Aufzeichnungen: standardmäßig aus, Hinweis, keine DB-Flut (08.11.2026)

Panel 2027.01.14 (unverändert) · baut auf v1.22.3 auf

## Bus-Log ohne Datenbank-Einträge
- Der Sensor „Bus-Log“ zeigt nur noch den Zustand (*Aufzeichnung aus / läuft / Stream getrennt*) und wird nur bei einer Änderung geschrieben. Vorher erzeugte jede Log-Zeile einen neuen Eintrag in der HA-Datenbank (~1 pro Sekunde).
- Die Live-Ansicht unter „Bosch → Bus-Log“ ist jetzt eine eigene Seite, die die Zeilen alle 2 s per Websocket holt (nur bei Änderung, pausiert im Hintergrund, Knopf „Anhalten“).

## Alle Aufzeichnungen standardmäßig aus
- EMS-ESP Bus-Log, EHS-Sentinel-Log, Heizstab-Log und Bus-Mitschnitt des ESP-Bus-Adapters starten nach **jedem Neustart von Home Assistant ausgeschaltet** – auch wenn sie vorher eingeschaltet waren. Einschalten wie bisher in der jeweiligen Log-Ansicht.

## Hinweis unter „System & Panel“
- Läuft irgendwo eine Aufzeichnung, erscheint oben auf „System & Panel“ (auch EMS-BSB) ein gelber Hinweis mit allen laufenden Logs, je „Ansehen“ und „Ausschalten“, bei mehreren zusätzlich „Alle ausschalten“ (Ausschalten nur für Administratoren).
- Neuer Websocket-Befehl `ems_esp_manager/panel/logs` (optional `off`: bus · ehs · heater · bsb · all).

Geändert: `buslog.py`, `log.py`, `sensor.py`, `dashboard_builder.py`, `heater.py`, `bsb.py`, `panel.py`, `ehs.py`, `manifest.json`.
Python- und JS-Syntax geprüft, Hinweis und Live-Log mit Testdaten im Browser geprüft – in HA noch nicht getestet.
