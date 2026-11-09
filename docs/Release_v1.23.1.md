# EMS-ESP Manager v1.23.1

Panel **2027.01.16** (unverändert) · Home Assistant ab 2025.2

## Behoben
- **Samsung (und Heizstab) fehlten unter „System & Panel → Installierte Geräte“**, wenn mehrere Geräte des Managers eingerichtet sind (z. B. Samsung EHS + Heizstab + Brötje/ESP-Bus-Adapter). Ursache: Beim HA-Start werden die Einträge parallel eingerichtet; jeder legte dabei kurzzeitig eine eigene Panel-Verwaltung an und die zuletzt fertige überschrieb die anderen – übrig blieb nur „Brötje EHS“. Die Panel-Verwaltung wird jetzt mit einer Sperre genau einmal angelegt und von allen Geräten gemeinsam genutzt. Damit erscheinen auch der Reiter **„Samsung“**, die Kacheln **EHS Sentinel / Wärmepumpe** und die Seitenleiste **/samsung-ehs** wieder.

> **Update:** kompletten Ordner `custom_components/ems_esp_manager` ersetzen und Home Assistant **neu starten** (Neuladen der Integration reicht nicht).
