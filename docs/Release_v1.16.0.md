# EMS-ESP Manager v1.16.0

Rechtliche Bereinigung des Panels (Marken- und Gestaltungsbezüge)

- **Panel-Datei umbenannt:** `bosch-ui800.html` → **`ems-panel.html`**. Ein früher hochgeladenes Panel (und dessen Sicherung) wird automatisch übernommen. Die alte Datei `bosch-ui800.html` im Integrationsordner wird nicht mehr benutzt und kann gelöscht werden.
- **Keine Produktnamen fremder Hersteller mehr im Namen oder Titel** des Panels: Titel „EMS-ESP Manager Panel“, Kopfkommentar neu (eigene Gestaltung, Hinweis „keine Verbindung zu den Herstellern“).
- **Kopflinie in eigenen Farben** (rot → gelb wie das SM-Haustechnik-Logo) statt der Regenbogen-Linie.
- Verweise auf Online-Simulator, „Nachbau“, „Original-Display“, Schriftart „Bosch Sans“ und „wie am Bosch-Bedienfeld“ entfernt; interne Bezeichnungen neutral (z. B. „Anlagenwerte-Profil“ statt „UI800-Profil“).
- Frühere Seitenleisten-Adressen mit Produktnamen (/bosch-hmi800, /bh-800) entfernt.
- Panel-Einstellungen im Browser werden von der alten Kennung übernommen (nichts geht verloren).
- README: Haftungs-/Markenhinweis ergänzt, Beschreibung neutral, neue Bildschirmfotos und Vorschau; alte Installationsanleitung (v1.10.2, mit altem Panel-Namen) aus dem Repository entfernt.
- hacs.json: Name „EMS-ESP Manager“.
- Panel 2027.01.02.

Hinweis: Hersteller- und Gerätenamen werden weiter dort genannt, wo sie beschreiben, womit die Integration zusammenarbeitet (z. B. „getestet an CS5800i mit UI 800“, „am UI 800 ändern“).
