## v1.11.9 – EMS control & hot-water optimisation / EMS-Regelung & Warmwasseroptimierung

### 🇬🇧 English

**New**

* **EMS control** (start page tile, replaces "Display cleaning"). Control functions of the EMS-ESP Manager. They run in Home Assistant, even when the panel is closed.
* **Hot-water optimisation** (first EMS control function). When a lot of sun is expected, the tank is heated to a higher temperature (default 60 °C), so the heat pump runs on your own PV power.
  * Forecast source selectable:
    * a **PV forecast** entity in kWh (e.g. Forecast.Solar, Solcast), or
    * a **weather** entity (average cloud coverage in the time window).
  * Optional **PV surplus** check (entity in W/kW, positive = feed-in).
  * The optimisation starts once a day inside the time window (default 11:00–15:00) if forecast and surplus are OK and the tank is not yet hot. It then sets hot-water mode **Comfort** and the comfort stop temperature to the target temperature.
  * It ends when the target temperature is reached, the time window is over or the function is switched off. Mode and stop temperature are then restored to their previous values.
  * The status page shows the last check with forecast, surplus and tank temperature. Tap "Status" to check immediately.
* **Hot-water temperature history** (Hot water → More… → "Verlauf Warmwassertemperatur"). Chart of today (0–24 h) with actual temperature (orange) and setpoint (blue dashed) from the Home Assistant history. The current setpoint and actual temperature are shown at the top.
* **Show cooling functions** (Service → Heating / Cooling). Hides all cooling items in the panel when switched off. The setting is stored in the manager and applies to all devices.

**Changed**

* **Heating menu:**
  * **Heating curve** moved from the service menu to the heating menu, directly below "Time program".
  * "Boost" and "Rename heating circuit" are only shown with "Extended view" switched on.
* **Hot-water menu:** new order: "Program times" and "Hot-water temperature history" at the top. New **Info** section at the bottom (as in the heating menu) with "Charging active", "Setpoint temperature" and "Actual temperature". "Thermal disinfection" is only shown with "Extended view" switched on.
* **Time program:** "Switching times" renamed to **"Program times"** and moved directly below "Program".
* **Heating curve chart:**
  * Setpoint shown as a **vertical blue dashed line** at the current outdoor temperature, labelled "Soll" at the top.
  * Current **actual flow temperature** shown as a **green dashed line** ("Ist").
* **Heating tile:** the line below the set temperature now reads e.g. **"Vorlauf-Soll 35° Heizen"**, i.e. calculated flow setpoint plus the current level.

**Fixed**

* **Current level (Heating / Setback):** EMS-ESP reports the wrong level for the UI 800 (shows "Setback" although the program says "Heating"). The panel now takes the level from the program times read from the controller. Fallback: compare the current set temperature with the heating / setback temperature.
* **Program without switching points:** if the controller has no program times stored, the panel shows **"ganztägig ☀"** (all day heating) with a hint, instead of empty days.

**Update**

Update via HACS (or replace the folder `custom_components/ems_esp_manager`), **restart Home Assistant** and reload the panel once (Ctrl+F5). Your mappings, heating circuit names and saved schedules are kept.

**Panel version:** 2026.11.18

---

### 🇩🇪 Deutsch

**Neu**

* **EMS-Regelung** (Kachel auf der Startseite, ersetzt „Display-Reinigung“). Regelfunktionen des EMS-ESP Managers. Sie laufen in Home Assistant, auch wenn das Panel geschlossen ist.
* **Warmwasseroptimierung** (erste EMS-Regelfunktion). Ist viel Sonne zu erwarten, wird der Speicher höher aufgeheizt (Standard 60 °C), damit die Wärmepumpe mit eigenem PV-Strom läuft.
  * Quelle der Vorhersage wählbar:
    * eine **PV-Prognose**-Entität in kWh (z. B. Forecast.Solar, Solcast) oder
    * eine **Wetter**-Entität (mittlere Bewölkung im Zeitfenster).
  * Optional Prüfung des **PV-Überschusses** (Entität in W/kW, positiv = Einspeisung).
  * Die Optimierung startet einmal am Tag im Zeitfenster (Standard 11:00–15:00), wenn Vorhersage und Überschuss passen und der Speicher noch nicht warm ist. Dann setzt sie die Warmwasser-Betriebsart auf **Komfort** und die Komfort-Stopptemperatur auf die Zieltemperatur.
  * Sie endet, wenn die Zieltemperatur erreicht, das Zeitfenster vorbei oder die Funktion ausgeschaltet ist. Betriebsart und Stopptemperatur werden dann auf die vorherigen Werte zurückgesetzt.
  * Die Statusseite zeigt die letzte Prüfung mit Prognose, Überschuss und Speichertemperatur. „Status“ antippen prüft sofort.
* **Verlauf Warmwassertemperatur** (Warmwasser → Mehr… → „Verlauf Warmwassertemperatur“). Grafik für heute (0–24 Uhr) mit Ist-Temperatur (orange) und Solltemperatur (blau gestrichelt) aus der Home-Assistant-Historie. Oben stehen die aktuellen Werte für Soll und Ist.
* **Kühlfunktionen anzeigen** (Service → Heizung / Kühlung). Im ausgeschalteten Zustand blendet das Panel alle Kühl-Punkte aus. Die Einstellung wird im Manager gespeichert und gilt auf allen Geräten.

**Geändert**

* **Heizen-Menü:**
  * **Heizkurve** vom Servicemenü ins Heizen-Menü verschoben, direkt unter „Zeitprogramm“.
  * „Boost“ und „Heizkreis umbenennen“ erscheinen nur bei eingeschalteter „Erweiterter Ansicht“.
* **Warmwasser-Menü:** neue Reihenfolge: „Programmzeiten“ und „Verlauf Warmwassertemperatur“ stehen oben. Neuer Bereich **Info** unten (wie im Heizen-Menü) mit „Ladung aktiv“, „Solltemperatur“ und „Isttemperatur“. „Thermische Desinfektion“ erscheint nur bei eingeschalteter „Erweiterter Ansicht“.
* **Zeitprogramm:** „Schaltzeiten“ heißt jetzt **„Programmzeiten“** und steht direkt unter „Programm“.
* **Heizkurve:**
  * Das Soll erscheint als **senkrechte blau gestrichelte Linie** bei der aktuellen Außentemperatur, oben beschriftet mit „Soll“.
  * Die aktuelle **Ist-Vorlauftemperatur** erscheint als **grün gestrichelte Linie** („Ist“).
* **Heizkreis-Kachel:** Die Zeile unter der Wunschtemperatur lautet jetzt z. B. **„Vorlauf-Soll 35° Heizen“**, also berechnetes Vorlauf-Soll und aktuelles Niveau.

**Behoben**

* **Aktuelles Niveau (Heizen / Absenken):** EMS-ESP meldet beim UI 800 ein falsches Niveau („Absenken“, obwohl laut Programm geheizt wird). Das Panel ermittelt das Niveau jetzt aus den vom Regler gelesenen Programmzeiten. Ersatzweise vergleicht es die aktuelle Wunschtemperatur mit der Heizen- bzw. Absenktemperatur.
* **Programm ohne Schaltpunkte:** Sind im Regler keine Programmzeiten hinterlegt, zeigt das Panel **„ganztägig ☀“** mit Hinweis statt leerer Tage.

**Update**

Über HACS aktualisieren (oder den Ordner `custom_components/ems_esp_manager` ersetzen), **Home Assistant neu starten** und das Panel einmal neu laden (Strg+F5). Zuordnungen, Heizkreis-Namen und gespeicherte Programmzeiten bleiben erhalten.

**Panel-Version:** 2026.11.18
