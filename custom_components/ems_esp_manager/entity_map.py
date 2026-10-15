"""Find the real Home Assistant entity for the panel's standard EMS-ESP ids.

The panel addresses entities by the EMS-ESP default ids (entity format
"single instance, short names"), e.g. ``sensor.thermostat_hc1_summermode``.
Depending on the EMS-ESP MQTT setting "Entity ID format" and on how Home
Assistant named the entities, the real entity id can look very different:

  * short names, single      unique_id thermostat_hc1_summermode
  * short names, multiple    unique_id ems-esp_thermostat_hc1_summermode
  * short names, 3.6 remap   unique_id boiler_wwseltemp   (dhw entities)
  * long names (v3.4)        unique_id thermostat_hc1_summer_mode (English name)

and the entity id itself may have been built from the device name
("number.warmepumpe_wlw_7_mb_ar_summer_mode") or renamed by the user.

The unique_id that EMS-ESP sends with the MQTT discovery always contains the
device type, the tag and the short (or English long) name, so it is used to
map every EMS-ESP entity back to the standard id.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

DEVICES = (
    "boiler", "thermostat", "mixer", "solar", "heatpump", "gateway", "switch",
    "controller", "connect", "alert", "extension", "generic", "heatsource",
    "ventilation", "water", "pool", "system", "temperaturesensor", "analogsensor",
    "scheduler", "custom",
)
_RE_DEV = re.compile(r"(?:^|_)(" + "|".join(DEVICES) + r")_(.+)$")
_RE_TAG = re.compile(r"^(hc\d+|dhw\d*|wwc\d+|ahs\d+|hs\d+|src\d+)_(.+)$")
# boiler/thermostat dhw entities in the 3.6 compatible formats ("ww" prefix or suffix)
_DHW_OLD_SUFFIX = ("nrg", "uptimecomp", "nrgconscomp", "auxelecheatnrgcons", "nrgsupp", "altopprio", "hpcircpump")

_LONG: dict[str, list[str]] | None = None


def _long_names() -> dict[str, list[str]]:
    """English long name (as used in the unique_id) -> EMS-ESP short names."""
    global _LONG  # noqa: PLW0603
    if _LONG is None:
        try:
            _LONG = json.loads((Path(__file__).parent / "ems_longnames.json").read_text("utf-8"))
        except (OSError, ValueError):
            _LOGGER.warning("ems_longnames.json missing – long entity names cannot be mapped")
            _LONG = {}
    return _LONG


def _candidates(uid: str) -> list[str]:
    """Standard object ids (device[_tag]_short) a unique_id can stand for."""
    m = _RE_DEV.search(uid.lower())
    if not m:
        return []
    device, rest = m.group(1), m.group(2)
    out = [f"{device}_{rest}"]                      # short names (single or multiple)
    tag, name = "", rest
    t = _RE_TAG.match(rest)
    if t:
        tag, name = t.group(1), t.group(2)
    prefix = f"{device}_{tag}_" if tag else f"{device}_"
    # long names: English name -> short name(s)
    for short in _long_names().get(name, []):
        out.append(prefix + short)
    # 3.6 compatible dhw names: boiler_wwseltemp / boiler_nrgww -> boiler_dhw_seltemp / boiler_dhw_nrg
    if not tag and device in ("boiler", "thermostat"):
        if rest.startswith("ww") and len(rest) > 2:
            out.append(f"{device}_dhw_{rest[2:]}")
        if rest.endswith("ww") and rest[:-2] in _DHW_OLD_SUFFIX:
            out.append(f"{device}_dhw_{rest[:-2]}")
    return out


def build_auto_map(hass: HomeAssistant) -> dict[str, str]:
    """Standard id -> real entity id for all EMS-ESP MQTT entities (only where they differ)."""
    reg = er.async_get(hass)
    found: dict[str, str] = {}
    exact: set[str] = set()
    for ent in reg.entities.values():
        if ent.platform != "mqtt" or not ent.unique_id or ent.disabled_by:
            continue
        # leftovers of an earlier entity format stay "unavailable" – never map to them
        state = hass.states.get(ent.entity_id)
        if state is None or (state.state == "unavailable" and state.attributes.get("restored")):
            continue
        domain = ent.entity_id.split(".", 1)[0]
        for i, obj in enumerate(_candidates(str(ent.unique_id))):
            key = f"{domain}.{obj}"
            if key == ent.entity_id:
                exact.add(key)
                continue
            # first candidate (exact short name) wins over derived ones
            if key not in found or i == 0:
                found[key] = ent.entity_id
    # never redirect an id that exists exactly as expected
    return {k: v for k, v in found.items() if k not in exact}


# ---------------------------------------------------------------------------
# Which values does EMS-ESP itself deliver?  (GET /api/<device>/values)
# Lets the panel tell "not delivered by this system" apart from "missing in HA".
_TAG_KEY = re.compile(r"^(hc\d+|dhw\d*|ahs\d+|hs\d+|src\d+|wwc\d+)$")
EMS_DEVICES = ("boiler", "thermostat", "mixer", "solar", "heatpump", "water")
_CACHE: dict[str, object] = {"t": 0.0, "ids": None}


def _flatten(device: str, data: dict) -> set[str]:
    out: set[str] = set()
    for key, val in data.items():
        if isinstance(val, dict) and _TAG_KEY.match(str(key)):
            out.update(f"{device}_{key}_{k}" for k in val)
        else:
            out.add(f"{device}_{key}")
    return out


async def ems_value_ids(api) -> list[str] | None:
    """Object ids (device[_tag]_short) of all values EMS-ESP currently has; None = unknown."""
    import time

    if _CACHE["ids"] is not None and time.monotonic() - float(_CACHE["t"]) < 60:
        return _CACHE["ids"]  # type: ignore[return-value]
    ids: set[str] = set()
    ok = False
    for dev in EMS_DEVICES:
        for path in (f"/api/{dev}/values", f"/api/{dev}"):
            try:
                status, data = await api._request("GET", path)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                status, data = 0, None
            if status == 200 and isinstance(data, dict) and data and "message" not in data:
                ids |= _flatten(dev, data)
                ok = True
                break
    if not ok:
        return None
    _CACHE.update(t=time.monotonic(), ids=sorted(ids))
    return _CACHE["ids"]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Check: can the values be read and written?  (GET /api/<device>/entities)
# EMS-ESP returns every entity with value, "readable" and "writeable" flags.
async def ems_entities(api) -> dict[str, dict] | None:
    """Object id (device[_tag]_short) -> {v, w, r, t, min, max, u}; None = EMS-ESP not reachable."""
    out: dict[str, dict] = {}
    ok = False
    for dev in EMS_DEVICES:
        try:
            status, data = await api._request("GET", f"/api/{dev}/entities")  # noqa: SLF001
        except Exception:  # noqa: BLE001
            continue
        if status != 200 or not isinstance(data, dict) or "message" in data:
            continue
        ok = True
        for key, info in data.items():
            if not isinstance(info, dict):
                continue
            item = {
                "w": bool(info.get("writeable")),
                "r": info.get("readable", True) is not False,
                "t": info.get("type"),
            }
            if "value" in info:
                item["v"] = info["value"]
            # language independent values: bool (true/false) and enum index
            if isinstance(info.get("bool"), bool):
                item["b"] = info["bool"]
            if isinstance(info.get("index"), int):
                item["i"] = info["index"]
            if isinstance(info.get("enum"), list):
                item["o"] = [str(x) for x in info["enum"]]
            for src, dst in (("min", "min"), ("max", "max"), ("uom", "u")):
                if src in info:
                    item[dst] = info[src]
            out[f"{dev}_{str(key).replace('.', '_')}"] = item
    return out if ok else None


# ---------------------------------------------------------------------------
# Plant profile: which EMS devices did EMS-ESP detect?  (GET /api/system/info)
_PROFILE: dict[str, object] = {"t": 0.0, "devices": None}
_PROFILE_TYPES = ("boiler", "thermostat", "mixer", "solar", "heatpump", "water", "heatsource",
                  "ventilation", "pool", "extension", "connect", "gateway", "controller", "switch", "alert")


async def ems_devices(api, force: bool = False) -> list[dict] | None:
    """Detected EMS devices [{type, name, brand, productID, deviceID, version}]; None = unknown."""
    import time

    if not force and _PROFILE["devices"] is not None and time.monotonic() - float(_PROFILE["t"]) < 600:
        return _PROFILE["devices"]  # type: ignore[return-value]
    try:
        status, data = await api._request("GET", "/api/system/info")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None
    if status != 200 or not isinstance(data, dict):
        return None
    devs = data.get("devices") or data.get("Devices") or []
    out = [
        {k: d.get(k) for k in ("type", "name", "brand", "productID", "deviceID", "version")}
        for d in devs
        if isinstance(d, dict) and str(d.get("type", "")).lower() in _PROFILE_TYPES
    ]
    _PROFILE.update(t=time.monotonic(), devices=out)
    return out


def ems_ha_entities(hass: HomeAssistant, disabled: bool = False) -> list[str]:
    """Entity ids of all EMS-ESP entities in Home Assistant (enabled, or only the disabled ones)."""
    reg = er.async_get(hass)
    return sorted(
        ent.entity_id
        for ent in reg.entities.values()
        if ent.platform == "mqtt" and ent.unique_id and bool(ent.disabled_by) == disabled
        and _RE_DEV.search(str(ent.unique_id).lower())
    )


async def ems_scan(api) -> dict:
    """Read the plant once: detected devices and all EMS-ESP entities (value, writeable …)."""
    import time

    return {
        "t": time.time(),
        "devices": await ems_devices(api, force=True),
        "entities": await ems_entities(api),
    }


_TYPE_DE = {"boiler": "Wärmeerzeuger", "thermostat": "Regler", "mixer": "Mischer", "solar": "Solar",
            "heatpump": "Wärmepumpe", "water": "Warmwasser", "heatsource": "Zuheizer", "ventilation": "Lüftung",
            "pool": "Pool", "extension": "Erweiterung", "connect": "Connect", "gateway": "Gateway",
            "controller": "Controller", "switch": "Schaltmodul", "alert": "Alarm"}
_TYPE_EN = {"boiler": "Heat source", "thermostat": "Controller", "mixer": "Mixer", "heatpump": "Heat pump",
            "water": "Hot water", "heatsource": "Aux heater", "gateway": "Gateway"}


def plant_summary(devices: list[dict] | None, language: str = "de") -> str | None:
    """Short text for the 'Detected plant' sensor, identical devices counted once (2× Gateway …)."""
    if not devices:
        return None
    labels = _TYPE_DE if language == "de" else _TYPE_EN
    parts: list[str] = []
    counts: dict[str, int] = {}
    for d in devices:
        name = str(d.get("name") or "").split(",")[0].strip()
        label = f"{labels.get(str(d.get('type')).lower(), str(d.get('type')).capitalize())} {name}".strip()
        if label not in counts:
            parts.append(label)
        counts[label] = counts.get(label, 0) + 1
    text = " · ".join(f"{counts[p]}× {p}" if counts[p] > 1 else p for p in parts)
    return text[:250]
