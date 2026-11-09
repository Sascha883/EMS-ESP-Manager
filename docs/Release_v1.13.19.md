# EMS-ESP Manager v1.13.19

## Anlern-Modus: Broadcasts erkennen, Messwerte ausblenden (Bus-Log 25.10. 15:07–15:24)
- Die CS5800i bestätigt Einstellungen per Broadcast („boiler(0x08) B all(0x00), HpInConfig(0x0486) …“). Diese Zeilen hat der Anlern-Modus bisher nicht ausgewertet. Jetzt werden sie erkannt und zählen als Rückmeldung der Wärmepumpe.
- Messwert-Telegramme (0xE3/E4/E5/E9, 0x48D, 0x48F, 0x494, 0x495, 0x4A2, 0x4AA, 0x4AF) werden ausgeblendet. Im Testlog waren das über 260 Änderungen in 12 min.
- Als Einstellung gilt nur noch ein Byte, das sich im Fenster genau einmal ändert. 0x486 steht immer oben.
- Leere Schreibzugriffe des UI 800 gelten nicht mehr als „neu“.

## Auswertung Bus-Log/Test 15:07–15:24
- In diesem Zeitraum wurde am UI 800 keine Eingangsfunktion geändert. Das UI 800 hat nur seine regelmäßigen Einstellungen geschrieben (0x23E, 0x240, 0x291–0x294, 0x29B …). Die Wärmepumpe fragt diese etwa jede Minute beim UI 800 ab; 0x486 ist nicht dabei, die Eingangskonfiguration liegt also in der Wärmepumpe selbst.
- Invertier-Bit Eingang 3: geschrieben, per Broadcast bestätigt (15:21:35) und zurückgesetzt (15:22:40).
- Der Kompressor war aus (25 W). Eine Wirkung der Dimmung war deshalb nicht prüfbar.

Panel-Version 2026.12.16. Dateien: input_learn.py, power_limit.py, bosch-ui800.html, manifest.json. Home Assistant neu starten.
