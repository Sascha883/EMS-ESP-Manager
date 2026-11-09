# EMS-ESP Manager v1.13.32

- EMS-EHS → Heizstab: Der Hinweis „Weboberfläche antwortet nicht – meist neue IP“ ist ersetzt.
  - Läuft der Heizstab über MQTT (Daten kommen an), erscheint ein Info-Hinweis: Im MQTT-Betrieb ist die Weboberfläche nicht per IP erreichbar. Geräte-Einstellungen gehen nur kurz nach einem Neustart des Heizstabs oder im AP-Modus (Taste → WLAN „NOVOLTO-…“ → http://192.168.2.1).
  - Kennzeichen oben: „Web · nur AP-Modus“ statt „antwortet nicht“.
  - Gleicher Text in der Karte für die Geräte-Einstellungen.
  - Nur wenn auch keine MQTT-Daten kommen, bleibt die Warnung mit dem Hinweis auf die IP.

Datei: ehs.py, manifest.json.
