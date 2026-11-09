# EMS-ESP Manager v1.23.0

Panel **2027.01.16** · ESP-Bus-Adapter-Firmware **2.4.0** (Zeitprogramme/Regler-Uhr ab **2.6.0**) · Home Assistant ab 2025.2

Fasst alle Änderungen seit dem Pre-Release **v1.17.1** zusammen (Zwischenstände v1.17.2 – v1.22.10).

> **Update:** immer den kompletten Ordner `custom_components/ems_esp_manager` ersetzen und Home Assistant neu starten. Danach den Browser-Cache leeren bzw. die Seite neu laden (Panel 2027.01.16).

## Neu

### Bosch-Einstellungen neu aufgebaut
- Das bisherige Dashboard **„EMS-ESP“** ist jetzt der Reiter **„Bosch“** auf der Seite **EMS-EHS** (neben „System & Panel“, Samsung, Heizstab).
- Menü wie bei „Brötje EHS“: **Übersicht · Werte · System · Logs**.
  - *Werte*: Umschalter Einstellungen / Messwerte.
  - *System*: Status-Kacheln (WP-Auswertung, Zuordnung, MQTT, Uhrzeit) und aufklappbare Bereiche.
  - *Logs*: Bus-Log.
- Reiter „Web-Oberfläche“ und „ESP-Bus-Adapter“ entfernt (Brötje hat seine eigene Seite).

### WP-Auswertung mit PDF-Bericht (Bosch, Samsung, Brötje)
- Auswertung aus dem HA-Verlauf, **Vorauswahl 24 h**, dazu 7 / 30 Tage: Taktung, Laufzeit je Start, Kurzläufe, Abschaltgründe, Taktung je Außentemperatur, Abtauungen, Kältekreis, Spreizung, Durchfluss, Arbeitszahl, Zuheizer, Störungscodes. Brötje: Kessel-Auswertung (Brenner, Modulation, Trinkwasserladungen, Solar).
- Bewertung **OK / Prüfen / Kritisch** mit Hinweisen zur Fehlersuche und Optimierung.
- Alle **Einstellwerte** der Anlage mit Hinweisen und **Änderungen im Zeitraum**.
- Download als **PDF** (A4, im Browser erzeugt) und **CSV**.
- Bosch: unter *System → WP-Auswertung* (nur mit Wärmepumpe am EMS-Bus).

### EMS-Regelung: Erweiterte Ansicht (Testmodus)
- **Leistungsbegrenzung** und **Heizstab** stehen jetzt unter *EMS-Regelung → Erweiterte Ansicht (Testmodus)*. Vorher erscheint ein Hinweis, dass die Funktionen experimentell sind und aktiv in die Regelung eingreifen (einmal je Sitzung bestätigen). Die Warmwasseroptimierung bleibt direkt unter EMS-Regelung.

### Externe Eingänge & Leistungsbegrenzung (Bosch-WP, Test)
- **Externe Eingänge 1–4** direkt aus dem Panel einstellen und testen (EVU-Sperren, Kompressor/Zuheizer/Heizen/Kühlen/WW sperren, PV, Überhitzungsschutz, invertieren, Begrenzungswert) – „übernommen“ erst nach Bestätigung der WP auf dem Bus.
- **Dimm-Testprogramm**: probiert die Eingangsfunktionen nacheinander aus und wertet die Kompressorleistung aus; Rückstellung auch nach Neustart, nicht bestätigte Bits werden wiederholt geschrieben.
- Dimmung schaltet immer richtig herum (vorher bei bereits invertiertem Eingang vertauscht).

### Brötje EHS (ESP-Bus-Adapter / BSB-LAN)
- **Zeitprogramme** Heizkreis 1–3 und Trinkwasser als Wochenübersicht bearbeiten, **Regler-Uhr** anzeigen, stellen und automatisch abgleichen (Adapter-Firmware 2.6.0).
- **ESPHome-Adapter koppeln** mit API-Schlüssel, bessere Adapter-Suche (Port 6053, keine Fehlerkennungen), Schlüssel bleibt beim Firmware-Update erhalten.

### Energie
- Mehr… → **Energie – Details** mit Woche · Monat · Jahr (Ring, Register, Blättern, Vergleich Vorzeitraum) aus der HA-Langzeitstatistik.
- Zähler in Wh/MWh werden korrekt in kWh umgerechnet.

### Einrichtung
- **Ein Einstellungs-Menü** („Konfigurieren“) für alle Geräte, je Gerät Sichtbarkeit in der Seitenleiste (alle / nur Administratoren / aus); Fachbetrieb und Wandtablet für alle Geräte.
- Seite **EMS-EHS** mit **„System & Panel“** wird bei jedem Hersteller angelegt.

## Verbessert / behoben
- **Keine Datenbank-Flut mehr:** Sensor „Bus-Log“ schreibt nur bei Zustandsänderung (vorher ~1 Eintrag/s), Live-Log als eigene Seite.
- **Alle Log-Aufzeichnungen** (Bus-Log, EHS, Heizstab, BSB) sind nach jedem HA-Neustart aus; laufende Aufzeichnungen werden unter „System & Panel“ angezeigt und lassen sich dort ausschalten.
- **Integration löschen:** aktive Dimmung, Test-Rücksetzungen und Warmwasseroptimierung werden vorher zurückgenommen; Heizstab wird abgeschaltet.
- **Mehrere EMS-ESP-Gateways** sauber getrennt (Zwischenspeicher, angelegte Werte, eine GitHub-Abfrage für alle).
- Bosch- und Brötje-Zuordnung getrennt (keine Brötje-Werte mehr als „fehlt“ in der Bosch-Liste).
- Unterseiten im Bosch-Reiter (Uhrzeit, MQTT, Bus-Log, Auswertung) wieder bedienbar.
- Code-Review aller Module (u. a. Schonfrist nach Eingaben, Heizstab-Ausschalten wird nachgeholt).

## Hinweise
- Leistungsbegrenzung, externe Eingänge und Heizstab sind **experimentell** – Anlage beobachten, Nutzung auf eigene Gefahr.
- Auf GitHub alte Dateien löschen, falls noch vorhanden: `bosch-ui800.html`, Ordner `samsung/`, alte Installationsanleitung (PDF).
