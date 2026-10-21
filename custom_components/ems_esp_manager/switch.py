"""Switches: EMS-ESP values missing in Home Assistant, analog sensors on/off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import EmsEspError
from .coordinator import EmsEspConfigEntry
from .entity import EmsEspEntity
from .extra_values import async_setup_platform_extra


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EmsEspConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    await async_setup_platform_extra(hass, entry, "switch", async_add_entities)
    async_add_entities([EmsEspAnalogSwitch(entry)])


class EmsEspAnalogSwitch(EmsEspEntity, SwitchEntity):
    """EMS-ESP setting 'analog_enabled' (Einstellungen → Sensoren → Analogsensoren).

    Needed for the built-in Core_voltage / Supply_voltage of the BBQKees E32 V2.2
    and for every analog sensor set up on a free GPIO. No restart needed.
    """

    _attr_icon = "mdi:sine-wave"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: EmsEspConfigEntry) -> None:
        super().__init__(entry, "analog_enabled")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.status.async_add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool | None:
        return self._runtime.extra.get("analog_enabled")

    async def _set(self, on: bool) -> None:
        from .gateway_sensors import async_set_analog_enabled

        try:
            await async_set_analog_enabled(self._runtime, on)
        except EmsEspError as err:
            raise HomeAssistantError(f"EMS-ESP: {err}") from err
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
