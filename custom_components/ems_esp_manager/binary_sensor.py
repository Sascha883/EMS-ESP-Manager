"""'Update available' indicator (shown only in the EMS-ESP dashboard)."""

from __future__ import annotations

from typing import Any

from awesomeversion import AwesomeVersion, AwesomeVersionException

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CHANGELOG_STABLE, CHANNEL_STABLE
from .coordinator import EmsEspConfigEntry, EmsEspRuntime
from .entity import EmsEspEntity


def latest_stable(rt: EmsEspRuntime) -> str | None:
    release = (rt.releases.data or {}).get(CHANNEL_STABLE)
    return release.version if release else None


def update_available(rt: EmsEspRuntime) -> bool:
    installed = (rt.status.data or {}).get("emsesp_version")
    latest = latest_stable(rt)
    if not installed or not latest:
        return False
    try:
        return AwesomeVersion(latest) > AwesomeVersion(installed)
    except AwesomeVersionException:
        return latest != installed


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EmsEspConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([EmsEspUpdateAvailable(entry)])


class EmsEspUpdateAvailable(EmsEspEntity, BinarySensorEntity):
    """On when a newer stable firmware exists."""

    _attr_device_class = BinarySensorDeviceClass.UPDATE

    def __init__(self, entry: EmsEspConfigEntry) -> None:
        super().__init__(entry, "update_available")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        for coordinator in (self._runtime.status, self._runtime.releases):
            self.async_on_remove(coordinator.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._runtime.status.last_update_success or self._runtime.installing

    @property
    def is_on(self) -> bool:
        return update_available(self._runtime)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rt = self._runtime
        release = (rt.releases.data or {}).get(CHANNEL_STABLE)
        return {
            "installed_version": (rt.status.data or {}).get("emsesp_version"),
            "latest_version": latest_stable(rt),
            "release_url": (release.url if release and release.url else None)
            or CHANGELOG_STABLE,
        }
