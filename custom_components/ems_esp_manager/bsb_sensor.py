"""Sensoren des Geräts „ESP-Bus-Adapter“: erkannter Regler + Favoriten aus der Bus-Abfrage."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .bsb import SIGNAL_UPDATE, BsbAdapter, get as get_adapter
from .const import DOMAIN

_DEVICE_CLASS = {"°C": SensorDeviceClass.TEMPERATURE, "bar": SensorDeviceClass.PRESSURE, "kWh": SensorDeviceClass.ENERGY,
                 "kW": SensorDeviceClass.POWER, "W": SensorDeviceClass.POWER}


def _device(entry, a: BsbAdapter) -> DeviceInfo:
    ident = a.st.get("ident") or {}
    return DeviceInfo(
        identifiers={(DOMAIN, f"bsb_{entry.entry_id}")},
        name="ESP-Bus-Adapter",
        manufacturer=ident.get("manufacturer") or "Siemens BSB/LPB",
        model=ident.get("ident") or ident.get("known_device") or "BSB-Regler",
        sw_version=(a.st.get("adapter_info") or {}).get("version"),
    )


async def async_setup_bsb_sensors(hass, entry, async_add_entities) -> None:
    a = get_adapter(hass)
    if a is None:
        return
    await a.async_catalog()
    ents: list[SensorEntity] = [BsbControllerSensor(entry, a)]
    ents += [BsbValueSensor(entry, a, str(ln)) for ln in a.cfg["favorites"]]
    async_add_entities(ents)


class _Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, a: BsbAdapter, key: str) -> None:
        self._a = a
        self._attr_unique_id = f"bsb_{entry.entry_id}_{key}"
        self._attr_device_info = _device(entry, a)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()


class BsbControllerSensor(_Base):
    """Erkannter Regler (Geräte-Identifikation, Familie/Variante)."""

    _attr_icon = "mdi:chip"
    _attr_name = "Regler"

    def __init__(self, entry, a: BsbAdapter) -> None:
        super().__init__(entry, a, "controller")

    @property
    def native_value(self) -> str | None:
        i = self._a.st.get("ident") or {}
        return (i.get("ident") or i.get("title"))[:250] if i else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        i = self._a.st.get("ident") or {}
        scan = self._a.st.get("scan") or {}
        return {
            "adapter": self._a.title(),
            "geraetefamilie": i.get("family"),
            "geraetevariante": i.get("variant"),
            "baureihe": i.get("series"),
            "bekanntes_geraet": i.get("known_device"),
            "hersteller": i.get("manufacturer"),
            "parameter_gefunden": scan.get("found"),
            "bus_abfrage": self._a.cfg["poll_enabled"],
            "bus_mitschnitt": self._a.cfg["log_enabled"],
        }


class BsbValueSensor(_Base):
    """Ein Favorit (Parameterzeile) – Wert aus der Bus-Abfrage oder vom Bus mitgelesen."""

    def __init__(self, entry, a: BsbAdapter, line: str) -> None:
        super().__init__(entry, a, f"p{line}")
        self._line = line
        r = a._row(line)  # noqa: SLF001
        self._attr_name = f"{line} {r.get('name') or 'Parameter'}"
        unit = r.get("unit") or None
        enum = r.get("enum")
        self._numeric = bool(unit) and not enum and r.get("type") not in ("str", "dt", "err")
        if self._numeric:
            self._attr_native_unit_of_measurement = unit
            self._attr_device_class = _DEVICE_CLASS.get(unit)
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING if unit == "kWh" else SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:thermometer" if unit == "°C" else "mdi:tune-variant"

    @property
    def native_value(self) -> Any:
        r = self._a.values.get(self._line) or {}
        if not r or not r.get("valid", True):
            return None
        if self._numeric:
            v = r.get("value")
            return v if isinstance(v, (int, float)) else None
        return str(r.get("text") or "")[:250] or None

    @property
    def available(self) -> bool:
        return self._line in self._a.values

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        r = self._a.values.get(self._line) or {}
        return {"zeile": self._line, "befehl": r.get("cmd"), "rohdaten": r.get("raw"), "text": r.get("text")}
