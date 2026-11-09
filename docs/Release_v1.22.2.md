# v1.22.2 – Prüfung & Optimierung (08.11.2026)

Panel 2027.01.14 (unverändert) · baut auf v1.22.1 auf

## Bosch-Reiter (EMS-EHS)
- Werte springen nach einer Änderung nicht mehr kurz auf den alten Wert zurück (Zahlen, Auswahlen, Schalter, Betriebsart: 10 s Schonfrist bis zur Rückmeldung).
- Leeres Soll-Feld schickt nicht mehr 0 °C an Heizkreis/Warmwasser; − / + bei leerem Feld ohne Wirkung.
- Bedingte Anzeigen (z. B. „Neueste installieren“ nach Update-Suche, Update-Fortschritt) und Bedienelemente, die beim Öffnen „n. v.“ waren, erscheinen jetzt ohne Neuladen.
- Jeder Klick auf eine Unterseite zeichnete die Seite doppelt (iframes luden zweimal) – behoben.
- Vorlagen-Abos (Markdown) werden beim Verlassen der Seite geschlossen.
- Messwerte: nur geänderte Werte werden neu geschrieben (weniger Last, Text bleibt markierbar).

## Sicherheit
- **Heizstab:** Kam das Ausschalten beim Deaktivieren der Regelung oder beim Gerätewechsel nicht an (MQTT kurz weg), blieb der Heizstab an. Jetzt wird das Ausschalten bei jedem Regeltakt nachgeholt; beim Entladen/HA-Stopp wird immer ausgeschaltet, wenn er zuletzt eingeschaltet war.
- **Leistungsbegrenzung (Weg B):** Ist `hppowerlimit` nicht lesbar, wird nichts geschrieben (vorher wurde beim Zurücksetzen 0 W geschrieben).
- **Warmwasseroptimierung:** Wetterabfrage mit 20 s Zeitlimit – ein hängender Wetterdienst hat die Optimierung sonst dauerhaft angehalten.

## Stabilität
- HA-Start: GitHub-Abfrage im Hintergrund (vorher bis zu 60 s Wartezeit ohne Internet).
- Firmware-Update EMS-ESP: Uptime als Text („000+01:02:03“) bricht die Fortschrittsanzeige nicht mehr ab.
- ESP-Bus-Adapter: OTA-Senden mit 30 s Zeitlimit (hing sonst endlos); keine Fehlermeldung mehr beim HA-Herunterfahren; Suche wird beim Entladen beendet.

Geändert: `ehs.py`, `heater.py`, `power_limit.py`, `dhw_optimizer.py`, `__init__.py`, `firmware.py`, `esp_ota.py`, `bsb.py`, `manifest.json`.
Python- und JS-Syntax geprüft – in HA noch nicht getestet.
