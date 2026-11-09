# v1.19.6 – Dimm-Testprogramm: alle Eingangsfunktionen + Leistungsverringerung (04.11.2026)

Panel 2027.01.09

- **Dimm-Testprogramm** prüft jetzt nacheinander alle Eingangsfunktionen:
  - EVU-Sperrzeit 1, 2 und 3
  - Kompressor sperren
  - Heizbetrieb sperren
  - Warmwasser sperren
  - Photovoltaikanlage
  - Begrenzungswert (Expertenansicht)

  Standard: EVU 1–3, Kompressor sperren, Heizbetrieb sperren. EVU-Schritte entfallen an Eingang 4.
- **Neu: „Leistungsverringerung während des Tests“** (Standard 30 %). Vor dem ersten Schritt setzt das Programm `power_reduction` auf diesen Wert und am Ende auf den alten Wert zurück, auch nach Abbruch oder Neustart.
- Die Auswertung zeigt bei „gedimmt“ den erreichten Prozentwert und ob er zur eingestellten Leistungsverringerung passt (±15 %). So lässt sich erkennen, welche Funktion die Leistungsverringerung auslöst.
- PV-Schritt mit Hinweis „hebt die Leistung eher an“ (Test 04.11.).

Geändert: `power_limit.py`, `ems-panel.html`, `manifest.json`. Nur simuliert, in HA ungetestet.
