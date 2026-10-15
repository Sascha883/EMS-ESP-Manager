"""Selection of a stable firmware version."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import EmsEspConfigEntry
from .api import EmsEspError
from .entity import EmsEspEntity
from .names import LOG_LEVEL_OPTIONS, TX_MODE_OPTIONS, WATCH_OPTIONS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EmsEspConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    from .extra_values import async_setup_platform_extra

    await async_setup_platform_extra(hass, entry, "select", async_add_entities)
    async_add_entities(
        [
            EmsEspVersionSelect(entry),
            EmsEspWatchSelect(entry),
            EmsEspLogLevelSelect(entry),
            EmsEspTxModeSelect(entry),
        ]
    )


class EmsEspVersionSelect(EmsEspEntity, CoordinatorEntity, SelectEntity):
    """Pick any stable firmware version (e.g. to go back to an older one)."""

    _attr_icon = "mdi:tag-multiple"

    def __init__(self, entry: EmsEspConfigEntry) -> None:
        CoordinatorEntity.__init__(self, entry.runtime_data.releases)
        EmsEspEntity.__init__(self, entry, "stable_version")

    @property
    def _versions(self) -> list[str]:
        releases = (self._runtime.releases.data or {}).get("stable_list") or []
        return [r.version for r in releases]

    @property
    def available(self) -> bool:
        # stays available, so the GitHub problem can be shown in the dashboard
        return True

    @property
    def options(self) -> list[str]:
        return self._versions or ["-"]

    @property
    def current_option(self) -> str | None:
        versions = self._versions
        if self._runtime.selected_version in versions:
            return self._runtime.selected_version
        return versions[0] if versions else None

    @property
    def extra_state_attributes(self) -> dict:
        releases = (self._runtime.releases.data or {}).get("stable_list") or []
        coord = self._runtime.releases
        return {
            "releases": [
                {"version": r.version, "date": r.date, "url": r.url} for r in releases
            ],
            "github_error": coord.error,
            "retry_at": coord.retry_at,
        }

    async def async_select_option(self, option: str) -> None:
        self._runtime.selected_version = option
        self.async_write_ha_state()


class EmsEspWatchSelect(EmsEspEntity, SelectEntity):
    """Log the EMS bus traffic: off / telegrams / raw data."""

    _attr_icon = "mdi:bus-alert"

    def __init__(self, entry: EmsEspConfigEntry) -> None:
        super().__init__(entry, "watch_mode")
        self._map = WATCH_OPTIONS[self._runtime.language]
        self._attr_options = list(self._map)

    @property
    def current_option(self) -> str:
        for label, value in self._map.items():
            if value == self._runtime.watch_mode:
                return label
        return self._attr_options[0]

    async def async_select_option(self, option: str) -> None:
        value = self._map[option]
        try:
            await self._runtime.api.command("system", "watch", value)
            if value != "off" and self._runtime.log_level < 5:
                # telegrams are logged at level 'notice'
                await self._runtime.api.set_log_level(5)
                self._runtime.log_level = 5
                self._runtime.status.async_update_listeners()
        except EmsEspError as err:
            raise HomeAssistantError(f"EMS-ESP: {err}") from err
        self._runtime.watch_mode = value
        self.async_write_ha_state()


class EmsEspLogLevelSelect(EmsEspEntity, SelectEntity):
    """How much the gateway writes into its log."""

    _attr_icon = "mdi:text-box-search-outline"

    def __init__(self, entry: EmsEspConfigEntry) -> None:
        super().__init__(entry, "log_level")
        self._map = LOG_LEVEL_OPTIONS[self._runtime.language]
        self._attr_options = list(self._map)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.status.async_add_listener(self.async_write_ha_state)
        )

    @property
    def current_option(self) -> str:
        for label, value in self._map.items():
            if value == self._runtime.log_level:
                return label
        return list(self._map)[3]

    async def async_select_option(self, option: str) -> None:
        level = self._map[option]
        try:
            await self._runtime.api.set_log_level(level)
        except EmsEspError as err:
            raise HomeAssistantError(f"EMS-ESP: {err}") from err
        self._runtime.log_level = level
        self.async_write_ha_state()


class EmsEspTxModeSelect(EmsEspEntity, SelectEntity):
    """How EMS-ESP sends on the bus (settings 'tx_mode').

    Since firmware 3.8.2 the default is 'Auto', which picks the timing at every
    start – on Sascha's Bosch bus it sometimes picked wrong (07.10.2026: Bus-Status
    'Sendefehler', thermostat not detected). After a change EMS-ESP is restarted
    so the devices are detected again with the new mode.
    """

    _attr_icon = "mdi:transit-connection-variant"

    def __init__(self, entry: EmsEspConfigEntry) -> None:
        super().__init__(entry, "tx_mode")
        self._map = TX_MODE_OPTIONS[self._runtime.language]
        self._attr_options = list(self._map)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.status.async_add_listener(self.async_write_ha_state)
        )

    @property
    def current_option(self) -> str | None:
        for label, value in self._map.items():
            if value == self._runtime.tx_mode:
                return label
        return None

    @property
    def extra_state_attributes(self) -> dict:
        return {"tx_mode_value": self._runtime.tx_mode, "bus_id": (
            f"0x{self._runtime.bus_id:02X}" if self._runtime.bus_id is not None else None
        )}

    async def async_select_option(self, option: str) -> None:
        value = self._map[option]
        rt = self._runtime
        try:
            data = await rt.api.set_settings({"tx_mode": value})
            rt.tx_mode = int(data.get("tx_mode", value))
            if rt.log is not None:
                rt.log.note(f"Tx-Modus auf {option} gestellt – EMS-ESP wird neu gestartet")
            await rt.api.command("system", "restart")
        except EmsEspError as err:
            raise HomeAssistantError(f"EMS-ESP: {err}") from err
        self.async_write_ha_state()
