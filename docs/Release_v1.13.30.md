# EMS-ESP Manager v1.13.30

## EMS-EHS → Verbindung & Log
- Eine Ansicht ohne Unterauswahl: links die Verbindungsdaten von EHS Sentinel, rechts das **EHS-Log**. Die Karte „Protokoll“ entfällt.
- Das EHS-Log ist gestaltet wie das Heizstab-Log:
  - Schalter **Aufzeichnung ein/aus**
  - Protokollstufe
  - Filter: Alle, Info+, Warnungen, Fehler, Paket-Log
  - Diagnose, Pause, **Herunterladen** (.txt mit Kopfzeile), Kopieren, Leeren
  - Statuszeile zum Adapter
  - Liste mit eigenem Scrollbereich
- Bei ausgeschalteter Aufzeichnung nimmt der Manager keine Meldungen von EHS Sentinel mehr auf. Das Home-Assistant-Log bleibt davon unberührt.
- Neuer WebSocket-Befehl `ems_esp_manager/samsung/log_enable`. Der Status meldet `log_enabled`.

Datei: ehs.py, manifest.json. Home Assistant neu starten, EMS-EHS mit Strg+F5 neu laden.
