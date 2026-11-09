# EMS-ESP Manager v1.13.40

## Heizstab: Geräte-Einstellungen über MQTT
Im MQTT-Betrieb sperrt der novolto seine Weboberfläche. Der Manager liest und schreibt die Einstellungen jetzt auch über MQTT:
- **Lesen**: `{Topic}/settings/get` mit `{"mqtt":[],"wifi":[],"heating":[]}` (leere Liste = alle Werte). Die Antwort kommt über `{Topic}/settings/reply` im selben Format wie die Experten-Seite.
- **Schreiben**: `{Topic}/settings/set` mit `{Bereich:{Einstellung:Wert}}`. Vorher wird gegen die vom Gerät gemeldeten Grenzen geprüft. Die Regelung pausiert dabei kurz.
- **Reihenfolge**: zuerst die Weboberfläche. Antwortet sie nicht, wird MQTT genommen, und das bleibt 2 min so, ohne erneute Wartezeit.
- **Anzeige**:
  - Kennzeichen „Einstellungen · über MQTT“
  - Hinweis in den Karten „Wird sofort im Heizstab gespeichert – über MQTT“
  - Warnung in „Verbindung zu Home Assistant“: Falsche Broker-Daten trennen die Verbindung, dann hilft nur noch der AP-Modus.
- **Sicherheit**: Passwörter erscheinen in keinem Log, weder gesendet noch empfangen.

Datei: heater.py, panel.py, ehs.py, manifest.json. Home Assistant neu starten, EMS-EHS mit Strg+F5 neu laden.
