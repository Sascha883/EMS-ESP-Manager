# v1.22.6 – Bosch und Brötje sauber getrennt (08.11.2026)

Panel **2027.01.15** · baut auf v1.22.5 auf

- **Zuordnung (Bosch):** Unter „Sonstiges“ standen 13 Brötje-Werte (`bsbpick_…`, z. B. Heizkreispumpe Q2, Kollektorpumpe, Solarertrag) als „fehlt in HA“. Ursache: Die Menüs des Brötje-Panels melden ihre Werte in dieselbe Liste wie die Bosch-Menüs. Jetzt zeigt die Bosch-Zuordnung nur Bosch-/EMS-ESP-Werte, die Brötje-Zuordnung (EMS-BSB) nur Brötje-Werte und eigene Zuordnungen. Gilt auch für die Zählung „x von y gefunden“ unter Panel-Einstellungen.
- **Bosch-Panel → EMS-Regelung:** Eintrag „ESP-Bus-Adapter“ entfernt – er steht nur noch im Brötje-Panel.
- **Bosch-Dashboard „EMS-ESP“:** Reiter „ESP-Bus-Adapter“ entfernt (Brötje hat die eigene Seite „EMS-BSB“ bzw. den Reiter im EMS-EHS).

Geändert: `ems-panel.html` (Panel 2027.01.15), `dashboard_builder.py`, `manifest.json`.
JS-Syntax geprüft, Zuordnungsliste im Browser geprüft (Bosch: 0 statt 13 Brötje-Werte; Brötje: 41 statt alle Werte) – in HA noch nicht getestet.
