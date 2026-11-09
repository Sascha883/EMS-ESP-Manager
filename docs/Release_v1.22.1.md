# v1.22.1 – Bosch-Reiter: Unterseiten wieder bedienbar (08.11.2026)

Panel 2027.01.14 (unverändert) · baut auf v1.22.0 auf

**Fehler in v1.22.0:** Im Reiter „Bosch“ (Seite „EMS-EHS“) ließen sich die Unterseiten **Uhrzeit, MQTT, Bus-Log** und die **WP-Auswertung** nicht bedienen (keine Werte, Knöpfe ohne Wirkung, ggf. „Nur innerhalb von Home Assistant nutzbar“).

**Ursache:** Diese Seiten laufen als eingebettete Seite (iframe) und holen sich die HA-Verbindung über `window.parent`. Im alten Dashboard „EMS-ESP“ war das direkt Home Assistant – im neuen Reiter liegt dazwischen die Seite „EMS-EHS“, dort gibt es kein `home-assistant`-Element.

**Korrektur:** Die Verbindung wird jetzt über `window.parent`, `window.parent.parent` und `window.top` gesucht – funktioniert im Bosch-Reiter und weiterhin im alten Dashboard bzw. direkt.

Geändert: `ntp.py`, `mqtt.py`, `buslog.py`, `wpa.py`, `panel.py`, `ehs.py`, `manifest.json`.
Python-Syntax geprüft, Verbindungssuche mit verschachtelten Fenstern getestet – in HA noch nicht getestet.
