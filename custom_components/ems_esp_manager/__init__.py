"""EMS-ESP Manager: firmware updates, status and a ready-made dashboard for EMS-ESP.

Since v1.13 the manufacturer is chosen at the first setup:
- Bosch / Buderus: EMS-ESP gateway (everything below)
- Samsung EHS: panel for the entities of EHS Sentinel (see samsung/)
- Heizstab: novolto heating rod on its own (see heater.py)
- ESP-Bus-Adapter: Siemens BSB/LPB controllers (Brötje, Elco, Baxi …) via ESPHome or BSB-LAN (see bsb.py)
"""

from __future__ import annotations

import logging
import re

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EmsEspApi, EmsEspError
from .const import (
    CONF_ADMIN_ONLY,
    CONF_SHOW_IN_SIDEBAR,
    DOMAIN,
    VENDOR_BOSCH,
    VENDOR_BSB,
    VENDOR_HEATER,
    VENDOR_SAMSUNG,
    ems_entries,
    entry_vendor,
)
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
from .ntp import async_setup_ntp
from .schedule import async_setup_schedule
from .panel import async_remove_panel, async_setup_panel
from .names import lang

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Gemeinsamer Kern + automatische Erkennung von ESP-Bus-Adaptern nach dem Start.

    v1.24.0: Panel-Verwaltung, Speicher (inkl. Umzug in Herstellerblöcke), Views und
    Websocket-Befehle entstehen hier genau einmal – bevor HA die einzelnen Geräte
    (Bosch, Samsung, Heizstab, ESP-Bus-Adapter …) parallel einrichtet.
    """
    from .panel import async_setup_core

    try:
        await async_setup_core(hass)
    except Exception:  # noqa: BLE001 - Geräte legen den Kern sonst beim Einrichten an
        _LOGGER.exception("Gemeinsamer Kern konnte beim Start nicht angelegt werden")
    try:
        from .bsb import async_setup_discovery

        async_setup_discovery(hass)
    except Exception:  # noqa: BLE001 - Erkennung darf den Start nie verhindern
        _LOGGER.exception("ESP-Bus-Adapter-Erkennung nicht gestartet")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EmsEspConfigEntry) -> bool:
    """Set up one entry – EMS-ESP gateway (Bosch) or Samsung EHS."""
    if entry_vendor(entry) == VENDOR_SAMSUNG:
        from .ehs import async_setup_samsung

        return await async_setup_samsung(hass, entry)
    if entry_vendor(entry) == VENDOR_HEATER:
        from .heater import async_setup_heater_entry

        return await async_setup_heater_entry(hass, entry)
    if entry_vendor(entry) == VENDOR_BSB:
        from .bsb import async_setup_bsb_entry

        return await async_setup_bsb_entry(hass, entry)
    return await _async_setup_ems(hass, entry)


async def _async_setup_ems(hass: HomeAssistant, entry: EmsEspConfigEntry) -> bool:
    """Set up one EMS-ESP gateway."""
    api = EmsEspApi(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    status = StatusCoordinator(hass, entry, api)
    releases = ReleaseCoordinator(hass, entry)

    # Bosch EHS panel first: it only needs Home Assistant, not the gateway
    await _async_setup_panel(hass, entry)
    async_setup_buslog(hass)
    async_setup_mqtt(hass)
    async_setup_ntp(hass)

    await status.async_config_entry_first_refresh()
    await releases.async_load_cache()
    # GitHub may be unreachable – never block setup (v1.22.2: im Hintergrund statt bis zu 60 s warten)
    entry.async_create_background_task(hass, releases.async_refresh(), f"{DOMAIN}_releases_{entry.entry_id}")

    entry.runtime_data = EmsEspRuntime(
        api=api, status=status, releases=releases, language=lang(hass.config.language)
    )
    _async_cleanup_registry(hass, entry)

    # live EMS bus log (server-sent events from the gateway)
    runtime = entry.runtime_data
    runtime.log = EmsEspLog(api, runtime.notify_log)
    from .buslog import async_log_flag

    runtime.log.recording = await async_log_flag(hass, "bus")
    schedule = async_setup_schedule(hass)
    # load the stored switch programs before the log stream can feed new telegrams
    await schedule.async_load()
    runtime.log.listeners.append(schedule.cache.feed)

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
            if isinstance(settings.get("locale"), str):
                runtime.locale = settings["locale"]
                info.append(f"Sprache {runtime.locale}")
            if isinstance(settings.get("analog_enabled"), bool):
                runtime.extra["analog_enabled"] = settings["analog_enabled"]
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
        if isinstance(settings.get("locale"), str):
            runtime.locale = settings["locale"]
        if isinstance(settings.get("analog_enabled"), bool):
            runtime.extra["analog_enabled"] = settings["analog_enabled"]
    except EmsEspError:
        pass
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # read the plant once when Home Assistant starts (detected devices, all values with
    # read/write flags) – used by the "Detected plant" sensor and the mapping tab
    async def _scan(_now=None) -> None:
        from .entity_map import ems_scan

        try:
            result = await ems_scan(api)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("EMS-ESP plant scan failed", exc_info=True)
            return
        if result.get("devices") is not None or result.get("entities") is not None:
            runtime.extra["scan"] = result
            status.async_update_listeners()

    runtime.extra["scan_fn"] = _scan
    entry.async_create_background_task(hass, _scan(), "ems_esp_manager_scan")

    # v1.22.0: Inhalt von „EMS-ESP“ steckt als Reiter „Bosch“ in EMS-EHS – kein eigener
    # Eintrag in der Seitenleiste mehr; /ems-esp bleibt für alte Links erreichbar.
    await async_register_dashboard(
        hass,
        show_in_sidebar=False,
        admin_only=entry.options.get(CONF_ADMIN_ONLY, False),
    )
    from .ehs import async_setup_hub

    await async_setup_hub(hass, entry)   # „System & Panel“ immer (v1.20.3)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: EmsEspConfigEntry) -> None:
    """Sidebar / admin / panel settings changed in 'Konfigurieren'."""
    await async_register_dashboard(
        hass,
        show_in_sidebar=False,
        admin_only=entry.options.get(CONF_ADMIN_ONLY, False),
    )
    from .ehs import async_refresh_ehs_page

    await async_refresh_ehs_page(hass)
    await _async_setup_panel(hass, entry)


async def _async_setup_panel(hass: HomeAssistant, entry: EmsEspConfigEntry) -> None:
    """Bosch EHS panel – one for all gateways, settings of the first entry."""
    first = ems_entries(hass)[0]
    if first.entry_id != entry.entry_id and first.state is ConfigEntryState.LOADED:
        return
    try:
        await async_setup_panel(hass, dict(entry.options), VENDOR_BOSCH)
    except Exception:  # noqa: BLE001 - the panel must never break the integration
        _LOGGER.exception("Bosch EHS panel could not be set up")


async def async_unload_entry(hass: HomeAssistant, entry: EmsEspConfigEntry) -> bool:
    """Unload a config entry."""
    if entry_vendor(entry) == VENDOR_SAMSUNG:
        from .ehs import async_unload_samsung

        return await async_unload_samsung(hass, entry)
    if entry_vendor(entry) == VENDOR_HEATER:
        from .heater import async_unload_heater_entry

        return await async_unload_heater_entry(hass, entry)
    if entry_vendor(entry) == VENDOR_BSB:
        from .bsb import async_unload_bsb_entry

        return await async_unload_bsb_entry(hass, entry)
    if entry.runtime_data.log:
        await entry.runtime_data.log.stop()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        from .ehs import async_release_hub

        async_release_hub(hass, entry)
        others = [
            e
            for e in ems_entries(hass)
            if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED
        ]
        if not others:
            async_remove_dashboard(hass)
            # only the Bosch sidebar entry – a Samsung panel keeps running
            async_remove_panel(hass, VENDOR_BOSCH)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: EmsEspConfigEntry) -> None:
    """v1.22.2: Eintrag gelöscht – Eingriffe an Wärmepumpe/Heizstab zurücknehmen.

    Home Assistant entlädt den Eintrag vorher (falls er geladen war); ein Eintrag,
    der nie fertig eingerichtet wurde (Gateway offline), wird gar nicht entladen –
    deshalb hier auch Seitenleiste und Regelungen sicher entfernen.
    """
    from .panel import _get as _get_mgr

    vendor = entry_vendor(entry)
    mgr = _get_mgr(hass)
    if vendor == VENDOR_HEATER:
        from .heater import get as _get_heater

        h = _get_heater(hass)
        try:
            if h is not None:
                await h.async_save_cfg({"enabled": False})   # schaltet aus, Tick holt es notfalls nach
            elif mgr is not None and isinstance(mgr.vdata(VENDOR_HEATER).get("heater"), dict):
                mgr.vdata(VENDOR_HEATER)["heater"]["enabled"] = False
                await mgr.async_save()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Heizstab beim Entfernen nicht ausgeschaltet", exc_info=True)
        return
    if vendor in (VENDOR_SAMSUNG, VENDOR_BSB):
        return
    # Bosch / EMS-ESP: nur wenn kein anderes Gateway mehr läuft
    others = [
        e for e in ems_entries(hass)
        if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED
    ]
    if others:
        return
    if mgr is not None:
        api = EmsEspApi(
            async_get_clientsession(hass),
            entry.data[CONF_HOST],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
        )
        try:
            from .power_limit import async_release_on_remove as _plim_release

            done = await _plim_release(hass, mgr, api)
            if done:
                _LOGGER.info("Integration entfernt: %s", ", ".join(done))
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Leistungsbegrenzung beim Entfernen nicht zurückgesetzt", exc_info=True)
        try:
            from .dhw_optimizer import async_release_on_remove as _dhw_release

            await _dhw_release(hass, mgr)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Warmwasseroptimierung beim Entfernen nicht zurückgesetzt", exc_info=True)
    async_remove_dashboard(hass)
    async_remove_panel(hass, VENDOR_BOSCH)


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
