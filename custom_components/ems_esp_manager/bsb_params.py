"""Eigene Parametertabelle für den ESP-Bus-Adapter (Sascha Matheis Haustechnik).

Bewusst klein gehalten: nur die Bedien- und Diagnosewerte, die praktisch jede Anlage mit
Siemens-Albatros-Regler (RVS, LMS, LMU …) hat – geprüft am Brötje-Regler RVS43.345/100.
Alles Weitere findet der Manager selbst:

* **ID-Suchlauf** – fragt ganze Bereiche von Befehls-IDs ab und merkt sich, worauf der Regler antwortet.
* **Anlernen** – Telegramme mit unbekannter ID (vom Bedienteil, vom Regler gesendet oder aus dem
  Suchlauf) werden gesammelt, der Datentyp wird geschätzt und der Wert kann benannt werden.
  Benannte Werte landen in der Anlagen-Tabelle (Panel-Speicher) und gelten wie diese Tabelle.

Aufbau einer Zeile:  (Zeile, Befehls-ID, Codec, Name, Enum, Flags, Ausweich-IDs)
    Codec   siehe CODECS
    Enum    Schlüssel in ENUMS oder None
    Flags   "r" = nur lesen
    Ausweich-IDs werden versucht, wenn der Regler die erste ID ablehnt (andere Gerätefamilie).

Die Befehls-IDs sind Kennungen des Bus-Protokolls (technische Fakten) und wurden an der eigenen
Anlage nachgeprüft. Namen, Codecs, Status-Texte und Aufbau sind selbst erstellt.
"""

from __future__ import annotations

# Codec: (Länge der Nutzdaten ohne Status-Byte, mit Vorzeichen, Teiler, Nachkommastellen, Einheit, Art)
#   Art: num | enum | str | dt | err
CODECS: dict[str, tuple[int, bool, float, int, str, str]] = {
    "temp":   (2, True, 64.0, 1, "°C", "num"),
    "steil":  (2, True, 50.0, 2, "", "num"),
    "pct":    (1, False, 1.0, 0, "%", "num"),
    "pct100": (2, False, 100.0, 1, "%", "num"),
    "enum":   (1, False, 1.0, 0, "", "enum"),
    "onoff":  (1, False, 1.0, 0, "", "enum"),
    "u8":     (1, False, 1.0, 0, "", "num"),
    "u16":    (2, False, 1.0, 0, "", "num"),
    "u32":    (4, False, 1.0, 0, "", "num"),
    "hours":  (4, False, 3600.0, 0, "h", "num"),
    "kwh10":  (2, False, 10.0, 1, "kWh", "num"),
    "kwh":    (4, False, 1.0, 0, "kWh", "num"),
    "bar10":  (2, False, 10.0, 1, "bar", "num"),
    "rpm":    (2, False, 1.0, 0, "U/min", "num"),
    "ua100":  (2, False, 100.0, 2, "µA", "num"),
    "ver10":  (2, False, 10.0, 1, "", "num"),
    "months": (1, False, 1.0, 0, "Monate", "num"),
    "str":    (0, False, 1.0, 0, "", "str"),
    "dt":     (8, False, 1.0, 0, "", "dt"),
    "err":    (2, False, 1.0, 0, "", "err"),
}

ENUMS: dict[str, dict[int, str]] = {
    "onoff": {0: "Aus", 1: "Ein", 255: "Ein"},
    "hk_mode": {0: "Schutzbetrieb", 1: "Automatik", 2: "Reduziert", 3: "Komfort"},
    "tww_mode": {0: "Aus", 1: "Ein", 2: "Eco"},
    "tww_release": {0: "immer (24 h)", 1: "nach Zeitprogrammen der Heizkreise", 2: "nach eigenem TWW-Zeitprogramm"},
    "legio": {0: "Aus", 1: "in festem Abstand", 2: "an einem Wochentag"},
    # Statuscodes (Zahlen = Codes des Reglers, Texte selbst formuliert)
    "st_hk": {
        0: "—", 3: "Wächter hat ausgelöst", 4: "Handbetrieb", 17: "Pumpennachlauf", 22: "Frostschutz Anlage",
        23: "Frostschutz", 24: "Aus", 56: "Überhitzungsschutz", 101: "Frostschutz Raum", 102: "Estrichprogramm läuft",
        103: "begrenzt – Kesselschutz", 104: "begrenzt – Warmwasser hat Vorrang", 105: "begrenzt – Pufferspeicher",
        106: "Heizen begrenzt", 107: "Wärmeabnahme für Pufferspeicher", 108: "Wärmeabnahme für Warmwasser",
        109: "Wärmeabnahme für Wärmeerzeuger", 110: "Wärmeabnahme", 111: "Vorheizen + Schnellaufheizen",
        112: "Vorheizen (Optimierung)", 113: "Schnellaufheizen", 114: "Heizen Komfort", 115: "Abschalten (Optimierung)",
        116: "Heizen Reduziert", 117: "Frostschutz Vorlauf", 118: "Sommerbetrieb", 119: "Tages-Eco",
        120: "Absenkung auf Reduziert", 121: "Absenkung auf Frostschutz", 122: "Raumtemperatur begrenzt",
        248: "Pumpe läuft dauernd",
    },
    "st_tww": {
        0: "—", 3: "Wächter hat ausgelöst", 4: "Handbetrieb", 17: "Pumpennachlauf", 24: "Frostschutz", 25: "Aus",
        53: "Rückkühlen", 66: "Laden mit Heizstab", 67: "Zwangsladen", 69: "Laden", 70: "voll – max. Speichertemperatur",
        71: "voll – max. Ladetemperatur", 75: "voll", 79: "Entladeschutz", 80: "Ladezeit begrenzt", 81: "Laden gesperrt",
        82: "Ladesperre", 85: "Zwangsladen Legionellen", 86: "Zwangsladen Nennwert", 92: "Push Legionellen",
        93: "Push Nennwert", 94: "Push", 95: "Laden Legionellen", 96: "Laden Nennwert", 97: "Laden Reduziert",
        98: "voll – Legionellentemperatur", 99: "voll – Nenntemperatur", 100: "voll – reduzierte Temperatur",
        199: "Zapfen", 200: "bereit", 201: "Bereitschaftsladen",
    },
    "st_kessel": {
        0: "—", 1: "Sicherheits-Temperaturbegrenzer", 2: "Störung", 3: "Wächter hat ausgelöst", 4: "Handbetrieb",
        5: "Schornsteinfeger Volllast", 6: "Schornsteinfeger Teillast", 7: "Schornsteinfeger", 8: "gesperrt (von Hand)",
        9: "gesperrt (automatisch)", 10: "gesperrt", 15: "freigegeben", 17: "Nachlauf", 18: "in Betrieb", 19: "freigegeben",
        20: "Mindesttemperatur", 23: "Frostschutz Anlage", 24: "Frostschutz", 25: "Aus", 59: "lädt Pufferspeicher",
        166: "in Betrieb – Heizung", 167: "Teillast – Heizung", 168: "in Betrieb – Warmwasser", 169: "Teillast – Warmwasser",
        170: "in Betrieb – Heizung + Warmwasser", 171: "Teillast – Heizung + Warmwasser", 175: "freigegeben – Heizung",
        176: "gesperrt – Außentemperatur", 198: "gesperrt – Öko-Betrieb",
    },
    "st_solar": {
        0: "—", 2: "Störung", 4: "Handbetrieb", 52: "Frostschutz Kollektor", 53: "Rückkühlen", 54: "Speicher voll",
        55: "Verdampfungsschutz", 56: "Überhitzungsschutz", 57: "max. Ladetemperatur", 58: "lädt Warmwasser",
        59: "lädt Pufferspeicher", 60: "lädt Schwimmbad", 61: "Ladetemperatur zu niedrig", 62: "Temperaturdifferenz zu klein",
        63: "zu wenig Sonne",
    },
}

# Gruppen (für die Anzeige im Panel)
GROUPS: list[tuple[str, float, float]] = [
    ("Uhrzeit", 0, 99),
    ("Heizkreis 1", 700, 999),
    ("Heizkreis 2", 1000, 1299),
    ("Heizkreis 3/P", 1300, 1599),
    ("Trinkwasser", 1600, 1699),
    ("Kessel", 2200, 2999),
    ("Solar", 3800, 3899),
    ("Gerät & Fehler", 6000, 7999),
    ("Status", 8000, 8099),
    ("Diagnose Wärmeerzeuger", 8300, 8599),
    ("Diagnose Verbraucher", 8700, 8999),
    ("Angelernt", 100000, 999999),
]

R = "r"
PARAMS: list[tuple] = [
    (0, "053D000B", "dt", "Datum und Uhrzeit", None, R, ()),
    # Heizkreis 1
    (700, "2D3D0574", "enum", "Betriebsart", "hk_mode", "", ("053D0074",)),
    (710, "2D3D058E", "temp", "Raumsollwert Komfort", None, "", ()),
    (712, "2D3D0590", "temp", "Raumsollwert Reduziert", None, "", ()),
    (714, "2D3D0592", "temp", "Raumsollwert Frostschutz", None, "", ()),
    (716, "2D3D05A5", "temp", "Komfortsollwert höchstens", None, "", ()),
    (720, "2D3D05F6", "steil", "Heizkurve Steilheit", None, "", ()),
    (721, "2D3D0610", "temp", "Heizkurve Parallelverschiebung", None, "", ()),
    (730, "2D3D05FD", "temp", "Sommer-/Winter-Umschaltung", None, "", ("393D05FD",)),
    (732, "2D3D0640", "temp", "Tagesheizgrenze", None, "", ()),
    (740, "213D0663", "temp", "Vorlauftemperatur mindestens", None, "", ("193D0663",)),
    (741, "213D0662", "temp", "Vorlauftemperatur höchstens", None, "", ("193D0662",)),
    (750, "2D3D0603", "pct", "Einfluss Raumtemperatur", None, "", ()),
    # Heizkreis 2
    (1000, "2E3D0574", "enum", "Betriebsart", "hk_mode", "", ()),
    (1010, "2E3D058E", "temp", "Raumsollwert Komfort", None, "", ()),
    (1012, "2E3D0590", "temp", "Raumsollwert Reduziert", None, "", ()),
    (1014, "2E3D0592", "temp", "Raumsollwert Frostschutz", None, "", ()),
    (1020, "2E3D05F6", "steil", "Heizkurve Steilheit", None, "", ()),
    (1021, "2E3D0610", "temp", "Heizkurve Parallelverschiebung", None, "", ()),
    (1030, "2E3D05FD", "temp", "Sommer-/Winter-Umschaltung", None, "", ()),
    (1040, "223D0663", "temp", "Vorlauftemperatur mindestens", None, "", ()),
    (1041, "223D0662", "temp", "Vorlauftemperatur höchstens", None, "", ()),
    # Heizkreis 3/P
    (1300, "2F3D0574", "enum", "Betriebsart", "hk_mode", "", ()),
    (1310, "2F3D058E", "temp", "Raumsollwert Komfort", None, "", ()),
    (1312, "2F3D0590", "temp", "Raumsollwert Reduziert", None, "", ()),
    (1320, "2F3D05F6", "steil", "Heizkurve Steilheit", None, "", ()),
    # Trinkwasser
    (1600, "313D0571", "enum", "Betriebsart Warmwasser", "tww_mode", "", ()),
    (1610, "313D06B9", "temp", "Warmwasser Nennsollwert", None, "", ()),
    (1612, "313D06BA", "temp", "Warmwasser Reduziertsollwert", None, "", ()),
    (1620, "253D0722", "enum", "Freigabe Warmwasserladung", "tww_release", "", ()),
    (1640, "313D0759", "enum", "Legionellenschutz", "legio", "", ()),
    (1645, "313D06BC", "temp", "Legionellenschutz Temperatur", None, "", ("113D06BC",)),
    # Kessel
    (2210, "0D3D092C", "temp", "Kesselsollwert mindestens", None, "", ("2D3D092C",)),
    (2212, "0D3D092B", "temp", "Kesselsollwert höchstens", None, "", ("2D3D0952",)),
    # Solar
    (3810, "493D085D", "temp", "Solar Einschaltdifferenz", None, "", ("193D085D",)),
    (3811, "493D085C", "temp", "Solar Ausschaltdifferenz", None, "", ("313D085C",)),
    # Gerät
    (6220, "053D000E", "ver10", "Software-Version", None, R, ()),
    (6224, "053D0001", "str", "Geräte-Identifikation", None, R, ()),
    (6225, "053D0002", "u16", "Gerätefamilie", None, R, ()),
    (6226, "053D0003", "u16", "Gerätevariante", None, R, ()),
    (6705, "053D0099", "err", "Software-Diagnosecode", None, R, ("053D3007",)),
    (7045, "053D05E2", "months", "Zeit seit Wartung", None, R, ("053D0B0F",)),
    # Status
    (8000, "053D07A3", "enum", "Status Heizkreis 1", "st_hk", R, ()),
    (8001, "053D07A5", "enum", "Status Heizkreis 2", "st_hk", R, ()),
    (8002, "053D07A7", "enum", "Status Heizkreis 3/P", "st_hk", R, ()),
    (8003, "053D07A1", "enum", "Status Warmwasser", "st_tww", R, ()),
    (8005, "053D07A9", "enum", "Status Kessel", "st_kessel", R, ()),
    (8007, "053D07AD", "enum", "Status Solar", "st_solar", R, ()),
    # Diagnose Wärmeerzeuger
    (8310, "0D3D0519", "temp", "Kesseltemperatur", None, R, ("053D130E", "113D0226")),
    (8311, "0D3D0923", "temp", "Kesselsollwert", None, R, ("053D130D",)),
    (8314, "113D051A", "temp", "Rücklauftemperatur Kessel", None, R, ()),
    (8316, "053D051D", "temp", "Abgastemperatur", None, R, ()),
    (8323, "093D0E69", "rpm", "Gebläsedrehzahl", None, R, ("113D0C82",)),
    (8325, "093D0E00", "pct100", "Gebläseansteuerung", None, R, ("113D305D",)),
    (8326, "113D305F", "pct", "Brennermodulation", None, R, ("053D0834", "0D3D0C85")),
    (8327, "113D3063", "bar10", "Wasserdruck", None, R, ("053D3063",)),
    (8329, "153D2FF0", "ua100", "Ionisationsstrom", None, R, ()),
    (8330, "0D3D093B", "hours", "Betriebsstunden Brenner", None, R, ()),
    (8331, "053D08A5", "u32", "Brennerstarts", None, R, ()),
    (8338, "193D2FEB", "hours", "Betriebsstunden Heizen", None, R, ("053D2FEB",)),
    (8339, "193D2FEC", "hours", "Betriebsstunden Warmwasser", None, R, ("053D2FEC",)),
    (8499, "053D09AB", "onoff", "Kollektorpumpe", "onoff", R, ()),
    (8505, "493D04CE", "pct", "Drehzahl Kollektorpumpe", None, R, ()),
    (8510, "493D052A", "temp", "Kollektortemperatur", None, R, ("053D052A",)),
    (8526, "493D0599", "kwh10", "Solarertrag heute", None, R, ()),
    (8527, "493D0598", "kwh", "Solarertrag gesamt", None, R, ()),
    (8530, "493D0893", "hours", "Betriebsstunden Solar", None, R, ()),
    # Diagnose Verbraucher
    (8700, "053D0521", "temp", "Außentemperatur", None, R, ()),
    (8701, "053D056F", "temp", "Außentemperatur Minimum", None, R, ()),
    (8702, "053D056E", "temp", "Außentemperatur Maximum", None, R, ()),
    (8703, "053D05F0", "temp", "Außentemperatur gedämpft", None, R, ()),
    (8704, "053D05F2", "temp", "Außentemperatur gemischt", None, R, ()),
    (8730, "053D09A5", "onoff", "Heizkreispumpe 1 (Q2)", "onoff", R, ()),
    (8731, "053D09A6", "onoff", "Mischer HK1 auf", "onoff", R, ()),
    (8732, "053D09A7", "onoff", "Mischer HK1 zu", "onoff", R, ()),
    (8740, "2D3D051E", "temp", "Raumtemperatur 1", None, R, ("053D051E",)),
    (8741, "2D3D0593", "temp", "Raumsollwert 1", None, R, ()),
    (8743, "213D0518", "temp", "Vorlauftemperatur 1", None, R, ()),
    (8744, "213D0667", "temp", "Vorlaufsollwert 1", None, R, ()),
    (8760, "053D09A8", "onoff", "Heizkreispumpe 2 (Q6)", "onoff", R, ()),
    (8770, "2E3D051E", "temp", "Raumtemperatur 2", None, R, ()),
    (8771, "2E3D0593", "temp", "Raumsollwert 2", None, R, ()),
    (8773, "223D0518", "temp", "Vorlauftemperatur 2", None, R, ()),
    (8774, "223D0667", "temp", "Vorlaufsollwert 2", None, R, ()),
    (8820, "053D09A3", "onoff", "Warmwasser-Ladepumpe (Q3)", "onoff", R, ()),
    (8830, "313D052F", "temp", "Warmwasser oben", None, R, ()),
    (8831, "313D074B", "temp", "Warmwasser Sollwert", None, R, ()),
    (8832, "313D0530", "temp", "Warmwasser unten", None, R, ()),
    (8950, "053D051F", "temp", "Sammelvorlauf", None, R, ()),
    (8980, "053D0534", "temp", "Pufferspeicher oben", None, R, ()),
    (8982, "053D0535", "temp", "Pufferspeicher unten", None, R, ()),
]

# An der eigenen Anlage geprüfte Geräte (Familie/Variante aus 6225/6226)
VERIFIED_DEVICES: dict[str, str] = {
    "134/100": "Brötje-Gerät mit Regler RVS43.345/100 (geprüft)",
}

# Bereiche für den ID-Suchlauf: (Name, die oberen 16 Bit der ID, von, bis)
PROBE_PRESETS: list[tuple[str, int, int, int]] = [
    ("Heizkreis 1", 0x2D3D, 0x0500, 0x06FF),
    ("Heizkreis 2", 0x2E3D, 0x0500, 0x06FF),
    ("Trinkwasser", 0x313D, 0x0500, 0x07FF),
    ("Allgemein / Status", 0x053D, 0x0400, 0x0AFF),
    ("Vorlauf / Mischer", 0x213D, 0x0500, 0x06FF),
    ("Kessel", 0x0D3D, 0x0500, 0x0CFF),
    ("Brenner / Feuerungsautomat", 0x113D, 0x0500, 0x0CFF),
    ("Solar", 0x493D, 0x0400, 0x09FF),
]
