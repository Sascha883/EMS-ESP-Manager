"""Registers the EMS-ESP dashboard in the sidebar – no manual dashboard setup needed."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant, callback

from .const import DASHBOARD_ICON, DASHBOARD_TITLE, DASHBOARD_URL, DOMAIN
from .dashboard_builder import build_dashboard

_LOGGER = logging.getLogger(__name__)

_DATA_DASHBOARD = f"{DOMAIN}_dashboard"


def _make_dashboard_class():
    """Create the LovelaceConfig subclass lazily (lovelace internals may change)."""
    from homeassistant.components.lovelace.dashboard import LovelaceConfig
    from homeassistant.helpers.json import json_bytes, json_fragment

    class EmsEspDashboard(LovelaceConfig):
        """Read-only dashboard, generated from the registries on every load."""

        def __init__(self, hass: HomeAssistant) -> None:
            super().__init__(
                hass,
                DASHBOARD_URL,
                {
                    "id": DOMAIN,
                    "mode": "yaml",
                    "title": DASHBOARD_TITLE,
                    "icon": DASHBOARD_ICON,
                    "show_in_sidebar": True,
                    "require_admin": False,
                },
            )

        @property
        def mode(self) -> str:
            return "yaml"

        async def async_get_info(self) -> dict[str, Any]:
            return {"mode": "yaml", "views": len(build_dashboard(self.hass)["views"])}

        async def async_load(self, force: bool) -> dict[str, Any]:
            return build_dashboard(self.hass)

        async def async_json(self, force: bool):
            return json_fragment(json_bytes(build_dashboard(self.hass)))

    return EmsEspDashboard


async def async_register_dashboard(
    hass: HomeAssistant, show_in_sidebar: bool = True, admin_only: bool = False
) -> None:
    """Add the 'EMS-ESP' dashboard (or update its sidebar/admin settings)."""
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        lovelace = hass.data.get(LOVELACE_DATA)
        if lovelace is None:
            _LOGGER.warning("Lovelace not loaded – EMS-ESP dashboard not registered")
            return
        if not hass.data.get(_DATA_DASHBOARD):
            if DASHBOARD_URL in lovelace.dashboards:
                _LOGGER.info(
                    "A dashboard with the path '%s' already exists – not replacing it",
                    DASHBOARD_URL,
                )
                return
            lovelace.dashboards[DASHBOARD_URL] = _make_dashboard_class()(hass)
        # hidden from the sidebar = no title/icon; still reachable via /ems-esp
        frontend.async_register_built_in_panel(
            hass,
            "lovelace",
            sidebar_title=DASHBOARD_TITLE if show_in_sidebar else None,
            sidebar_icon=DASHBOARD_ICON if show_in_sidebar else None,
            frontend_url_path=DASHBOARD_URL,
            config={"mode": "yaml"},
            require_admin=admin_only,
            update=True,
        )
        hass.data[_DATA_DASHBOARD] = True
    except Exception:  # noqa: BLE001 - never break the integration because of the UI
        _LOGGER.exception(
            "Could not register the EMS-ESP dashboard automatically. "
            "The entities are still available on the device page"
        )


@callback
def async_remove_dashboard(hass: HomeAssistant) -> None:
    """Remove the sidebar dashboard."""
    if not hass.data.pop(_DATA_DASHBOARD, False):
        return
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        frontend.async_remove_panel(hass, DASHBOARD_URL)
        lovelace = hass.data.get(LOVELACE_DATA)
        if lovelace is not None:
            lovelace.dashboards.pop(DASHBOARD_URL, None)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Removing dashboard failed", exc_info=True)
