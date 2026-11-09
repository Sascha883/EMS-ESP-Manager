# EMS-ESP Manager v1.13.44

Panel 2026.12.21 – Heizstab-Menü neu aufgebaut (Bosch und Samsung):

- Menü „EMS-Regelung“: Punkt „Heizstab-Einstellungen & Log“ (Samsung) entfernt.
- Menü „Heizstab“ übersichtlicher, gegliedert wie die Heizstab-Seite auf EMS-EHS:
  - **Regelung durch Home Assistant:** Heizstab-Regelung, Betriebsart, Überschuss-Sensor, Vorzeichen, Höchstleistung (bei „Feste Leistung“: Leistung)
  - **Speicher & Freigabe:** Maximaltemperatur
  - **Info:** Leistung, Wassertemperatur, Netzspannung, Tagesenergie (mit Wert von gestern)
  - Kurze Erklärungen unter den Einstellungen (z. B. „darunter aus“, „bleibt als Einspeisung übrig“).
- Neuer Schalter **„Erweiterte Ansicht“** oben im Heizstab-Menü. Eingeschaltet erscheinen zusätzlich Reserve, Mindestleistung, Abschaltverzögerung, Regelintervall, Hysterese und **Einschaltpunkt Heizstab** (Maximaltemperatur − Hysterese).
- Nicht mehr im Heizstab-Menü des Panels: Status, Geräteauswahl, Web-Adresse/Experten-Seite, Geräte-Einstellungen, Freigabe-Signal und Akku-Ladestand sowie die Hinweistexte. Bereits gespeicherte Werte gelten weiter.

Dateien: bosch-ui800.html, manifest.json.
