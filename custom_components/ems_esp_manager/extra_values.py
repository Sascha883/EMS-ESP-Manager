"""Values EMS-ESP delivers but Home Assistant has no entity for – created by the manager on request.

The panel's mapping tab offers "In Home Assistant anlegen" for values that are
"missing in HA". The manager then adds its own entity with exactly the standard
entity id the panel expects (e.g. ``sensor.boiler_selflowtemp``), reads the value
from EMS-ESP's REST API and – for number/select/switch – writes back via the API.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import re
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .api import EmsEspError
from .coordinator import EmsEspConfigEntry
from .entity import EmsEspEntity
from .entity_map import EMS_DEVICES, _flatten

_LOGGER = logging.getLogger(__name__)

_RE_ID = re.compile(r"^(sensor|binary_sensor|number|select|switch|text)\.(" + "|".join(EMS_DEVICES) + r")_(.+)$")
_RE_TAG = re.compile(r"^(hc\d+|dhw\d*|ahs\d+|hs\d+|src\d+|wwc\d+)_(.+)$")
WRITE_DOMAINS = ("number", "select", "switch")


def parse_id(std_id: str) -> tuple[str, str, str, str] | None:
    """'number.thermostat_hc1_seltemp' -> (domain, device, tag, name)."""
    m = _RE_ID.match(std_id)
    if not m:
        return None
    domain, dev, rest = m.groups()
    t = _RE_TAG.match(rest)
    tag, name = (t.group(1), t.group(2)) if t else ("", rest)
    return domain, dev, tag, name


def _obj(dev: str, tag: str, name: str) -> str:
    return f"{dev}_{tag}_{name}" if tag else f"{dev}_{name}"


class ValuesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls /api/<device>/values for the devices of created entities."""

    def __init__(self, hass: HomeAssistant, entry: EmsEspConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name="ems_esp_manager values",
                         update_interval=timedelta(seconds=30))
        self.api = entry.runtime_data.api
        self.devices: set[str] = set()

    async def _async_update_data(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for dev in sorted(self.devices):
            try:
                status, data = await self.api._request("GET", f"/api/{dev}/values")  # noqa: SLF001
            except EmsEspError:
                continue
            if status == 200 and isinstance(data, dict):
                for key, val in data.items():
                    if isinstance(val, dict):
                        for k2, v2 in val.items():
                            out[f"{dev}_{key}_{k2}"] = v2
                    else:
                        out[f"{dev}_{key}"] = val
        return out


def _store(hass: HomeAssistant):
    from .panel import _get  # panel store holds the list of created values

    return _get(hass)


def created(hass: HomeAssistant) -> dict[str, dict]:
    mgr = _store(hass)
    return dict(mgr.data.get("created", {})) if mgr else {}


@callback
def _runtime_extra(entry: EmsEspConfigEntry) -> dict[str, Any]:
    ex = entry.runtime_data.extra
    ex.setdefault("adders", {})
    if "values" not in ex:
        ex["values"] = ValuesCoordinator(entry.runtime_data.status.hass, entry)
    return ex


def _make(entry: EmsEspConfigEntry, std_id: str, meta: dict) -> Any:
    p = parse_id(std_id)
    if not p:
        return None
    domain = meta.get("domain") or p[0]
    cls = {"sensor": ExtraSensor, "number": ExtraNumber, "select": ExtraSelect, "switch": ExtraSwitch}.get(domain)
    return cls(entry, std_id, meta) if cls else None


async def async_setup_platform_extra(hass: HomeAssistant, entry: EmsEspConfigEntry, domain: str, add) -> None:
    """Called from each platform: remember the adder and add the created entities of this domain."""
    ex = _runtime_extra(entry)
    ex["adders"][domain] = add
    ents = []
    for std_id, meta in created(hass).items():
        if (meta.get("domain") or std_id.split(".")[0]) == domain and (e := _make(entry, std_id, meta)):
            ex["values"].devices.add(parse_id(std_id)[1])
            ents.append(e)
    if ents:
        add(ents)
        entry.async_create_background_task(hass, ex["values"].async_refresh(), "ems_esp_manager_values")


async def async_create(hass: HomeAssistant, entry: EmsEspConfigEntry, std_id: str, label: str, info: dict | None) -> str:
    """Create one entity; returns its entity id. info = EMS-ESP entity info (w, t, min, max, u, o)."""
    p = parse_id(std_id)
    if not p:
        raise HomeAssistantError(f"{std_id}: keine EMS-ESP-ID")
    domain = p[0]
    info = info or {}
    if domain == "binary_sensor" or domain == "text":
        domain = "sensor"
    if domain in WRITE_DOMAINS and not info.get("w"):
        domain = "sensor"   # EMS-ESP does not allow writing – show the value only
    if domain == "select" and not info.get("o"):
        domain = "sensor"
    ent_id = f"{domain}.{p[3] and _obj(p[1], p[2], p[3])}"
    reg = er.async_get(hass)
    uid = f"{entry.unique_id or entry.entry_id}_x_{std_id}"
    reg_ent = reg.async_get(ent_id)
    st = hass.states.get(ent_id)
    ours = reg_ent is not None and reg_ent.platform == "ems_esp_manager" and reg_ent.unique_id == uid
    if not ours:
        if reg_ent is not None and reg_ent.platform == "ems_esp_manager":
            reg.async_remove(ent_id)     # leftover of an entity the manager created before
        elif reg_ent is not None:
            raise HomeAssistantError(f"{ent_id} gibt es schon (Integration {reg_ent.platform})")
        elif st is not None:
            raise HomeAssistantError(f"{ent_id} gibt es schon")
    meta = {"domain": domain, "label": label[:60] if label else None,
            **{k: info[k] for k in ("min", "max", "u", "o", "t") if k in info}}
    mgr = _store(hass)
    mgr.data.setdefault("created", {})[std_id] = meta
    await mgr.async_save()
    ex = _runtime_extra(entry)
    ex["values"].devices.add(p[1])
    add = ex["adders"].get(domain)
    if add and (e := _make(entry, std_id, meta)):
        add([e])
    await ex["values"].async_refresh()
    return ent_id


async def async_remove(hass: HomeAssistant, entry: EmsEspConfigEntry, std_id: str) -> None:
    mgr = _store(hass)
    meta = mgr.data.get("created", {}).pop(std_id, None)
    await mgr.async_save()
    if meta is None:
        return
    reg = er.async_get(hass)
    uid = f"{entry.unique_id or entry.entry_id}_x_{std_id}"
    domain = meta.get("domain") or std_id.split(".")[0]
    if (eid := reg.async_get_entity_id(domain, "ems_esp_manager", uid)) is not None:
        reg.async_remove(eid)


# ---------------------------------------------------------------- entities
class _Extra(EmsEspEntity, CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry: EmsEspConfigEntry, std_id: str, meta: dict) -> None:
        coord = _runtime_extra(entry)["values"]
        CoordinatorEntity.__init__(self, coord)
        EmsEspEntity.__init__(self, entry, f"x_{std_id}")
        _, self._dev, self._tag, self._name = parse_id(std_id)
        self._key = _obj(self._dev, self._tag, self._name)
        self._meta = meta
        self._attr_translation_key = None
        self._attr_name = meta.get("label") or self._key
        self.entity_id = f"{meta.get('domain') or std_id.split('.')[0]}.{self._key}"
        self._attr_extra_state_attributes = {"ems_esp_value": f"{self._dev}/{self._tag + '/' if self._tag else ''}{self._name}",
                                             "created_by": "EMS-ESP Manager"}

    @property
    def _raw(self) -> Any:
        return (self.coordinator.data or {}).get(self._key)

    @property
    def available(self) -> bool:
        return super().available and self._raw is not None

    async def _write(self, value: Any) -> None:
        cmd = f"{self._tag}/{self._name}" if self._tag else self._name
        try:
            await self._runtime.api.command(self._dev, cmd, value)
        except EmsEspError as err:
            raise HomeAssistantError(f"EMS-ESP: {err}") from err
        await self.coordinator.async_request_refresh()


class ExtraSensor(_Extra, SensorEntity):
    def __init__(self, entry, std_id, meta) -> None:
        super().__init__(entry, std_id, meta)
        if meta.get("u") and meta.get("t") == "number":
            self._attr_native_unit_of_measurement = meta["u"]

    @property
    def native_value(self) -> Any:
        v = self._raw
        return v if not isinstance(v, (dict, list)) else str(v)


class ExtraNumber(_Extra, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, entry, std_id, meta) -> None:
        super().__init__(entry, std_id, meta)
        if isinstance(meta.get("min"), (int, float)):
            self._attr_native_min_value = meta["min"]
        if isinstance(meta.get("max"), (int, float)):
            self._attr_native_max_value = meta["max"]
        self._attr_native_step = 0.5 if meta.get("u") in ("°C", "K") else 1
        if meta.get("u"):
            self._attr_native_unit_of_measurement = meta["u"]

    @property
    def native_value(self) -> float | None:
        try:
            return float(self._raw)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self._write(int(value) if float(value).is_integer() else value)


class ExtraSelect(_Extra, SelectEntity):
    def __init__(self, entry, std_id, meta) -> None:
        super().__init__(entry, std_id, meta)
        self._attr_options = list(meta.get("o") or [])

    @property
    def current_option(self) -> str | None:
        v = self._raw
        return str(v) if v is not None and str(v) in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        await self._write(option)


class ExtraSwitch(_Extra, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        v = self._raw
        if v is None:
            return None
        return str(v).lower() in ("on", "true", "1", "an", "ein")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)
