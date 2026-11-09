# EMS-ESP Manager v1.15.7

Passend zur **ESP-Bus-Adapter-Firmware 2.0** (eigene Bus-Komponente „espbus“, ohne Komponente aus GitHub)

- Liest das Log-Format der neuen Firmware (Tag „espbus“, z. B. `RX RET 00>42 id=053D0521 d=00.01.A0`): Regler-Erkennung, Parameter-Scan, Bus-Abfrage, Bus-Log und Bus-Teilnehmer funktionieren damit wie bisher.
- Das bisherige Log-Format (Firmware 1.2) wird weiter erkannt.

Geändert: bsb_proto.py, bsb.py, manifest.json.
