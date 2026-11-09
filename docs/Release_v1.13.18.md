# EMS-ESP Manager v1.13.18

## Neu: Eingangsfunktion anlernen (Service → Externer Eingang → Funktion anlernen)
Die CS5800i nimmt die Funktion eines Eingangs (EVU, Sperren, PV …) von EMS-ESP über 0x486 nicht an. Der Anlern-Modus zeigt, wie das UI 800 sie schreibt:
1. Ausgangszustand: hpin1–4opt sowie gezielte Lesezugriffe auf 0x486 (in kleinen Stücken, alle 54 Byte) und die Kandidaten 0x4A2, 0x4A8, 0x4A9, 0x4AB, 0x4CA.
2. Ruhephase 30 s: Bytes, die sich ohnehin ständig ändern, werden gemerkt und später ausgeblendet.
3. Anlernfenster (1–10 min): Jetzt am UI 800 die Funktion ändern und danach „Anlernen beenden“ drücken. Neue Schreibzugriffe des UI 800 und geänderte Bytes erscheinen live im Protokoll.
4. Endzustand: dieselben Lesezugriffe noch einmal, mit Vergleich vorher/nachher.
- Bei 0x486 wird jedes Byte zugeordnet, z. B. „Offset 23 = Eingang 3: Heizen sperren“.
- Das Anlernprotokoll lässt sich kopieren oder herunterladen (eingang-anlernen_….txt).
- Der Manager schreibt dabei nichts in die Wärmepumpe. Anlernen und Funktionstest sperren sich gegenseitig.

## Funktionstest
- „Beobachtung je Weg“ jetzt bis 15 min einstellbar (bisher 5 min).

## Auswertung Testprotokoll 25.10. 14:54 (v1.13.15)
- Eingang 3 steht auf „Heizen sperren · invertiert“ (hpin3opt 100000010000000, 0x486 Offset 23). Das Lesen der Funktion passt also zur EMS-ESP-Zuordnung.
- Weg A: Das Invertier-Bit wurde übernommen und zurückgesetzt. Der Kompressor lief (Heizen, ca. 400 W, 18 %), es gab aber innerhalb von 60 s keine Reaktion; der Eingangszustand blieb „aus“.
- Mögliche Gründe: Die Mindestlaufzeit des Kompressors ist länger als 60 s, oder die Wärmepumpe wertet die geänderte Logik erst verzögert aus. Deshalb den Test mit Beobachtung 10–15 min wiederholen.
- Bei 400 W wäre eine Leistungsbegrenzung (z. B. 4,2 kW) ohnehin nicht zu sehen. Sichtbar ist nur eine Sperre.

Panel-Version 2026.12.15. Dateien: input_learn.py (neu), power_limit.py, panel.py, bosch-ui800.html, manifest.json. Home Assistant neu starten.
