# v1.22.7 – Bosch-Menü wie Brötje EHS (08.11.2026)

Panel **2027.01.15** (unverändert) · baut auf v1.22.6 auf

- **EMS-EHS → Reiter „Bosch“:** Menü neu aufgebaut wie bei „Brötje EHS“ – vier Hauptpunkte als Chips: **Übersicht · Werte · System · Logs**.
  - Übersicht = bisher „System & Firmware“ (inkl. WP-Auswertung)
  - Werte = Einstellungen / Messwerte (Umschalter oben)
  - System = Zuordnung / MQTT / Uhrzeit (Umschalter oben)
  - Logs = bisher „Bus-Log“
- Jeder Hauptpunkt merkt sich die zuletzt gewählte Unterseite.
- **Weboberfläche entfernt** (EMS-ESP-Weboberfläche bleibt über „System & Panel“/den Gerätelink erreichbar).

Geändert: `ehs.py`, `manifest.json`. JS-Syntax geprüft, Darstellung im Browser geprüft (Desktop/Handy) – in HA noch nicht getestet.
