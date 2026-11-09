# EMS-ESP Manager v1.13.23

## EMS-EHS → Reiter „Heizstab“: Einstellungen und Log auf einer Seite
- Der separate Reiter „Heizstab-Log“ entfällt. Das Log steht jetzt unten auf der Heizstab-Seite. Alte Links auf `#heizstablog` springen dorthin.
- **Hinweise**: Eine neue Karte oben zeigt nur, was gerade wichtig ist, jeweils mit dem nächsten Schritt:
  - MQTT fehlt
  - kein Topic gewählt
  - Gerät hängt an der novolto-Cloud
  - keine Messwerte
  - Störung des Heizstabs, auf Deutsch, z. B. „MQTT-Verbindung war unterbrochen“
  - Weboberfläche antwortet nicht, mit Hinweis auf eine neue IP und eine feste IP im Router
  - Regelung aus
  - Überschuss-Sensor fehlt
- **Status-Karte**: verständlichere Bezeichnungen wie „Daten kommen an“, „antwortet nicht“ oder „Heizt mit … W“, dazu das Alter der Messwerte.
- **Regelung**: Unter jeder Einstellung eine kurze Erklärung. Die Beschreibung passt zur gewählten Betriebsart, darunter stehen „Jetzt“ und „Zuletzt gesendet“.
- **Geräte-Einstellungen**:
  - Bei einem Fehler erscheint ein verständlicher Text, die technische Meldung klein darunter.
  - „MQTT umstellen“ ist eingeklappt, sobald der Heizstab schon an Home Assistant sendet. Dann gibt es nur „Zugangsdaten ändern“ und „Zurück zur Cloud“.
  - Kurze Erklärungen zu den Einstellungen im Gerät.
- **Heizstab-Log**:
  - Aufzeichnung mit Schalter ein- und ausschalten. Standard ist **aus**, bei Bedarf einschalten. Das Ein- und Ausschalten wird selbst im Log vermerkt.
  - Bei ausgeschaltetem Log schreibt die Integration auch keine Heizstab-Warnungen mehr ins Protokoll von Home Assistant.
  - **Herunterladen** als .txt mit Kopfzeile (Topic, Seriennummer, Web-Adresse, Filter) und tabulatorgetrennten Einträgen. Dazu Kopieren, Leeren, Pause und Filter.
  - „Verbindung prüfen“ funktioniert auch ohne Aufzeichnung. Das Ergebnis steht im Status und über dem Log.

Dateien: heater.py, panel.py, ehs.py, manifest.json. Home Assistant neu starten, Seite EMS-EHS mit Strg+F5 neu laden.
