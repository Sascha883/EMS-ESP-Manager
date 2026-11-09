# v1.22.3 – Sicheres Entfernen & Dimm-Testprogramm (08.11.2026)

Panel 2027.01.14 (unverändert) · baut auf v1.22.2 auf

## Integration löschen
- Wird der letzte EMS-ESP-Eintrag gelöscht, nimmt der Manager vorher seine Eingriffe an der Wärmepumpe zurück:
  - aktive **Dimmung** (Eingang invertiert bzw. `hppowerlimit`) → ursprünglicher Wert,
  - offene **Test-Rücksetzungen** (Eingangstest, Dimm-Testprogramm, Leistungsverringerung),
  - aktive **Warmwasseroptimierung** → alte Betriebsart und Stopptemperatur.
  Klappt das nicht (Gateway offline), steht eine Warnung im HA-Protokoll mit den Werten zum Prüfen am UI 800.
- Seitenleiste „Bosch“ und die Regelungen werden auch entfernt, wenn der Eintrag nie fertig eingerichtet war (Gateway offline beim Start).
- **Heizstab-Gerät gelöscht:** Regelung wird ausgeschaltet und der Heizstab abgeschaltet – auch wenn andere Geräte (Bosch/Samsung) weiterlaufen.

## Dimm-Testprogramm
- Ließ sich beim Zurückstellen ein Bit nicht bestätigen oder war der Zustand nicht lesbar (Bus belegt), wurde es trotzdem als erledigt markiert – im schlimmsten Fall blieb z. B. „Kompressor sperren“ dauerhaft aktiv.
- Jetzt: nicht lesbare Bits werden sicherheitshalber geschrieben; nicht bestätigte Bits bleiben gespeichert und werden alle 30 s erneut geschrieben, bis es klappt (max. ~1 h, dann Warnung). Hinweis „ACHTUNG … bitte am UI 800 prüfen“ im Protokoll.
- Gleiches gilt nach jedem einzelnen Schritt (Eingang deaktivieren, Funktion zurück, Begrenzungswert).

Geändert: `__init__.py`, `power_limit.py`, `dhw_optimizer.py`, `manifest.json`.
Python-Syntax geprüft – in HA noch nicht getestet.
