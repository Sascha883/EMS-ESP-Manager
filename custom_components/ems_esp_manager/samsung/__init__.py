"""Samsung EHS heat pump in the EMS-ESP Manager (data from "EHS Sentinel").

Chosen at the first setup ("Samsung EHS"). There is no gateway to talk to:
the HACS integration EHS Sentinel delivers the entities, the manager adds

- the panel (same file as the Bosch HMI 800, Samsung colours and menus) at
  /samsung-ehs in the sidebar,
- connection status, log recording and packet log of EHS Sentinel
  (Service → Verbindung & Protokoll),
- the shared functions of the manager (Heizstab, kiosk link, installer info).

Replaces the separate integration "Samsung EHS Panel" (samsung_ehs_panel).
"""

from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from ..const import DOMAIN, EHS_DOMAIN, VENDOR_SAMSUNG
from . import websocket as samsung_ws
from .logbuffer import EhsLogBuffer
from .websocket import _DATA

_LOGGER = logging.getLogger(__name__)

OLD_DOMAIN = "samsung_ehs_panel"
_NOTE_OLD = f"{DOMAIN}_samsung_old_panel"
_NOTE_EHS = f"{DOMAIN}_samsung_no_ehs"


async def async_setup_samsung(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Samsung EHS part (one entry)."""
    from ..panel import async_setup_panel

    data = hass.data.setdefault(_DATA, {})
    if "logbuffer" not in data:
        data["logbuffer"] = EhsLogBuffer()
    data["logbuffer"].attach()
    if not data.get("ws_registered"):
        samsung_ws.async_register(hass)
        data["ws_registered"] = True

    try:
        await async_setup_panel(hass, dict(entry.options), VENDOR_SAMSUNG)
    except Exception:  # noqa: BLE001 - the panel must never break the integration
        _LOGGER.exception("Samsung EHS panel could not be set up")

    _notify(hass)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_samsung(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove the Samsung sidebar entry and stop the log recording."""
    from ..panel import async_remove_panel

    buf = hass.data.get(_DATA, {}).get("logbuffer")
    if buf:
        buf.detach()
    async_remove_panel(hass, VENDOR_SAMSUNG)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from ..panel import async_setup_panel

    await async_setup_panel(hass, dict(entry.options), VENDOR_SAMSUNG)


def _notify(hass: HomeAssistant) -> None:
    """Hints: EHS Sentinel missing / old separate panel still installed."""
    if hass.config_entries.async_entries(EHS_DOMAIN):
        persistent_notification.async_dismiss(hass, _NOTE_EHS)
    else:
        persistent_notification.async_create(
            hass,
            "Für Samsung EHS braucht der EMS-ESP Manager die HACS-Integration "
            "**EHS Sentinel** (Verbindung zum RS485-Adapter der Wärmepumpe). "
            "Bitte über HACS installieren und einrichten – das Panel „Samsung EHS“ "
            "zeigt die Werte danach automatisch.",
            title="EMS-ESP Manager – Samsung EHS",
            notification_id=_NOTE_EHS,
        )
    old = [
        e
        for e in hass.config_entries.async_entries(OLD_DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if old:
        persistent_notification.async_create(
            hass,
            "Das Samsung-Panel ist jetzt im EMS-ESP Manager enthalten (Seitenleiste "
            "„Samsung EHS“). Die separate Integration **Samsung EHS Panel** wird nicht "
            "mehr gebraucht: unter Einstellungen → Geräte & Dienste löschen und in HACS "
            "entfernen. Eigene Zuordnungen aus dem alten Panel übernimmt das neue beim "
            "ersten Öffnen als Administrator im selben Browser.",
            title="EMS-ESP Manager – Samsung EHS",
            notification_id=_NOTE_OLD,
        )
    else:
        persistent_notification.async_dismiss(hass, _NOTE_OLD)
