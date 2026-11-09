# v1.20.1 – Auswertung: Einstellwerte, automatische Zuordnung, Brötje-Reiter (08.11.2026)

Panel unverändert (2027.01.13) · baut auf v1.20.0 auf · gilt für Bosch, Samsung und Brötje

**Einstellwerte der Anlage (neu in der Auswertung)**
- Alle Einstellwerte mit den Bezeichnungen und Gruppen aus dem Panel: Samsung FSV 10xx–50xx, Heizkurve und Warmwasser; Bosch Heizkurve, Heizkreise, Wärmepumpe, Zuheizer, Warmwasser, PV usw.; Brötje Heizkreise und Trinkwasser.
- Zusätzlich alle weiteren Einstellwerte, die die Anlage liefert (andere Modelle/Versionen) unter „Weitere Einstellwerte“.
- Hinweise: zu Auffälligkeiten der Auswertung (z. B. Takten → Heizkurve, Min. Vorlauf, Raumthermostat, Schalthysterese, Heizgrenze; Warmwasser → Start-/Stopp-Differenz, Laufzeiten; Zuheizer → Freigabe; Hydraulik → Pumpen-ΔT) und zu einzelnen Werten (z. B. Max. Vorlauf über 55 °C, Warmwasser über 55 °C, steile Heizkennlinie).
- **Änderungen im Zeitraum**: welche Einstellung wann von welchem auf welchen Wert geändert wurde (HA-Verlauf).
- Anzeige mit Filter „Mit Hinweis · Geändert · Alle“, vollständige Liste in PDF und CSV.

**Entitäten automatisch zuordnen** wie im Panel: eigene Zuordnung, automatische Zuordnung des Managers über die EMS-ESP unique_id (Kurz-/Langnamen, 3.6-Format, Präfix, umbenannt), Samsung über „…ehssentinel_<name>“ in jeder Domain, Brötje über das Namensende der ESPHome-Entitäten.

**Fehler behoben:** Im Reiter „Brötje EHS“ der Samsung-Seite stand unter Logs die WP-Auswertung mit den Samsung-Werten. Jetzt erscheint dort die Kessel-Auswertung mit den Brötje-Werten.

Geändert: `wpa.py`, `ehs.py`, `manifest.json`, README.
Im Browser mit Demodaten und nachgestelltem HA-Verlauf (umbenannte Präfixe, EMS-ESP-Altnamen, Einstellwert-Änderung) geprüft – in HA noch nicht getestet.
