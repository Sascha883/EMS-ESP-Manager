# EMS-ESP Manager v1.13.42

- Heizstab-Log weniger überladen:
  - Das Legacy-Topic `info` (alle 5 s) wird nur noch 1× pro Minute aufgezeichnet.
  - Das eigene Echo von `settings/get` bzw. `settings/set` steht nicht mehr doppelt im Log.

Datei: heater.py, manifest.json.
