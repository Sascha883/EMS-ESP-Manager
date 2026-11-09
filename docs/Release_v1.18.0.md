# EMS-ESP Manager v1.18.0

ESP-Bus-Adapter koppeln (API-Schlüssel) – passend zur Adapter-Firmware 2.4.0

**Koppeln mit API-Schlüssel**
- Dashboard „Brötje EHS“ → ESP-Bus-Adapter → Adapter: neue Zeile **„ESPHome-Adapter koppeln“** (Adresse + Schlüssel).
  Der Manager prüft den Schlüssel direkt am Adapter, bindet ihn in Home Assistant (ESPHome) ein oder aktualisiert den Schlüssel eines vorhandenen Eintrags und wählt ihn als ESP-Bus-Adapter.
- Den Schlüssel erzeugt bzw. zeigt die Adapter-Weboberfläche (System & Update → Home Assistant → Kopieren).
- Neue Zeile **„Home Assistant“** beim gewählten Adapter: verbunden / getrennt / „Schlüssel passt nicht“ / nicht eingebunden.

**Adapter-Suche**
- „Adresse prüfen“ erkennt jetzt auch ESPHome-Adapter (Port 6053), die noch nicht in Home Assistant eingebunden sind → Schaltfläche „Koppeln“.
- Von Home Assistant gefundene, noch nicht eingebundene ESPHome-Geräte erscheinen mit „Koppeln“.
- Eigene ESP-Bus-Adapter (Projekt matheis.esp-bus-adapter) werden immer gefunden – auch offline oder ohne Entitäten.
- Heimnetz-Suche: Webgeräte mit Anmeldung (z. B. Router, Kameras) erscheinen nicht mehr in der Liste, nur noch im Such-Log.

**Firmware-Update des Adapters**
- Ab Adapter-Firmware 2.4 steht der Schlüssel nicht mehr in der .bin. Der Manager erkennt das („✓ API-Schlüssel im Adapter gespeichert“) und überträgt nach dem Update den bisherigen Schlüssel aus Home Assistant in den Adapter – Home Assistant bleibt verbunden.

(enthält v1.17.3: keine Fehlerkennung von Sonoff/ESPHome-Geräten als BSB-LAN)
