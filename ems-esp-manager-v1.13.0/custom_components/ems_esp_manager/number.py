"""Number entities the manager creates for EMS-ESP values missing in Home Assistant."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EmsEspConfigEntry
from .extra_values import async_setup_platform_extra


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EmsEspConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    await async_setup_platform_extra(hass, entry, "number", async_add_entities)
