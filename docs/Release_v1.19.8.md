# v1.19.8 – WP-Auswertung mit PDF-Bericht (08.11.2026)

Panel unverändert (2027.01.12) · neu im Manager für Bosch, Samsung und Brötje

**Neuer Punkt unter „Logs“**
- Samsung (EMS-EHS): Logs → **EHS-Log · WP-Auswertung**
- Brötje (EMS-BSB): ESP-Bus-Adapter → Logs → **BSB-Log · Kessel-Auswertung**
- Bosch (Dashboard „EMS-ESP“): neue Ansicht **WP-Auswertung** neben „Bus-Log“ (nur wenn eine Wärmepumpe am EMS-Bus erkannt wird)

**Auswertung** aus dem Home-Assistant-Verlauf, Zeitraum 24 h · 7 Tage · 30 Tage:
- Taktung: Starts gesamt und je Tag, max. Starts je Stunde, Laufzeit je Start (Median/Ø), Läufe unter 10 min, Stillstand, Abschaltgrund (Vorlauf über Soll, Warmwasser fertig, Störung, Regelung), Taktung je Außentemperatur
- Wärmepumpe: Abtauungen (Anzahl, Dauer, bei über 7 °C), Kältekreis im eingeschwungenen Betrieb (Heißgas, Sauggas, Überhitzung, Hoch-/Niederdruck mit Sättigungstemperaturen, Unterkühlung (Bosch), Kondensator- und Verdampfer-Abstand, Expansionsventil, Lüfter), Spreizung, Durchfluss, Arbeitszahl, Zuheizer, Störungscodes
- Brötje (Kessel): Brennerstarts und -laufzeit, Modulation, Vorlauf gegen Soll, Trinkwasserladungen (Q3), Trinkwasser, Raum, Solar, Störungen im Kesselstatus
- Bewertung OK / Prüfen / Kritisch mit Hinweis zur Fehlersuche bzw. Optimierung

**Downloads**
- **PDF** (A4): Kennzahlen, Bewertung, Verläufe, Starts/Laufzeit je Tag, Verteilung der Laufzeiten, Tabellen (Taktung, Abschaltgrund, Messwerte, Störungen, letzte 300 Läufe). Wird im Browser erzeugt, keine zusätzlichen Bibliotheken.
- **CSV** (Semikolon, Dezimalkomma): alle Werte im Zeitraster plus Liste aller Läufe.

Neu: `wpa.py`. Geändert: `ehs.py`, `buslog.py`, `dashboard_builder.py`, `manifest.json`, README.
Mit Demodaten und nachgestelltem HA-Verlauf (Entitäten mit Präfix, W/l·h-Umrechnung) im Browser geprüft – in HA noch nicht getestet.
