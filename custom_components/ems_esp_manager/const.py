"""Constants for the EMS-ESP Manager integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "ems_esp_manager"


def _manifest_version() -> str:
    import json
    from pathlib import Path

    try:
        return json.loads((Path(__file__).parent / "manifest.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return "?"


MANAGER_VERSION: Final = _manifest_version()
MANUFACTURER: Final = "EMS-ESP"

DEFAULT_USERNAME: Final = "admin"
DEFAULT_PASSWORD: Final = "admin"

STATUS_SCAN_INTERVAL: Final = timedelta(seconds=30)
RELEASE_SCAN_INTERVAL: Final = timedelta(hours=6)

GITHUB_REPO: Final = "emsesp/EMS-ESP32"
GITHUB_API: Final = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
GITHUB_DOWNLOAD: Final = f"https://github.com/{GITHUB_REPO}/releases/download"
CHANGELOG_STABLE: Final = f"https://github.com/{GITHUB_REPO}/blob/main/CHANGELOG.md"
CHANGELOG_DEV: Final = f"https://github.com/{GITHUB_REPO}/blob/dev/CHANGELOG_LATEST.md"

# oldest stable version offered for selection (uploadURL + HA discovery format)
MIN_SELECTABLE_VERSION: Final = "3.7.0"

CHANNEL_STABLE: Final = "stable"
CHANNEL_DEV: Final = "dev"
CHANNELS: Final = [CHANNEL_STABLE, CHANNEL_DEV]

# System status codes (src/core/system.h in EMS-ESP)
STATUS_NORMAL: Final = 0
STATUS_PENDING_UPLOAD: Final = 1
STATUS_ERROR_UPLOAD: Final = 3
STATUS_PENDING_RESTART: Final = 4
STATUS_RESTART_REQUESTED: Final = 5
STATUS_INVALID_GPIO: Final = 6
STATUS_UPLOADING: Final = 100  # 100 + percent

# Options
CONF_SHOW_IN_SIDEBAR: Final = "show_in_sidebar"
CONF_ADMIN_ONLY: Final = "admin_only"

# Dashboard
DASHBOARD_URL: Final = "ems-esp"
DASHBOARD_TITLE: Final = "EMS-ESP"
DASHBOARD_ICON: Final = "mdi:water-boiler"

# Bosch HMI 800 panel (served by the integration)
CONF_PANEL_SHOW_IN_SIDEBAR: Final = "panel_show_in_sidebar"
CONF_PANEL_ADMIN_ONLY: Final = "panel_admin_only"
CONF_PANEL_TOKEN: Final = "panel_token"
CONF_INSTALLER_NAME: Final = "installer_name"
CONF_INSTALLER_ADDR: Final = "installer_addr"
CONF_INSTALLER_TEL: Final = "installer_tel"
PANEL_FILE: Final = "bosch-ui800.html"
PANEL_TITLE: Final = "Bosch HMI 800"
PANEL_ICON: Final = "mdi:thermostat-box"
PANEL_SIDEBAR_URL: Final = "bosch-hmi800"
URL_PANEL: Final = "/api/ems_esp_manager/panel"
URL_PANEL_ADMIN: Final = "/api/ems_esp_manager/panel/admin"

# Manufacturer chosen at first setup (entry.data["vendor"]); old entries = Bosch
CONF_VENDOR: Final = "vendor"
VENDOR_BOSCH: Final = "bosch"
VENDOR_SAMSUNG: Final = "samsung"
VENDORS: Final = [VENDOR_BOSCH, VENDOR_SAMSUNG]

# Samsung EHS (data from the HACS integration "EHS Sentinel")
EHS_DOMAIN: Final = "ehs_sentinel"
SAMSUNG_UNIQUE_ID: Final = "samsung_ehs"
SAMSUNG_TITLE: Final = "Samsung EHS"
SAMSUNG_PANEL_TITLE: Final = "Samsung EHS"
SAMSUNG_PANEL_ICON: Final = "mdi:heat-pump"
SAMSUNG_SIDEBAR_URL: Final = "samsung-hmi"  # not "samsung-ehs": used by the old separate panel


def entry_vendor(entry) -> str:
    """Manufacturer of a config entry (entries before v1.13 are EMS-ESP/Bosch)."""
    return (getattr(entry, "data", None) or {}).get(CONF_VENDOR, VENDOR_BOSCH)


def ems_entries(hass) -> list:
    """Config entries of this integration that talk to an EMS-ESP gateway."""
    return [e for e in hass.config_entries.async_entries(DOMAIN) if entry_vendor(e) == VENDOR_BOSCH]


def samsung_entries(hass) -> list:
    """Config entries of this integration for a Samsung EHS heat pump."""
    return [e for e in hass.config_entries.async_entries(DOMAIN) if entry_vendor(e) == VENDOR_SAMSUNG]
