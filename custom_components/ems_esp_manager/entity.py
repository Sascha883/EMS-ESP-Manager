"""Base entity for EMS-ESP Manager."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER
from .coordinator import EmsEspConfigEntry
from .names import NAMES


class EmsEspEntity(Entity):
    """Common device info for all entities of one gateway."""

    _attr_has_entity_name = True

    def __init__(self, entry: EmsEspConfigEntry, key: str) -> None:
        self._entry = entry
        self._runtime = entry.runtime_data
        status = self._runtime.status.data or {}
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_name = NAMES[self._runtime.language].get(key)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="EMS-ESP Gateway",
            manufacturer=MANUFACTURER,
            model=status.get("model") or status.get("esp_platform") or "EMS-ESP",
            sw_version=status.get("emsesp_version"),
            configuration_url=self._runtime.api.base,
        )
