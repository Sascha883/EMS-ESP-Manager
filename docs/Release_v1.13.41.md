# EMS-ESP Manager v1.13.41

- Heizstab-Log: Bei eingeschalteter Aufzeichnung werden jetzt auch **unbekannte MQTT-Topics** des Heizstabs mitgeschrieben (alles unter `{Topic}/#`), mit Inhalt und Kennzeichnung „retained“. Damit lässt sich sehen, was der Heizstab z. B. bei einem Tastendruck, Neustart oder beim Wechsel in den AP-Modus sendet.
- Passwörter werden maskiert.

Datei: heater.py, manifest.json.
