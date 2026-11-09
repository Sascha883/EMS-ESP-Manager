# v1.20.3 – „System & Panel“ immer vorhanden (08.11.2026)

Panel 2027.01.14 (unverändert) · baut auf v1.20.2 auf · gilt für Bosch, Samsung, Heizstab und Brötje

**Neu:** Die Seite „EMS-EHS“ mit dem Reiter **System & Panel** wird jetzt immer angelegt – egal ob nur Bosch, nur Samsung, nur Heizstab oder nur ESP-Bus-Adapter (Brötje) eingerichtet ist. Bisher gab es sie nur mit Samsung.

- Reiter „Samsung“ erscheint nur, wenn Samsung EHS eingerichtet ist; „Heizstab“ und „<Hersteller> EHS“ wie bisher nur mit dem jeweiligen Gerät.
- System & Panel ohne Samsung: Karten „EHS Sentinel“ und „Wärmepumpe“ sowie die Zeile „EHS Sentinel“ bei den Versionen entfallen; Untertitel zeigt die eingerichteten Hersteller.
- Neue Karte **Installierte Geräte** mit „Öffnen“-Knopf je Panel (bei Bosch zusätzlich Dashboard EMS-ESP).
- „Panel öffnen“, Kiosk-Adresse und Zuordnungszahl folgen dem eingerichteten Hersteller (Samsung → Bosch → Brötje → Heizstab).
- **Version & Panel** ist fest geöffnet (ohne Pfeil), alle anderen Karten lassen sich per Pfeil auf- und zuklappen.
- **WP-Auswertung zieht unter „System“ um** (vorher unter „Logs“):
  - Samsung: Samsung → System, Umschalter oben „System | WP-Auswertung“.
  - Brötje: Brötje EHS → System, Umschalter „System | Kessel-Auswertung“.
  - Bosch: Dashboard EMS-ESP → System & Firmware → Knopf „WP-Auswertung“ (Unteransicht mit Zurück-Pfeil, kein eigener Reiter mehr).
  - „Logs“ zeigt nur noch EHS-Log bzw. BSB-Log.
- Die Seite wird erst entfernt, wenn kein Gerät des Managers mehr geladen ist.

Geändert: `ehs.py`, `wpa.py`, `dashboard_builder.py`, `__init__.py`, `bsb.py`, `heater.py`, `manifest.json`.
Python- und JS-Syntax geprüft – in HA noch nicht getestet.
