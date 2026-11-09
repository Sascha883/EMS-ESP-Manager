# v1.19.3 – Externe Eingänge aus Home Assistant einstellen (04.11.2026)

Panel 2027.01.06

**Externe Eingänge 1–4 (Service → Wärmepumpe → Externer Eingang)**
- Am 04.11.2026 an einer CS5800i mit UI 800 entschlüsselt: Jeder Menüpunkt eines Eingangs ist genau ein Byte im Telegramm 0x486. Das UI 800 schreibt es einzeln, die Wärmepumpe bestätigt sofort. Sie speichert also alle Funktionen, nicht nur „Eingang invertiert“ (Annahme vom 25.10. widerlegt).
- Je Eingang stehen die Schalter wie am UI 800 oben:
  - Eingang 1: Eingang invertiert, Zuheizerbetr. sperren, EVU-Sperrzeit 1
  - Eingang 2: Eingang invertiert, Zuheizerbetr. sperren, Heizbetrieb sperren
  - Eingang 3: Eingang invertiert, Zuheizerbetr. sperren, Überhitzungsschutz
  - Eingang 4: Eingang invertiert, Zuheizerbetr. sperren, Photovoltaikanlage
- Alle weiteren Funktionen aus 0x486 (EVU-Sperrzeit 2/3, Kompressor, Kühlen, Heizen, Warmwasser, Druckschalter …) in der Expertenansicht, dort auch der Offset je Schalter.
- Schreiben wie das UI 800: genau ein Byte (EMS-ESP `hpinNopt` mit x-Maske). Als „übernommen“ gilt ein Schalter erst, wenn die Wärmepumpe den neuen Wert selbst auf dem Bus meldet (0x486-Broadcast oder Antwort auf gezieltes Lesen).
- Sperr- und EVU-Funktionen fragen vor dem Einschalten nach.

**Neuer Test „Funktionen testen“** (je Eingang oder alle 4 Eingänge)
- Je Menüpunkt: Ausgangswert direkt bei der WP lesen → umschalten → Bestätigung abwarten (max. 10 s) → 3 s halten → zurückstellen → Bestätigung prüfen.
- Protokoll mit Rohtelegrammen, Ergebnis je Punkt (angenommen / nicht angenommen / keine Antwort) und Zusammenfassung „x von y übernommen“. Download als `eingangstest_….txt`.
- Wird das Zurückstellen nicht bestätigt, versucht der Manager es weiter (auch nach einem HA-Neustart).
- Expertenansicht: „Alle Funktionen testen“ für alle Bytes eines Eingangs.

**Leistungsbegrenzung**
- Funktionstest bricht bei aktiver Dimmung nicht mehr ab: Die Dimmung wird für den Test beendet, der Ausgangszustand neu gelesen, dann wird getestet.
- Bezeichnungen wie am UI 800 (EVU-Sperrzeit, Zuheizerbetr. sperren, Photovoltaikanlage, Überhitzungsschutz).

Geändert: `power_limit.py`, `panel.py`, `input_learn.py`, `ems-panel.html`, `manifest.json`.
Nur in einer Simulation und im Browser geprüft – in HA noch nicht getestet.
