"""Hersteller-Register des EMS-ESP Managers (ab v1.24.0).

Jeder Hersteller (Bosch, Samsung, Brötje/ESP-Bus-Adapter, Heizstab, später Viessmann
und Vaillant) ist hier genau einmal beschrieben. Gemeinsame Teile (Panel, Seite EMS-EHS,
Speicher) fragen das Register statt eigene ``if vendor == …``-Weichen zu pflegen.
"""

from .base import REGISTRY, VendorSpec, active_keys, get, panel_paths, vendor_names

__all__ = ["REGISTRY", "VendorSpec", "active_keys", "get", "panel_paths", "vendor_names"]
