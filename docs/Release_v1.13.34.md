# EMS-ESP Manager v1.13.34 – Code-Prüfung

Drei unabhängige Prüfungen (Heizstab/Panel, EMS-EHS-Seite, Regelfunktionen) mit anschließender Korrektur. Getestet in der Demo (+/−, Logs, Filter, leere Felder) und mit ruff (F, B, ASYNC).

## Heizstab (heater.py)
- **Neu laden der Integration**: Beim Entladen werden laufende Suchen und Prüfungen abgebrochen. MQTT-Abos, die während des Wartens auf den Broker entstehen, werden sofort wieder gelöst. Vorher konnten doppelte Abos und eine zweite, verwaiste Regelung entstehen.
- **MQTT-Start**: Schlägt der Start fehl oder ist MQTT noch nicht bereit, gibt es einen neuen Versuch nach 60 s. Es läuft nur ein Wiederholungs-Timer, vorher wuchs die Liste ohne MQTT endlos.
- **Regelung**: Ein Regeldurchlauf läuft nie mehr parallel zu einem zweiten. Das verhindert widersprüchliche Befehle.
- **Mindestleistung** wird auf das Raster aufgerundet und nie unterschritten. Beispiel: Minimum 300 W bei 200-W-Schritten ergab vorher 200 W.
- **Sicherheit**: Kommen über 3 min keine Messwerte, wird ein eingeschalteter Heizstab ausgeschaltet.
- Die **Abschaltverzögerung** startet nach einem Wechsel der Betriebsart oder der Freigabe wieder korrekt.
- **Speichern**: Tagesenergie und Zustand werden beim Entladen und beim Herunterfahren gespeichert, vorher nur alle 10 min.
- **Base-Topic**: Ein im Panel gewähltes Topic wird beim Neustart nicht mehr durch das Topic aus der Einrichtung ersetzt.
- **Hintergrundprüfung** läuft nur einmal gleichzeitig. Bei einem offline Heizstab stauen sich keine Zeitüberschreitungen mehr.
- **Heimnetz-Suche** stürzt mit einem Hostnamen statt einer IP nicht mehr ab.
- **Datenschutz**: Zugangsdaten in der Broker-Adresse werden ausgeblendet. Im Log werden alle Felder mit pass, pw, psk, secret, token oder key maskiert.

## Leistungsbegrenzung (power_limit.py)
- Bei nicht erreichbarem EMS-ESP stauen sich keine Regel-Aufgaben mehr: früher alle 30 s eine neue mit je bis zu 10 min Abfragen. Einzelwerte werden nur noch bei HTTP-Fehlern abgefragt, nicht bei fehlender Verbindung.
- Läuft das Schalten des Eingangs in eine Zeitüberschreitung, wird nachgelesen, ob es trotzdem übernommen wurde. Vorher konnte der nächste Durchlauf den Eingang zurückdrehen und den falschen Ausgangswert speichern.
- Ist die Leistungsgrenze nicht lesbar, startet die Dimmung nicht. Vorher hätte „zurücksetzen“ später 0 W geschrieben.
- Ist der Auslöser 15 min unbekannt oder entfernt, wird die Dimmung beendet.
- Ein Zurücksetzen, das nie gelingt, blockiert die Regelung nicht mehr dauerhaft. Nach 120 Versuchen (ca. 1 h) wird abgebrochen und ins Log geschrieben.

## Warmwasseroptimierung (dhw_optimizer.py)
- „Jetzt prüfen“ bzw. Speichern und der Timer können nicht mehr gleichzeitig starten. Vorher konnten Komfort/60 °C als „vorheriger Zustand“ gemerkt werden und dauerhaft bleiben.
- Schlägt der Start fehl, gibt es heute keinen weiteren Versuch. Vorher wurde alle 5 min umgeschaltet.

## Sensoren
- Die Laufzeit (uptime) funktioniert auch, wenn EMS-ESP sie als Text liefert (z. B. „000+01:02:03“).

## Seite EMS-EHS (ehs.py)
- **Einstellungen**: Ein leeres oder ungültiges Zahlenfeld schickt nicht mehr das Minimum an die Wärmepumpe. Schalter springen nicht mehr kurz zurück.
- **Logs**:
  - Veraltete Antworten werden verworfen, z. B. beim Wechsel zum Paket-Log.
  - Es wird nur die Liste neu gezeichnet, eine offene Auswahl (Protokollstufe) bleibt offen.
- **Upload**: Die Rückfrage „Übernehmen?“ verschwindet nicht mehr nach 15 s.
- **Heizstab**:
  - Nach dem Speichern wird nicht neu gezeichnet, während in einem Textfeld getippt wird.
  - „Prüfen“ stürzt nicht mehr ab, wenn der Heizstab nicht antwortet.
- **Leistung**:
  - Entitäten werden über einen Index gesucht statt bei jedem Aufruf in allen Zuständen (Wärmepumpen-Kacheln alle 3 s).
  - Die Auswahllisten werden nur bei Änderungen neu aufgebaut.
  - Die Timer arbeiten nur für sichtbare Reiter, nicht bei verstecktem Fenster und nie überlappend.
  - Das Heizstab-Log wird nur in der Ansicht „Info & Log“ geladen.
- **Sicherheit**: Werte aus Gerät und Entitäten in Eingabefeldern werden maskiert.
- Toter Code entfernt.

Dateien: heater.py, panel.py, power_limit.py, dhw_optimizer.py, sensor.py, ehs.py, manifest.json. Home Assistant neu starten, Seiten mit Strg+F5 neu laden.
