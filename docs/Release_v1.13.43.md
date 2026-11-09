# EMS-ESP Manager v1.13.43

- Heizstab: Hat sich der Heizstab bei MQTT abgemeldet (offline, z. B. nach Tastendruck), meldet die Verbindungsprüfung das jetzt richtig: „Heizstab hat sich abgemeldet … Weboberfläche erreichbar“. Vorher stand dort bis zu 60 s lang fälschlich „Heizstab sendet“, abwechselnd mit „Regelung pausiert“.

Datei: heater.py, manifest.json.
