# v1.19.5 – Dimmung: richtige Schaltrichtung (04.11.2026)

Panel 2027.01.08

- **Fehler behoben:** Die Dimmung hat das Invertier-Bit des Eingangs bisher nur *umgedreht*. Stand der Eingang schon auf „invertiert“ (Kontakt offen = aktiv), wurde die Funktion beim Dimmen *ausgeschaltet* und beim Beenden wieder *eingeschaltet*. Mit EVU-Sperrzeit 1 an Eingang 1 war die Wärmepumpe danach dauerhaft gesperrt (Test 04.11. 16:10).
- Jetzt gilt immer: Dimmung an = „Eingang invertiert“ Ja (aktiv), Dimmung aus = Nein (inaktiv). Ist der Eingang schon dauerhaft invertiert, verweigert der Manager das Dimmen mit Hinweis.
- Funktionstest Weg A: Er misst zuerst 30 s mit inaktivem Eingang (schaltet ihn dafür bei Bedarf inaktiv und wartet 60 s), dann mit aktivem Eingang. Er erkennt, wenn der Kompressor ausgeht („Sperre, keine Dimmung“), und protokolliert die Betriebsart. Warnung, wenn der Eingang nach dem Test wieder dauerhaft aktiv ist.
- Erklärtext Weg A im Panel angepasst.

Geändert: `power_limit.py`, `ems-panel.html`, `manifest.json`.
