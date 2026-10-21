"""Status sensors of the EMS-ESP gateway."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import platform_string
from .const import (
    MANAGER_VERSION,
    CHANNEL_STABLE,
    STATUS_ERROR_UPLOAD,
    STATUS_INVALID_GPIO,
    STATUS_PENDING_RESTART,
    STATUS_PENDING_UPLOAD,
    STATUS_RESTART_REQUESTED,
    STATUS_UPLOADING,
)
from .entity_map import plant_summary
from .coordinator import EmsEspConfigEntry, EmsEspRuntime
from .entity import EmsEspEntity
from .names import STATES

SYSTEM_STATES = [
    "normal",
    "pending_upload",
    "uploading",
    "upload_error",
    "pending_restart",
    "restarting",
    "invalid_gpio",
]
BUS_STATES = ["connected", "tx_errors", "offline"]


def _system_state(data: dict[str, Any]) -> str:
    code = int(data.get("status") or 0)
    if code >= STATUS_UPLOADING:
        return "uploading"
    return {
        STATUS_PENDING_UPLOAD: "pending_upload",
        STATUS_ERROR_UPLOAD: "upload_error",
        STATUS_PENDING_RESTART: "pending_restart",
        STATUS_RESTART_REQUESTED: "restarting",
        STATUS_INVALID_GPIO: "invalid_gpio",
    }.get(code, "normal")


def _bus_state(data: dict[str, Any]) -> str | None:
    code = data.get("bus_status")
    if code is None:
        return None
    return BUS_STATES[int(code)] if int(code) < len(BUS_STATES) else None


# /rest/systemStatus "network_status": 10 = Ethernet connected (EMS-ESP custom code),
# otherwise the WiFi.status() code of the ESP32 (3 = WL_CONNECTED)
NETWORK_ETHERNET = 10


def network_type(data: dict[str, Any]) -> str | None:
    """'lan' or 'wifi' – how the gateway is connected; None = unknown."""
    code = data.get("network_status")
    if code is not None:
        try:
            return "lan" if int(code) == NETWORK_ETHERNET else "wifi"
        except (TypeError, ValueError):
            return None
    # older firmware without network_status: Ethernet reports RSSI 0
    rssi = data.get("wifi_rssi")
    if isinstance(rssi, (int, float)):
        return "wifi" if rssi else "lan"
    return None


def _progress(rt: EmsEspRuntime) -> int | None:
    code = int((rt.status.data or {}).get("status") or 0)
    if code >= STATUS_UPLOADING:
        return min(code - STATUS_UPLOADING, 100)
    if code in (STATUS_PENDING_RESTART, STATUS_RESTART_REQUESTED):
        return 100
    return 0


def _release(rt: EmsEspRuntime, channel: str) -> str | None:
    rel = (rt.releases.data or {}).get(channel)
    return rel.version if rel else None


@dataclass(frozen=True, kw_only=True)
class EmsEspSensorDescription(SensorEntityDescription):
    """Describes a sensor."""

    value_fn: Callable[[EmsEspRuntime], Any]
    from_releases: bool = False
    attr_fn: Callable[[EmsEspRuntime], dict[str, Any]] | None = None


SENSORS: tuple[EmsEspSensorDescription, ...] = (
    EmsEspSensorDescription(
        key="system_state",
        icon="mdi:state-machine",
        value_fn=lambda rt: STATES[rt.language][_system_state(rt.status.data)],
    ),
    EmsEspSensorDescription(
        key="firmware_installed",
        icon="mdi:chip",
        value_fn=lambda rt: rt.status.data.get("emsesp_version"),
    ),
    EmsEspSensorDescription(
        key="firmware_latest",
        icon="mdi:tag-arrow-up",
        from_releases=True,
        value_fn=lambda rt: _release(rt, CHANNEL_STABLE),
    ),
    EmsEspSensorDescription(
        key="manager_version",
        icon="mdi:puzzle-check",
        value_fn=lambda rt: MANAGER_VERSION,
    ),
    EmsEspSensorDescription(
        key="update_progress",
        icon="mdi:progress-download",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda rt: _progress(rt),
    ),
    EmsEspSensorDescription(
        key="bus_log",
        icon="mdi:console-line",
        value_fn=lambda rt: (rt.log.last_line[-250:] if rt.log and rt.log.last_line else None),
        attr_fn=lambda rt: {
            "lines": list(rt.log.lines) if rt.log else [],
            "connected": bool(rt.log and rt.log.connected),
        },
    ),
    EmsEspSensorDescription(
        key="hardware",
        icon="mdi:memory",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda rt: platform_string(rt.status.data),
    ),
    EmsEspSensorDescription(
        key="bus_status",
        icon="mdi:swap-horizontal",
        value_fn=lambda rt: STATES[rt.language].get(_bus_state(rt.status.data) or "", None),
    ),
    EmsEspSensorDescription(
        key="num_devices",
        icon="mdi:devices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda rt: rt.status.data.get("num_devices"),
    ),
    EmsEspSensorDescription(
        key="plant",
        icon="mdi:heat-pump-outline",
        value_fn=lambda rt: plant_summary((rt.extra.get("scan") or {}).get("devices"), rt.language),
        attr_fn=lambda rt: {
            "devices": [
                {k: d.get(k) for k in ("type", "name", "brand", "deviceID", "productID", "version")}
                for d in ((rt.extra.get("scan") or {}).get("devices") or [])
            ],
        },
    ),
    EmsEspSensorDescription(
        key="network",
        icon="mdi:lan",
        value_fn=lambda rt: STATES[rt.language].get(network_type(rt.status.data) or "", None),
    ),
    EmsEspSensorDescription(
        key="wifi_rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        # on LAN there is no Wi-Fi signal (EMS-ESP sends 0) -> no value
        value_fn=lambda rt: (rt.status.data.get("wifi_rssi") or None)
        if network_type(rt.status.data) != "lan" else None,
    ),
    EmsEspSensorDescription(
        key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda rt: rt.status.data.get("uptime"),
    ),
    EmsEspSensorDescription(
        key="free_heap",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.KILOBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda rt: rt.status.data.get("free_heap"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EmsEspConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    from .extra_values import async_setup_platform_extra

    await async_setup_platform_extra(hass, entry, "sensor", async_add_entities)
    async_add_entities(EmsEspSensor(entry, d) for d in SENSORS)


class EmsEspSensor(EmsEspEntity, CoordinatorEntity, SensorEntity):
    """A status sensor."""

    entity_description: EmsEspSensorDescription
    # big attributes that change all the time – keep them out of the recorder database
    _unrecorded_attributes = frozenset({"lines", "devices"})

    def __init__(self, entry: EmsEspConfigEntry, description: EmsEspSensorDescription) -> None:
        rt = entry.runtime_data
        CoordinatorEntity.__init__(
            self, rt.releases if description.from_releases else rt.status
        )
        EmsEspEntity.__init__(self, entry, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # values that depend on both coordinators (channel/latest, progress)
        other = self._runtime.status if self.entity_description.from_releases else self._runtime.releases
        self.async_on_remove(other.async_add_listener(self.async_write_ha_state))
        if self.entity_description.key == "bus_log":
            listeners = self._runtime.log_listeners
            listeners.append(self.async_write_ha_state)
            self.async_on_remove(lambda: listeners.remove(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        if self.entity_description.from_releases:
            return self.native_value is not None
        if self.entity_description.key in ("update_progress", "bus_log"):
            return True
        return self._runtime.status.last_update_success

    @property
    def icon(self) -> str | None:
        if self.entity_description.key == "network":
            kind = network_type(self._runtime.status.data or {})
            return {"lan": "mdi:ethernet", "wifi": "mdi:wifi"}.get(kind or "", "mdi:lan")
        return super().icon

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attr_fn is None:
            return None
        return self.entity_description.attr_fn(self._runtime)

    @property
    def native_value(self) -> Any:
        try:
            return self.entity_description.value_fn(self._runtime)
        except (AttributeError, TypeError, ValueError):
            return None
