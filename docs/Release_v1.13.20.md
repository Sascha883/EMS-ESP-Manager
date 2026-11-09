# EMS-ESP Manager v1.13.20

## Heizstab (novolto): Startseite suchen und Experten-Seite in der Integration öffnen
- Die Web-Oberfläche des novolto antwortet auch im Heimnetz (getestet: http://192.168.0.17/), nicht nur im Hotspot 192.168.2.1.
- Bessere Erkennung: Die Startseite ist eine Web-App ohne Text. Erkannt wird der Heizstab jetzt an ihrem Skript (Einstellungen broker_uri, heat_msmt_ivl, legacy_info_ivl, temp_hyst_mode bzw. „novolto“). Nachgeladene Teile wie die Expert-Seite werden mitgeprüft.
- Einrichtungsdialog: neues Feld „Web-Adresse des Heizstabs“. Mit „Erneut suchen“ wird nur diese Adresse geprüft. Startseite und Experten-Seite erscheinen als Link. Der Heizstab lässt sich jetzt auch ohne MQTT anlegen; das Base-Topic wählst du später im Panel.
- Panel „Heizstab“ → Gerät:
  - „Web-Adresse“: Suche im Heimnetz oder Prüfung einer Adresse, mit Live-Protokoll.
  - „Experten-Seite“: im Panel anzeigen, bei https-Zugriff in neuem Fenster.
  - „Startseite in neuem Fenster“.
- Die Adresse wird in der Heizstab-Konfiguration gespeichert (Feld host).

Panel-Version 2026.12.17. Dateien: heater.py, config_flow.py, panel.py, const.py, bosch-ui800.html, strings.json, translations/de.json, translations/en.json, manifest.json. Home Assistant neu starten.
