# EMS-ESP Manager v1.17.3

Adapter-Suche (ESP-Bus-Adapter / BSB) – keine Fehlerkennungen mehr

- **Sonoff, ESPHome & Co. werden nicht mehr als BSB-LAN erkannt.** Bisher reichte es, wenn die Antwort auf `/JI` irgendwo „BSB“ enthielt. Jetzt muss sie ein echtes BSB-LAN-JSON sein (`name` = BSB-LAN oder `bus` = BSB/LPB/PPS zusammen mit typischen BSB-LAN-Feldern wie `buswritable`, `busaddr`).
- Weiterleitungen werden bei der Suche nicht mehr verfolgt.
- **Geräte, die Home Assistant schon kennt** (ESPHome, Shelly, Sonoff …), werden bei der Heimnetz-Suche übersprungen; im Such-Log steht, wie viele.
- **„Webgerät … (Anmeldung nötig – evtl. BSB-LAN)“** erscheint nur noch, wenn das Gerät HTTP-Basic-Anmeldung verlangt (wie BSB-LAN). Geräte mit Digest-Anmeldung (ESPHome-Webserver, eigener BSB-Adapter) tauchen dort nicht mehr auf. Bei manuell eingegebener Adresse wird ein geschütztes Gerät weiterhin angezeigt.
- Wurde versehentlich ein Nicht-BSB-LAN-Gerät gewählt, steht im Log jetzt eine klare Meldung statt „JSONDecodeError“.
