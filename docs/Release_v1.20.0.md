# v1.20.0 – Brötje EHS: Zeitprogramme und Regler-Uhr (08.11.2026)

Panel 2027.01.13 · nur Dashboard „<Hersteller> EHS“ (ESP-Bus-Adapter / BSB-LAN)

**Zeitprogramme**
- Heizkreis 1, 2, 3 und Trinkwasser als Wochenübersicht mit Tagesbalken (je Tag bis 3 Phasen Ein–Aus)
- Tag antippen → Phasen in 10-min-Schritten einstellen, Phase hinzufügen/löschen, beim Speichern übernehmen für den Tag, Mo–Fr, Sa–So oder alle Tage
- Zu finden: Heizkreis → Mehr… → Programmzeiten · Trinkwasser → Mehr… → Programmzeiten · ≡ Menü → Zeitprogramme · Service → Anlageneinstellungen · ESP-Bus-Adapter → Zeitprogramme & Uhr
- Heizkreis-Kachel zeigt im Automatikbetrieb das aktuelle Niveau (Komfort / Reduziert) aus dem Zeitprogramm

**Regler-Uhr**
- ≡ Menü → Zeit / Datum: Uhrzeit und Datum des Reglers (läuft sekündlich weiter), Abweichung zu Home Assistant
- „Uhr jetzt stellen“ auf die Zeit von Home Assistant, Schalter „Automatisch stellen“ (ab 1 min Abweichung, max. 1× pro Stunde)
- Kachel Wärmeerzeuger zeigt die Reglerzeit statt der Zeit des Tablets

**Technik**
- Neu: `bsb_zeit.py`. Websocket `ems_esp_manager/panel/bsb` mit `zeit: {op: zp_set | uhr_set | auto | read}`, Antwort enthält immer `zeit` (Programme, Uhr, Fähigkeiten)
- ESP-Bus-Adapter: braucht Firmware **2.6.0** (Aktionen `bsb_zeitprogramm`, `bsb_uhrzeit`, Text-Entitäten „Zeitprogramm …“, „Regler Datum und Uhrzeit“). Ältere Firmware: Hinweis im Panel
- BSB-LAN: Lesen über /JQ (Zeilen 0, 501–507, 521–527, 541–547, 561–567), Schreiben über /JS – ungetestet
- Geändert: `panel.py`, `ems-panel.html`, `manifest.json`, README

Im Browser mit Demodaten geprüft (Brötje-Modus, keine Fehler), Python-Prüfung der Eingaben – in HA und am RVS43.345 noch nicht getestet.
