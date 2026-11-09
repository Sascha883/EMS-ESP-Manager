# EMS-ESP Manager v1.16.1

Panel für Brötje / ESP-Bus-Adapter (BSB) überarbeitet – Panel 2027.01.03

**Startseite und Menüs**
- Kacheln **„ESP-Bus-Adapter“** und **„Parameter“** von der Startseite entfernt; Solar (bzw. Werte) und EMS-Regelung stehen jetzt gemeinsam in einer Spalte.
- Menüpunkt **„Alle Parameter (ESP-Bus-Adapter)“** in den Heizkreis-Menüs entfernt.
- **„Panel-Einstellungen“** aus dem Hauptmenü ins Servicemenü verschoben (≡ 2 s gedrückt halten).
- Menü **Wärmeerzeuger**: Trinkwassertemperatur, Solar und ESP-Bus-Adapter entfernt.
- Der ESP-Bus-Adapter bleibt erreichbar über **EMS-Regelung → ESP-Bus-Adapter** und **Service → Diagnose**.

**Servicemenü an den Brötje-Regler angepasst** (statt der Bosch-Wärmepumpen-Punkte)
- **Anlageneinstellungen:** je Heizkreis Betriebsart, Komfort-/Reduziertsollwert, Heizkennlinie Steilheit; Trinkwasserbetrieb und Nennsollwert.
- **Diagnose:** Status (Kessel, Heizkreise, Trinkwasser, Solar), Monitorwerte (Temperaturen, Modulation, Pumpen Q2/Q3/Q6, Kollektor), ESP-Bus-Adapter.
- **Info:** Regler-Erkennung, Geräte-Identifikation/-familie/-variante, Adapter (Verbindung, WLAN, IP, Laufzeit), Panel-Version; Energie-Details mit Solarertrag.

**Einstellen schneller und Anzeige stimmig**
- Geänderte Werte (Sollwerte, Heizkennlinie) springen nicht mehr auf den alten Wert zurück, bis der Regler bestätigt hat.
- Heizkennlinie mit 2 Nachkommastellen (Schrittweite 0,02) – in Liste und Einstellrad; vorher wurde auf 1 Stelle gerundet.
- Einstellrad bei vielen Stufen deutlich schneller (Wischen), Startseiten-Rad sendet nach 0,7 s statt 1,2 s.
- Nicht gelieferte Werte erscheinen als „–“ statt „unknown“.
- Passend dazu **Firmware ESP-Bus-Adapter 2.0.2** (06_BSB-Adapter): neuer Wert wird sofort gemeldet und direkt nach der Bestätigung des Reglers zurückgelesen.
