# EMS-ESP Manager v1.13.17

## Externe Eingänge: klare Meldung, wenn die Wärmepumpe eine Funktion nicht übernimmt (Rückmeldung Sascha 25.10.)
- Im Bus-Log von 12:38–13:04 sieht man das Schreiben von „PV-Eingang“ (HpInConfig 0x486, Offset 39) und „Modulation“ (Offset 27). Die CS5800i hat beides nicht gespeichert: Es kam keine Bestätigung, und beim Zurücklesen stand an diesen Stellen 00.
- „Logik invertieren“ (Offset 0–2) übernimmt sie dagegen; die Wärmepumpe bestätigt das per Broadcast.
- Neue Fehlermeldung: „… die Wärmepumpe hat den Wert nicht gespeichert. Bei der CS5800i nimmt sie über 0x486 nur „Logik invertieren“ an; die Funktion bitte am UI 800 einstellen.“
- Bisher stand bei einem Fehler beim Schreiben eines Eingangs „Status lesen fehlgeschlagen“. Jetzt heißt es korrekt „Eingang n schreiben fehlgeschlagen“.
- Im Menü „Funktion des Eingangs“ steht jetzt ein entsprechender Hinweis für die CS5800i.

Panel-Version 2026.12.14. Dateien: bosch-ui800.html, power_limit.py, manifest.json. Home Assistant neu starten.
