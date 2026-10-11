# EMS-ESP Manager für Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Validate](https://github.com/Sascha883/EMS-ESP-Manager/actions/workflows/validate.yml/badge.svg)](https://github.com/Sascha883/EMS-ESP-Manager/actions/workflows/validate.yml)

Custom-Integration, um ein [EMS-ESP](https://github.com/emsesp/EMS-ESP32)-Gateway (z. B. BBQKees) komplett aus Home Assistant zu bedienen – ohne die EMS-ESP-Weboberfläche. Entwickelt und getestet an einer **Bosch Compress CS5800i mit UI 800**.

> Kein offizielles Projekt von EMS-ESP, BBQKees oder Bosch. Nutzung auf eigene Gefahr – Änderungen an Wärmepumpen-Parametern nur mit Fachkenntnis.

## Funktionen

- **System & Firmware** – Status, Laufzeit, Bus-Status, WLAN; Firmware-Updates (stabil/dev) und Wechsel auf ältere Versionen direkt aus HA
- **Dashboard „EMS-ESP“** – wird automatisch erzeugt: Übersicht, Einstellungen, Messwerte, Zuordnung, Bus-Log, MQTT, Web-Oberfläche
- **Bus-Log** – Live-Mitschnitt der EMS-Telegramme, Download als Textdatei, Log-Level und Bus-Mitschnitt einstellbar; Verbindung wird nach EMS-ESP-Neustart automatisch wiederhergestellt
- **Tx-Modus** – Sendemodus (EMS / EMS+ / HT3 / Hardware / Auto) in HA umstellbar
- **MQTT** – alle MQTT-Einstellungen des EMS-ESP (Broker, Topics, Discovery, Sendeintervalle) mit Status-Anzeige
- **Bosch HMI 800 Panel** – Bedienoberfläche im Stil des UI 800 (Sidebar oder Kiosk-Adresse), inkl. Servicemenü
- **Zeitprogramme** – Heizkreis 1 (Telegramm 0x02C3) und Warmwasser (0x02FF) lesen und schreiben, obwohl EMS-ESP diese Typen nicht kennt

Ausführliche Anleitung: [Installationsanleitung (PDF)](EMS-ESP-Manager_Installationsanleitung_v1.10.2.pdf)

## Voraussetzungen

- Home Assistant **2025.2** oder neuer
- EMS-ESP **3.7** oder neuer (getestet mit 3.8.6), Zugang mit Admin-Benutzer
- Für die Entitäten: MQTT-Integration in Home Assistant mit EMS-ESP-Discovery

## Installation

### Über HACS (empfohlen)

1. HACS → ⋮ → **Benutzerdefinierte Repositories** → `https://github.com/Sascha883/EMS-ESP-Manager`, Typ **Integration**
2. „EMS-ESP Manager“ installieren und Home Assistant neu starten
3. Einstellungen → Geräte & Dienste → **Integration hinzufügen** → „EMS-ESP Manager“ (wird per Zeroconf meist automatisch gefunden)

### Manuell

Ordner `custom_components/ems_esp_manager` nach `config/custom_components/` kopieren und Home Assistant neu starten.

## Einrichtung

- **Adresse** des EMS-ESP (IP oder `ems-esp.local`), Benutzer und Passwort eines EMS-ESP-Admins
- Unter **Konfigurieren**: Sidebar-Einträge, Token für den Kiosk-Zugriff auf das Bosch-Panel und die Fachbetriebs-Angaben für die Info-Seite des Panels

## Hinweise aus der Praxis (Bosch CS5800i / UI 800)

- **Tx-Modus nicht auf „Auto“ lassen.** Ab EMS-ESP 3.8.2 ist „Auto“ Standard; bei der CS5800i wählte die Automatik nach einem Neustart teils falsch (Bus-Status „Sendefehler“, Regler nicht erkannt). Fest **EMS+** (oder EMS) funktioniert.
- **Warmwasser Start-/Stopptemperatur:** Die Wärmepumpe erzwingt Stopp ≥ Start + 6 K (Komfort) bzw. + 7 K (Eco, Eco+) und hebt zu kleine Werte ohne Meldung an. Beim Absenken zuerst die Start-, dann die Stopptemperatur ändern.
- Die „Temp.-diff. für Beladung“ (`comfdiff`/`ecodiff`/`ecoplusdiff`) ist das Ladedelta (Vorlauf über Warmwassertemperatur), nicht der Abstand zwischen Start und Stopp.

## Lizenz

**PolyForm Noncommercial License 1.0.0** – siehe [LICENSE](LICENSE).

- ✅ Erlaubt: private Nutzung, Ausprobieren, Anpassen für den Eigenbedarf, gemeinnützige Organisationen, Bildung, Forschung
- ❌ Nicht erlaubt: kommerzielle Nutzung jeder Art – z. B. Einsatz bei Kunden durch Fachbetriebe oder Dienstleister, Verkauf oder Einbau in kommerzielle Produkte

Kommerzielle Nutzung nur mit gesonderter Lizenz – Anfrage über [haustechnik-matheis.de](https://haustechnik-matheis.de).

Die Nutzung erfolgt auf eigene Gefahr und ohne jegliche Gewährleistung oder Haftung. Versionen, die vor dem Lizenzwechsel unter MIT veröffentlicht wurden, bleiben unter MIT.
