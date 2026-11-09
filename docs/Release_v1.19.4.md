# v1.19.4 – Dimm-Testprogramm (04.11.2026)

Panel 2027.01.07

**EMS-Regelung → Leistungsbegrenzung → Dimm-Testprogramm**
- Probiert nacheinander aus, welche Eingangsfunktion die Wärmepumpe wirklich drosselt. Dafür wird ein freier, nicht verdrahteter Eingang verwendet (Standard: Eingang 2).
- Schritte, einzeln wählbar:
  - EVU-Sperrzeit 1 (nicht an Eingang 4)
  - Kompressor sperren
  - Photovoltaikanlage (PV max. Kompressorleistung)
  - Expertenansicht: Begrenzungswert ohne Funktion (nur Eingang 1/4)
- Ablauf je Schritt:
  1. Warten, bis der Kompressor läuft (≥ 300 W, max. 10 min).
  2. Grundlast messen (60 s).
  3. Funktion setzen, solange der Eingang noch inaktiv ist.
  4. Eingang über „Eingang invertiert“ aktivieren und 5 min beobachten (P, Drehzahl, Kompressor, Betriebsart, Eingangszustand, 0x04A2).
  5. Eingang deaktivieren und Funktion zurückstellen.
  6. Erholung abwarten, bis wieder ≥ 70 % der Grundlast erreicht sind (max. 15 min).
- Ergebnis je Schritt: „gedimmt“ (Leistung ≥ 15 % geringer, Kompressor läuft weiter), „gesperrt“ (Kompressor aus) oder „keine Reaktion“, mit Reaktionszeit. Am Ende eine Empfehlung.
- Zeiten einstellbar. Abbrechen jederzeit möglich, dann stellt das Programm sofort alles zurück, auch nach einem HA-Neustart. Eingänge mit vorhandener Funktion werden abgelehnt.
- Protokoll im Panel, Download als `dimm-testprogramm_….txt`.
- Während eines Tests sind Dimmung ein/aus und das Schalten der Eingänge gesperrt (sonst würden die Aufrufe bis zum Testende hängen).

Geändert: `power_limit.py`, `panel.py`, `ems-panel.html`, `manifest.json`.
Nur in einer Simulation und im Browser geprüft – in HA noch nicht getestet.
