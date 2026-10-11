"""EMS-ESP Manager: firmware updates, status and a ready-made dashboard for EMS-ESP."""

from __future__ import annotations

import logging
import re

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EmsEspApi, EmsEspError
from .const import CONF_ADMIN_ONLY, CONF_SHOW_IN_SIDEBAR, DOMAIN
from .coordinator import (
    EmsEspConfigEntry,
    EmsEspRuntime,
    ReleaseCoordinator,
    StatusCoordinator,
)
from .dashboard import async_register_dashboard, async_remove_dashboard
from .log import EmsEspLog
from .buslog import async_setup_buslog
from .mqtt import async_setup_mqtt
from .schedule import async_setup_schedule
from .panel import async_remove_panel, async_setup_panel
from .names import lang

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: EmsEspConfigEntry) -> bool:
    """Set up one EMS-ESP gateway."""
    api = EmsEspApi(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    status = StatusCoordinator(hass, entry, api)
    releases = ReleaseCoordinator(hass, entry)

    # Bosch panel first: it only needs Home Assistant, not the gateway
    await _async_setup_panel(hass, entry)
    async_setup_buslog(hass)
    async_setup_mqtt(hass)

    await status.async_config_entry_first_refresh()
    await releases.async_load_cache()
    await releases.async_refresh()  # GitHub may be unreachable – never block setup

    entry.runtime_data = EmsEspRuntime(
        api=api, status=status, releases=releases, language=lang(hass.config.language)
    )
    _async_cleanup_registry(hass, entry)

    # live EMS bus log (server-sent events from the gateway)
    runtime = entry.runtime_data
    runtime.log = EmsEspLog(api, lambda: status.async_update_listeners())
    runtime.log.listeners.append(async_setup_schedule(hass).cache.feed)

    async def _log_connected(quiet: bool = False) -> None:
        """Log stream (re)connected – e.g. after a restart or firmware update.

        EMS-ESP forgets 'watch' on every restart, so the capture chosen in
        Home Assistant is switched on again here.
        """
        from .log import TX_MODES

        info: list[str] = []
        try:
            settings = await api.get_settings()
            bus_id, tx_mode = settings.get("ems_bus_id"), settings.get("tx_mode")
            if isinstance(bus_id, int):
                runtime.bus_id = bus_id
                info.append(f"Bus-ID 0x{bus_id:02X}")
            if isinstance(tx_mode, int):
                runtime.tx_mode = tx_mode
                info.append(f"Tx-Modus {TX_MODES.get(tx_mode, tx_mode)}")
        except EmsEspError as err:
            _LOGGER.debug("EMS-ESP settings not readable: %s", err)
        try:
            st = await api.get_status()
            info.append(f"Firmware {st.get('emsesp_version', '?')}")
            if "bus_status" in st:
                code = st.get("bus_status")
                text = {0: "verbunden", 1: "Sendefehler", 2: "getrennt"}.get(code, code)
                info.append(f"Bus-Status {code} ({text})")
        except EmsEspError:
            pass
        try:
            runtime.log_level = int((await api.get_log_settings()).get("level", runtime.log_level))
        except EmsEspError:
            pass
        if runtime.watch_mode in ("on", "raw"):
            try:
                await api.command("system", "watch", runtime.watch_mode)
                if runtime.log_level < 5:
                    await api.set_log_level(5)
                    runtime.log_level = 5
                info.append(f"Bus-Mitschnitt wieder eingeschaltet ({runtime.watch_mode})")
            except EmsEspError as err:
                info.append(f"Bus-Mitschnitt konnte nicht eingeschaltet werden ({err})")
        line = ", ".join(info or ["ohne Details"])
        # silence reconnects (no bus capture, quiet log) happen every 5 min –
        # only write the status line again when something changed
        if not quiet or line != runtime.extra.get("last_connect_line"):
            runtime.log.note("Log-Verbindung hergestellt – " + line)
        runtime.extra["last_connect_line"] = line

    runtime.log.on_connect = _log_connected
    runtime.log.start(
        lambda coro, name: entry.async_create_background_task(hass, coro, name)
    )
    try:
        runtime.log_level = int((await api.get_log_settings()).get("level", 6))
    except EmsEspError:
        pass
    try:
        settings = await api.get_settings()
        if isinstance(settings.get("tx_mode"), int):
            runtime.tx_mode = settings["tx_mode"]
        if isinstance(settings.get("ems_bus_id"), int):
            runtime.bus_id = settings["ems_bus_id"]
    except EmsEspError:
        pass
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await async_register_dashboard(
        hass,
        show_in_sidebar=entry.options.get(CONF_SHOW_IN_SIDEBAR, True),
        admin_only=entry.options.get(CONF_ADMIN_ONLY, False),
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: EmsEspConfigEntry) -> None:
    """Sidebar / admin / panel settings changed in 'Konfigurieren'."""
    await async_register_dashboard(
        hass,
        show_in_sidebar=entry.options.get(CONF_SHOW_IN_SIDEBAR, True),
        admin_only=entry.options.get(CONF_ADMIN_ONLY, False),
    )
    await _async_setup_panel(hass, entry)


async def _async_setup_panel(hass: HomeAssistant, entry: EmsEspConfigEntry) -> None:
    """Bosch HMI 800 panel – one for all gateways, settings of the first entry."""
    first = hass.config_entries.async_entries(DOMAIN)[0]
    if first.entry_id != entry.entry_id and first.state is ConfigEntryState.LOADED:
        return
    try:
        await async_setup_panel(hass, dict(entry.options))
    except Exception:  # noqa: BLE001 - the panel must never break the integration
        _LOGGER.exception("Bosch HMI 800 panel could not be set up")


async def async_unload_entry(hass: HomeAssistant, entry: EmsEspConfigEntry) -> bool:
    """Unload a config entry."""
    if entry.runtime_data.log:
        await entry.runtime_data.log.stop()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        others = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED
        ]
        if not others:
            async_remove_dashboard(hass)
            async_remove_panel(hass)
    return unloaded


# entities that existed in earlier versions and are gone now
_OBSOLETE_KEYS = {"firmware", "update_channel", "firmware_stable", "firmware_dev"}
_UNNAMED_ID = re.compile(r"^[a-z_]+\.ems_esp_gateway(_\d+)?$")


@callback
def _async_cleanup_registry(hass: HomeAssistant, entry: EmsEspConfigEntry) -> None:
    """Remove obsolete entities and give unnamed ones a readable entity id.

    - v1.2: the update entity was replaced (no update notice under Settings)
    - v1.3: development channel removed (only stable firmware)
    - entity ids like 'button.ems_esp_gateway_2' (created while names were
      missing) are renamed to e.g. 'button.ems_esp_restart'
    """
    ent_reg = er.async_get(hass)
    prefix = f"{entry.unique_id or entry.entry_id}_"
    for reg_entry in list(er.async_entries_for_config_entry(ent_reg, entry.entry_id)):
        key = reg_entry.unique_id.removeprefix(prefix)
        if reg_entry.domain == "update" or key in _OBSOLETE_KEYS:
            ent_reg.async_remove(reg_entry.entity_id)
            continue
        if _UNNAMED_ID.match(reg_entry.entity_id):
            new_id = f"{reg_entry.domain}.ems_esp_{key}"
            if ent_reg.async_get(new_id) is None and hass.states.get(new_id) is None:
                ent_reg.async_update_entity(reg_entry.entity_id, new_entity_id=new_id)
