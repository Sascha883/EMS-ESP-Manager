"""Beschreibung je Hersteller (v1.24.0).

Neuen Hersteller hinzufügen (z. B. Viessmann über ESP32):
1. Kennung in ``const.py`` (``VENDOR_…``) – existiert für Viessmann/Vaillant bereits.
2. Eintrag unten in ``REGISTRY`` mit ``active=True``.
3. Kennung in ``const.VENDORS`` aufnehmen (Reihenfolge = Reihenfolge in Listen).
4. Einrichten/Entfernen (``async_setup_entry``/``async_unload_entry``) im eigenen Modul.

Speicher: jeder Hersteller schreibt nur in ``PanelManager.vdata(<key>)``
(= ``vendors.<key>`` in ``.storage/ems_esp_manager.panel``), nie auf oberster Ebene.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json

from ..const import (
    BSB_PANEL_ICON,
    BSB_SIDEBAR_URL,
    BSB_SIDEBAR_URLS_OLD,
    BSB_TITLE,
    HEATER_PANEL_ICON,
    HEATER_SIDEBAR_URL,
    HEATER_TITLE,
    PANEL_ICON,
    PANEL_SIDEBAR_URL,
    PANEL_SIDEBAR_URLS_OLD,
    PANEL_TITLE,
    SAMSUNG_PANEL_ICON,
    SAMSUNG_PANEL_TITLE,
    SAMSUNG_SIDEBAR_URL,
    SAMSUNG_SIDEBAR_URLS_OLD,
    URL_PANEL,
    VENDOR_BOSCH,
    VENDOR_BSB,
    VENDOR_HEATER,
    VENDOR_SAMSUNG,
    VENDOR_VAILLANT,
    VENDOR_VIESSMANN,
    VENDORS,
)


@dataclass(frozen=True)
class VendorSpec:
    """Was der Manager über einen Hersteller wissen muss."""

    key: str                      # Kennung (entry.data["vendor"], Speicherblock vendors.<key>)
    name: str                     # Anzeige „Installierte Geräte“
    sidebar_title: str            # Titel des Panels in der Seitenleiste
    icon: str                     # mdi-Symbol der Seitenleiste
    sidebar_url: str              # Adresse ohne „/“ (z. B. „samsung-ehs“)
    panel_query: str = ""         # Zusatz an URL_PANEL („?vendor=…“)
    old_urls: tuple[str, ...] = ()
    connection: str = ""          # Anbindung (Info/Doku)
    heat_pump: bool = True        # hat Wärmepumpen-/Heizungswerte (Zuordnung, WP-Auswertung)
    show_sidebar: bool = True     # eigener Seitenleisten-Eintrag möglich
    active: bool = True           # False = vorgemerkt, noch nicht wählbar
    store_keys: tuple[str, ...] = field(default=())  # Altschlüssel (< v1.24) auf oberster Ebene

    @property
    def panel_url(self) -> str:
        return URL_PANEL + self.panel_query

    @property
    def sidebar(self) -> tuple[str, str, str, str]:
        """(Titel, Symbol, Adresse, Panel-URL) – Format von panel._SIDEBAR."""
        return (self.sidebar_title, self.icon, self.sidebar_url, self.panel_url)


REGISTRY: dict[str, VendorSpec] = {
    s.key: s
    for s in (
        VendorSpec(
            key=VENDOR_BOSCH, name="Bosch / Buderus", sidebar_title=PANEL_TITLE, icon=PANEL_ICON,
            sidebar_url=PANEL_SIDEBAR_URL, old_urls=tuple(PANEL_SIDEBAR_URLS_OLD), connection="EMS-ESP",
            store_keys=("map", "hc_names", "created", "show_cooling", "dhw_opt", "dhw_opt_state",
                        "plim", "plim_state", "dimprog"),
        ),
        VendorSpec(
            key=VENDOR_SAMSUNG, name="Samsung EHS", sidebar_title=SAMSUNG_PANEL_TITLE, icon=SAMSUNG_PANEL_ICON,
            sidebar_url=SAMSUNG_SIDEBAR_URL, panel_query=f"?vendor={VENDOR_SAMSUNG}",
            old_urls=tuple(SAMSUNG_SIDEBAR_URLS_OLD), connection="EHS Sentinel",
        ),
        VendorSpec(
            key=VENDOR_HEATER, name="Heizstab", sidebar_title=HEATER_TITLE, icon=HEATER_PANEL_ICON,
            sidebar_url=HEATER_SIDEBAR_URL, panel_query=f"?vendor={VENDOR_HEATER}&page=heater",
            connection="novolto (MQTT)", heat_pump=False, show_sidebar=False,
            store_keys=("heater", "heater_state"),
        ),
        VendorSpec(
            key=VENDOR_BSB, name="ESP-Bus-Adapter", sidebar_title=BSB_TITLE, icon=BSB_PANEL_ICON,
            sidebar_url=BSB_SIDEBAR_URL, panel_query=f"?vendor={VENDOR_BSB}&page=bsb",
            old_urls=tuple(BSB_SIDEBAR_URLS_OLD), connection="ESP-Bus-Adapter / BSB-LAN", show_sidebar=False,
            store_keys=("bsb", "bsb_state", "bsb_user_params"),
        ),
        # ---- vorgemerkt: Anbindung über eigenes ESP32-Gateway am Heizungsbus ----
        VendorSpec(
            key=VENDOR_VIESSMANN, name="Viessmann", sidebar_title="Viessmann EHS", icon="mdi:heat-pump",
            sidebar_url="viessmann-ehs", panel_query=f"?vendor={VENDOR_VIESSMANN}",
            connection="ESP32", active=False,
        ),
        VendorSpec(
            key=VENDOR_VAILLANT, name="Vaillant", sidebar_title="Vaillant EHS", icon="mdi:heat-pump",
            sidebar_url="vaillant-ehs", panel_query=f"?vendor={VENDOR_VAILLANT}",
            connection="ESP32", active=False,
        ),
    )
}


def get(key: str) -> VendorSpec:
    return REGISTRY[key]


def active_keys() -> list[str]:
    """Wählbare Hersteller in fester Reihenfolge (= const.VENDORS)."""
    return [k for k in VENDORS if k in REGISTRY and REGISTRY[k].active]


def panel_paths() -> str:
    """JSON {Kennung: "/adresse"} für die Seite EMS-EHS („Panel öffnen“, „Installierte Geräte“)."""
    return json.dumps({k: "/" + REGISTRY[k].sidebar_url for k in active_keys()})


def vendor_names() -> str:
    """JSON {Kennung: Anzeigename} für die Seite EMS-EHS."""
    return json.dumps({k: REGISTRY[k].name for k in active_keys()}, ensure_ascii=False)


def _check() -> None:
    """Register und const.VENDORS müssen zusammenpassen (fällt beim Import auf, nicht beim Kunden)."""
    missing = [k for k in VENDORS if k not in REGISTRY]
    inactive = [k for k in VENDORS if k in REGISTRY and not REGISTRY[k].active]
    if missing or inactive:
        raise RuntimeError(f"Hersteller-Register unvollständig: fehlt {missing}, inaktiv {inactive}")
    seen: dict[str, str] = {}
    for spec in REGISTRY.values():
        for url in (spec.sidebar_url, *spec.old_urls):
            if url in seen:
                raise RuntimeError(f"Adresse /{url} doppelt ({seen[url]}, {spec.key})")
            seen[url] = spec.key
    owners: dict[str, str] = {}
    for spec in REGISTRY.values():
        for k in spec.store_keys:
            if k in owners:
                raise RuntimeError(f"Speicherschlüssel {k} doppelt ({owners[k]}, {spec.key})")
            owners[k] = spec.key


_check()
