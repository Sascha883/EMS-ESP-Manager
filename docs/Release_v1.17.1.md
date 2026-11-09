# EMS-ESP Manager v1.17.1

Heizkreise umbenennen – Panel 2027.01.05

- Neuer Punkt **Menü → Heizkreise umbenennen**: listet alle gefundenen Heizkreise (Bosch/Samsung: HK1–HK4, Brötje/BSB: HK1/HK2) mit ihrem aktuellen Anzeigenamen.
- Antippen → Namen eingeben (max. 20 Zeichen) → Speichern. Leer lassen = Standardname.
- Der Name erscheint auf der Startseite, in den Heizkreis-Menüs und im Brötje-Servicemenü (Diagnose → Status). Er wird im Manager gespeichert und gilt für alle Panels.
- Heizkreis-Kachel auf der Startseite: Unterzeile jetzt **„Vorlauf-Soll 43 °C · Ist 21 °C“** (Vorlauf-Soll zuerst, Raum-Istwert danach, nur ganze Grad).

### Korrekturen (Rückmeldung Sven, 21.10.2026)
- **Warmwasser-Zeitprogramm: Eco und Eco+ waren vertauscht.** Die Stufe je Schaltpunkt (Telegramm 0x02FF) ist jetzt 01 = Eco, 02 = Eco+ (vorher umgekehrt). Das betraf die Anzeige und das Schreiben an den Regler. Bereits gelesene Zeitprogramme werden nach dem Update automatisch richtig angezeigt.
- Zeitprogramm ohne Schaltpunkte: beim Warmwasser steht jetzt „ganztägig Eco“ statt „ganztägig Absenken“.
- Dashboard EMS-ESP → Einstellungen: „Ausschalttemp.“ (Komfort/Eco/Eco+) und „Einschalttemperaturdifferenz“ beim Warmwasser werden ausgeblendet, solange sie 0 sind. Die Wärmepumpe liefert diese Werte nicht.
