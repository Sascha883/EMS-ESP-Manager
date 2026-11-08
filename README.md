<p align="center">
  <img src="docs/images/banner.png" alt="EMS-ESP Manager – Haustechnik aus der Praxis" width="100%">
</p>

# EMS-ESP Manager für Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Sascha883/EMS-ESP-Manager)](https://github.com/Sascha883/EMS-ESP-Manager/releases)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.2%2B-41BDF5.svg)](https://www.home-assistant.io)
[![Version](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSascha883%2FEMS-ESP-Manager%2Fmain%2Fcustom_components%2Fems_esp_manager%2Fmanifest.json&query=%24.version&label=Version&color=d32f2f)](custom_components/ems_esp_manager/manifest.json)
[![Lizenz](https://img.shields.io/badge/Lizenz-PolyForm%20Noncommercial-lightgrey.svg)](LICENSE)

Custom-Integration für Home Assistant, mit der du deine Wärmepumpe so bedienst wie am Original-Display – direkt in Home Assistant, auf dem Wand-Tablet oder unterwegs.

| | **Bosch / Buderus** | **Samsung EHS** *(ab v1.13)* |
|---|---|---|
| **Datenquelle** | [EMS-ESP](https://github.com/emsesp/EMS-ESP32)-Gateway (z. B. BBQKees) | HACS-Integration **EHS Sentinel** (RS485) |
| **Panel** | Bosch HMI 800 | Samsung EHS – gleiche Menüs, Samsung-Farben |
| **Gateway-Verwaltung** | Firmware, Bus-Log, MQTT, Uhrzeit, Sensoren | Verbindungsstatus und Protokoll von EHS Sentinel |
| **EMS-Regelung** | Warmwasseroptimierung, Heizstab | Heizstab |

Der Hersteller wird bei der Einrichtung gewählt. Entwickelt und getestet an einer **Bosch Compress CS5800i mit UI 800** und einer **Samsung EHS Split-Anlage**.

> [!NOTE]
> Kein offizielles Projekt von EMS-ESP, BBQKees, Bosch, Samsung, EHS Sentinel oder novolto. Nutzung auf eigene Gefahr – Änderungen an Wärmepumpen-Parametern nur mit Fachkenntnis.

<p align="center">
  <img src="docs/images/vorschau.gif" alt="Vorschau Bosch HMI 800 Panel" width="640">
</p>

---

## Inhalt

- [Funktionen](#funktionen)
  - [Bosch HMI 800 Panel](#bosch-hmi-800-panel)
  - [Samsung EHS Panel](#samsung-ehs-panel)
  - [EMS-Regelung](#ems-regelung)
  - [Manager-Dashboard](#manager-dashboard-ems-esp)
- [Screenshots](#screenshots)
- [Voraussetzungen](#voraussetzungen)
- [Installation](#installation)
- [Einrichtung](#einrichtung)
- [Entitäts-Namen und EMS-ESP-Versionen](#entitäts-namen-und-ems-esp-versionen)
- [Hinweise aus der Praxis](#hinweise-aus-der-praxis-bosch-cs5800i--ui-800)
- [Fehler melden](#fehler-melden)
- [Lizenz](#lizenz)

---

## Funktionen

### Bosch HMI 800 Panel
- Bedienoberfläche im Stil des Bosch UI 800 – als Eintrag in der Seitenleiste oder als **Kiosk-Adresse** für ein Wand-Tablet.
- **Heizen** mit Aus / Auto / Manuell, Wunschtemperatur per Rolle, Heizkurve mit Soll- und Ist-Linie, Vorlauf-Soll und aktuelles Niveau auf der Heizkreis-Kachel.
- **Warmwasser** mit Betriebsart, Extra-Warmwasser und **Verlauf der Warmwassertemperatur** (Soll/Ist, heute).
- **Programmzeiten** für Heizkreis 1 und Warmwasser lesen und schreiben (Telegramme `0x02C3` und `0x02FF` – kennt EMS-ESP selbst nicht).
- **Energie Monitoring** mit Tageswerten („Heute“) und Gesamtwerten, Anteil Umweltwärme und Arbeitszahl.
- Systemübersicht, Sommer-/Winterumschaltung, Kühlfunktionen ein-/ausblendbar.
- **Servicemenü** mit Diagnose und **Übersicht Kältekreislauf** (Temperaturen, Drücke, Kompressor, Ventilator, Expansionsventile – umschaltbar auf Bosch-Bezeichnungen).
- **Geräte am EMS-Bus** mit Softwarestand, Bus-ID und Produkt-ID (Service → Info).
- **Störung und Wartungsmeldung zurücksetzen** direkt im Panel.
- **Automatische Entitäts-Zuordnung** – funktioniert mit allen EMS-ESP-Entity-ID-Formaten (Kurz- und Langnamen, mit Präfix, umbenannt), inklusive Prüfung Lesen/Schreiben. Werte, die in HA fehlen, lassen sich mit einem Klick anlegen.

### Samsung EHS Panel
- **Gleiche Menüführung wie das Bosch-Panel**, Farben angelehnt an das Samsung-Bedienteil (hell, Samsung-Blau als Akzent) – Eintrag **„Samsung EHS“** in der Seitenleiste.
- Daten aus der HACS-Integration **EHS Sentinel**; Samsung-Bezeichnungen und FSV-Einstellungen, wo der Bosch-Text nicht passt.
- Heizkurve als Wassergesetz mit Verschiebung, Zone 1, Warmwasser, Energie, Systemübersicht.
- **Service → Verbindung & Protokoll:** Verbindungsstatus und Log-Mitschnitt von EHS Sentinel.
- Kiosk-Adresse, Fachbetrieb-Info und Heizstab-Regelung wie beim Bosch-Panel.
- Ersetzt die frühere separate Integration „Samsung EHS Panel“ – eigene Zuordnungen werden beim ersten Öffnen übernommen.

### EMS-Regelung
Regelfunktionen, die in Home Assistant laufen – auch wenn das Panel geschlossen ist.
- **Heizstab (novolto P2300(G) / P3000(G))** über die lokale MQTT-API des Geräts – für Bosch und Samsung:
  - Betriebsarten **Aus**, **Manuell** (feste Leistung) und **PV-Überschuss** (Leistung = Überschuss + aktuelle Heizstab-Leistung − Reserve).
  - Einstellbar: Reserve, Mindest-/Höchstleistung, Schrittweite, Regelintervall, Ausschaltverzögerung, Zieltemperatur und Hysterese.
  - Optional **Freigabe-Entität** (z. B. Schalter oder Binärsensor) und **Akku-Ladestand** als Bedingung (Standard: ab 80 %).
  - Fällt der Überschusswert aus (älter als 5 min) oder wird Home Assistant beendet, schaltet die Regelung den Heizstab ab.
- **Warmwasseroptimierung** *(Bosch)*: Ist viel Sonne zu erwarten, wird der Speicher einmal am Tag höher aufgeheizt (Standard 60 °C), damit die Wärmepumpe mit eigenem PV-Strom läuft.
  - Vorhersage über eine **PV-Prognose**-Entität (kWh, z. B. Forecast.Solar, Solcast) oder eine **Wetter**-Entität (Bewölkung).
  - Optional zusätzlich **PV-Überschuss** (W/kW).
  - Zeitfenster und Zieltemperatur einstellbar; danach werden Betriebsart und Stopptemperatur automatisch zurückgesetzt.

### Manager-Dashboard „EMS-ESP“
*Nur Bosch / Buderus (EMS-ESP-Gateway).*

- **System & Firmware** – Status, Bus, Netzwerkanschluss (LAN/WLAN), WLAN-Signal, Laufzeit; Firmware-Updates und Wechsel auf ältere stabile Versionen direkt aus HA.
- **Gateway-Sensoren** – Analogsensoren ein/aus; Kernspannung, Versorgungsspannung und Gateway-Temperatur beim BBQKees Gateway E32 V2.2 per Taste anlegen.
- **Sprache der EMS-ESP-Werte** umschalten (z. B. Deutsch) – Entitäts-IDs bleiben gleich.
- **Uhrzeit** – EMS-ESP-Zeit im Vergleich zu HA, Zeitserver (NTP), Zeitzone, „Uhrzeit von Home Assistant übernehmen“.
- **Einstellungen und Messwerte** aller EMS-Geräte, automatisch erzeugt.
- **Zuordnung** der Panel-Felder, gruppiert nach Heizung, Warmwasser, Wärmepumpe, Energie.
- **Bus-Log** – Live-Mitschnitt der EMS-Telegramme, Download als Textdatei (mit Manager-Version und Geräteliste), Log-Level und Tx-Modus einstellbar.
- **MQTT** – alle MQTT-Einstellungen des EMS-ESP (Broker, Discovery, Entity-ID-Format, Sendeintervalle) mit Statusanzeige.
- **Web-Oberfläche** des EMS-ESP eingebettet.

📄 Ausführliche Anleitung: [Installationsanleitung (PDF, Stand v1.10.2 – Bosch)](docs/EMS-ESP-Manager_Installationsanleitung_v1.10.2.pdf)

---

## Screenshots

### Bosch HMI 800 Panel

| Startseite | Heizen |
|:---:|:---:|
| <img src="docs/images/panel-start.png" width="400"> | <img src="docs/images/panel-heizen.png" width="400"> |
| **Warmwasser** | **Systemübersicht** |
| <img src="docs/images/panel-warmwasser.png" width="400"> | <img src="docs/images/panel-system.png" width="400"> |
| **Energie Monitoring** | **Programmzeiten Warmwasser** |
| <img src="docs/images/panel-energie.png" width="400"> | <img src="docs/images/panel-schaltzeiten.png" width="400"> |
| **Heizkurve** | **Übersicht Kältekreislauf** |
| <img src="docs/images/panel-heizkurve.png" width="400"> | <img src="docs/images/panel-kaeltekreis.png" width="400"> |

### Manager-Dashboard in Home Assistant

| System & Firmware | Bus-Log |
|:---:|:---:|
| <img src="docs/images/ha-system.png" width="400"> | <img src="docs/images/ha-buslog.png" width="400"> |
| **MQTT-Einstellungen** | **Entitäten-Zuordnung** |
| <img src="docs/images/ha-mqtt.png" width="400"> | <img src="docs/images/panel-zuordnung.png" width="400"> |

<sub>Screenshots mit Demo-Daten; einzelne Bilder zeigen ältere Versionen.</sub>

---

## Voraussetzungen

| | |
|---|---|
| **Home Assistant** | 2025.2 oder neuer (eigenes Integrations-Logo ab 2026.3) |
| **Verlauf** | Recorder aktiv (Standard) – für Tageswerte und Warmwasser-Verlauf |
| **Bosch / Buderus** | EMS-ESP 3.7 oder neuer (getestet mit 3.8.x), Admin-Zugang; MQTT-Integration in HA, EMS-ESP mit aktivierter HA-Discovery |
| **Samsung EHS** | HACS-Integration **EHS Sentinel**, eingerichtet und verbunden (RS485-Adapter an der Wärmepumpe) |
| **Heizstab** *(optional)* | novolto P2300(G) / P3000(G), am MQTT-Broker von Home Assistant angemeldet; Entität für PV-Überschuss (W oder kW) |

---

## Installation

### Über HACS (empfohlen)

1. HACS → ⋮ → **Benutzerdefinierte Repositories** → `https://github.com/Sascha883/EMS-ESP-Manager`, Typ **Integration**.
2. „EMS-ESP Manager“ installieren.
3. Home Assistant neu starten.

### Manuell

1. Die ZIP-Datei aus den [Releases](https://github.com/Sascha883/EMS-ESP-Manager/releases) herunterladen und entpacken.
2. Den Ordner `ems_esp_manager` nach `config/custom_components/` kopieren.
3. Home Assistant neu starten.

### Update

Über HACS aktualisieren (oder den Ordner ersetzen), Home Assistant neu starten und das Panel einmal neu laden (Strg+F5). Zuordnung, Heizkreis-Namen, Kiosk-Schlüssel, Programmzeiten und die Einstellungen der EMS-Regelung bleiben erhalten.

> [!TIP]
> Wer bisher die separate Integration **Samsung EHS Panel** nutzt: Nach dem Update den EMS-ESP Manager mit *Samsung EHS* hinzufügen, dann die alte Integration unter Geräte & Dienste löschen und in HACS entfernen.

---

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → „EMS-ESP Manager“**.
2. **Welche Heizung?** – *Bosch / Buderus* oder *Samsung EHS* wählen.
   - **Bosch / Buderus:** Adresse des EMS-ESP (IP oder `ems-esp.local`), Benutzer und Passwort eines EMS-ESP-Admins. Das Gateway wird per Zeroconf meist automatisch gefunden.
   - **Samsung EHS:** keine Zugangsdaten nötig – die Werte kommen aus EHS Sentinel. Fehlt EHS Sentinel, erscheint ein Hinweis.
3. Unter **Konfigurieren** einstellen:
   - Seitenleisten-Einträge (für alle oder nur Admins).
   - **Zugriff von außen / Kiosk** (Wand-Tablet ohne Anmeldung): in HA unter *Profil → Sicherheit → Langlebige Zugriffstoken* einen Token erstellen, einfügen, speichern – danach **Konfigurieren** erneut öffnen und die **Kiosk-Adresse** kopieren.
   - **Fachbetrieb** (Name, Adresse, Telefon) für die Info-Seite des Panels.
4. **Heizstab** und **Warmwasseroptimierung** im Panel unter **EMS-Regelung** einrichten.

In der Seitenleiste erscheinen danach **„EMS-ESP“** (Dashboard) und **„Bosch HMI 800“** bzw. **„Samsung EHS“** (Panel).

---

## Entitäts-Namen und EMS-ESP-Versionen

*Bosch / Buderus.* Das Panel findet die passenden Entitäten automatisch – egal, welches **Entity-ID-Format** im EMS-ESP eingestellt ist:

| Format im EMS-ESP | Beispiel |
|---|---|
| Einzelinstanz, MQTT-Namen (Standard) | `sensor.thermostat_hc1_summermode` |
| Mehrfachinstanzen | `sensor.ems_esp_thermostat_hc1_summermode` |
| Langnamen (v3.4) | `sensor.thermostat_hc1_summer_mode` |
| v3.5 / 3.6-kompatibel (Warmwasser) | `sensor.boiler_wwcurtemp` |
| In HA umbenannt / nach Gerät benannt | `sensor.warmepumpe_summer_mode` |

Beim **UI 800 / HMI 800** liefert EMS-ESP 3.8.x einige Werte (Sommer-/Winterumschaltung, Heizbetrieb bis, Kühlstart, Verzögerungen) nur für die ganze Anlage statt pro Heizkreis – das Panel nutzt dann automatisch diese Werte. Auch die geänderte Betriebsart aus EMS-ESP 3.9-dev (Sommer/Auto/Winter) wird unterstützt.

Eigene Zuordnungen im Reiter **„Zuordnung“** haben immer Vorrang.

---

## Hinweise aus der Praxis (Bosch CS5800i / UI 800)

- **Tx-Modus nicht auf „Auto“ lassen.** Ab EMS-ESP 3.8.2 ist „Auto“ Standard; bei der CS5800i wählte die Automatik nach einem Neustart teils falsch (Bus-Status „Sendefehler“, Regler nicht erkannt). Fest **EMS+** (oder EMS) funktioniert.
- **Warmwasser Start-/Stopptemperatur:** Die Wärmepumpe erzwingt Stopp ≥ Start + 6 K (Komfort) bzw. + 7 K (Eco, Eco+) und hebt zu kleine Werte ohne Meldung an. Beim Absenken zuerst die Start-, dann die Stopptemperatur ändern.
- Die „Temp.-diff. für Beladung“ (`comfdiff` / `ecodiff` / `ecoplusdiff`) ist das Ladedelta (Vorlauf über Warmwassertemperatur), nicht der Abstand zwischen Start und Stopp.
- **Programmzeiten:** Der Regler beantwortet Leseanfragen nur in seinen 24-Byte-Blöcken; geschrieben wird immer das komplette Wochenprogramm – genau wie es das Display macht.
- **Aktuelles Niveau (Heizen/Absenken):** EMS-ESP meldet es beim UI 800 teils falsch. Das Panel ermittelt es deshalb aus den gelesenen Programmzeiten.
- **Uhrzeit:** Hat das Gateway keinen Internetzugang, läuft die EMS-ESP-Uhr weg. Im Reiter „Uhrzeit“ lässt sie sich von Home Assistant übernehmen.

---

## Fehler melden

Bitte ein [Issue](https://github.com/Sascha883/EMS-ESP-Manager/issues) anlegen mit:

- Version des EMS-ESP Managers und von EMS-ESP bzw. EHS Sentinel.
- Hersteller, Regler / Wärmepumpe (z. B. UI 800, CS5800i, Samsung EHS Split).
- Screenshot der betroffenen Seite.
- Bei fehlenden Werten: Ausgabe dieser Vorlage (Entwicklerwerkzeuge → Template):
  ```
  {{ integration_entities('mqtt') | select('search', 'thermostat|boiler') | list }}
  ```
- Bei Bus-Themen: Download aus dem Reiter **Bus-Log** (enthält Manager-Version und Geräteliste).
- Samsung: Mitschnitt aus **Service → Verbindung & Protokoll**.

Das Formular unter *New issue* fragt diese Punkte ab.

---

## Lizenz

**PolyForm Noncommercial License 1.0.0** – siehe [LICENSE](LICENSE).

- ✅ Erlaubt: private Nutzung, Ausprobieren, Anpassen für den Eigenbedarf, gemeinnützige Organisationen, Bildung, Forschung.
- ❌ Nicht erlaubt: kommerzielle Nutzung jeder Art – z. B. Einsatz bei Kunden durch Fachbetriebe oder Dienstleister, Verkauf oder Einbau in kommerzielle Produkte.

Kommerzielle Nutzung nur mit gesonderter Lizenz – Anfrage über [haustechnik-matheis.de](https://haustechnik-matheis.de).

Kunden von Sascha Matheis Haustechnik erhalten im Rahmen ihres Auftrags eine Nutzungslizenz für die bei ihnen installierte Anlage.

Weitergabe, auch in veränderter Form, nur mit dem vollständigen Lizenztext und dem Copyright-Hinweis. Das Entfernen des Urheberhinweises oder das Ausgeben des Codes als eigenes Werk ist nicht gestattet.

© 2026 Sascha Matheis, Sascha Matheis Haustechnik, Espenau – [haustechnik-matheis.de](https://haustechnik-matheis.de)

Die Nutzung erfolgt auf eigene Gefahr und ohne jegliche Gewährleistung oder Haftung.

---

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.png">
    <img src="docs/images/logo-light.png" alt="SM Haustechnik" width="90">
  </picture><br>
  <b>Sascha Matheis Haustechnik</b> · Espenau · Kassel · Nordhessen<br>
  <a href="https://haustechnik-matheis.de">haustechnik-matheis.de</a> · Instagram <a href="https://www.instagram.com/haustechnik_matheis">@haustechnik_matheis</a>
</p>
