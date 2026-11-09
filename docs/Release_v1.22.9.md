# v1.22.9 – EMS-Regelung: Erweiterte Ansicht (Testmodus) (09.11.2026)

Panel **2027.01.16** · baut auf v1.22.8 auf

- **Panel → EMS-Regelung:** ganz oben neuer Punkt **„Erweiterte Ansicht (Testmodus)“**.
- Beim Öffnen erscheint zuerst ein Hinweis: Funktionen sind experimentell, greifen aktiv in die Regelung ein, Nutzung auf eigene Verantwortung. Erst nach „Verstanden – öffnen“ geht es weiter (einmal je Browser-Sitzung).
- **Leistungsbegrenzung, Heizstab und Heizstab-Einstellungen** stehen nur noch dort (nicht mehr direkt unter EMS-Regelung). Warmwasseroptimierung bleibt unter EMS-Regelung.
- Die Funktionen selbst sind unverändert und laufen weiter, wenn sie eingeschaltet sind.

Geändert: `ems-panel.html` (Panel 2027.01.16), `manifest.json`. JS-Syntax geprüft, im Panel-Demo-Modus im Browser getestet – in HA noch nicht getestet.
