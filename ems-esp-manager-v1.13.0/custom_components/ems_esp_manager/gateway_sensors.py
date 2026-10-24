"""Built-in system sensors of the BBQKees Gateway E32 V2.2.

The E32 V2.2 measures its own core voltage (GPIO 39), supply voltage (GPIO 36)
and board temperature (1-Wire sensor on the board). EMS-ESP creates these
sensors ONLY on the very first start with factory settings and board profile
"E32V2_2" (src/core/analogsensor.cpp AnalogSensor::start(),
src/core/temperaturesensor.cpp set_internal_). A gateway that was set up with
another profile, restored from a backup or upgraded from old settings never
gets them – and "Analogsensoren" may be switched off as well.

This module switches the analog sensors on and creates the missing sensors
with exactly the values EMS-ESP itself uses, so Home Assistant receives
Core_voltage, Supply_voltage and Gateway_temperature via MQTT discovery.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .api import EmsEspError
from .coordinator import EmsEspRuntime

_LOGGER = logging.getLogger(__name__)

BOARD_V22 = "E32V2_2"

UOM_VOLTS = 23   # DeviceValueUOM::VOLTS
TYPE_ADC = 3     # AnalogType::ADC

# same values as AnalogSensor::start() in EMS-ESP 3.8.x
SYSTEM_ANALOGS: tuple[dict[str, Any], ...] = (
    {"gpio": 39, "name": "core_voltage", "factor": 0.00377136},   # divider 24k – 8,66k
    {"gpio": 36, "name": "supply_voltage", "factor": 0.017},      # divider 24k – 1,5k
)
TEMP_NAME = "gateway_temperature"


async def async_read_analog_enabled(rt: EmsEspRuntime) -> None:
    """Remember the setting 'analog_enabled' for the switch."""
    try:
        settings = await rt.api.get_settings()
    except EmsEspError:
        return
    if isinstance(settings.get("analog_enabled"), bool):
        rt.extra["analog_enabled"] = settings["analog_enabled"]


async def async_set_analog_enabled(rt: EmsEspRuntime, on: bool) -> None:
    """Switch the analog sensors of EMS-ESP on/off (no restart needed)."""
    data = await rt.api.set_settings({"analog_enabled": on})
    rt.extra["analog_enabled"] = bool(data.get("analog_enabled", on))
    if rt.log is not None:
        rt.log.note(f"Analogsensoren {'eingeschaltet' if on else 'ausgeschaltet'}")


def _is_v22(status: dict[str, Any], settings: dict[str, Any]) -> tuple[bool, str, str]:
    board = str(status.get("board") or "")          # from the eFuse of BBQKees boards
    profile = str(settings.get("board_profile") or "")
    return BOARD_V22 in (board, profile), board, profile


async def async_create_gateway_sensors(rt: EmsEspRuntime) -> list[str]:
    """Switch the analog sensors on and create the missing system sensors.

    Returns the result lines (also written to the bus log). Raises
    HomeAssistantError if the gateway has no built-in sensors.
    """
    api = rt.api
    status = await api.get_status()
    settings = await api.get_settings()
    v22, board, profile = _is_v22(status, settings)
    model = status.get("model") or board or "unbekannt"
    if not v22:
        raise HomeAssistantError(
            "Dieses Gateway hat keine eingebaute Spannungs- und Temperaturmessung "
            f"(Modell: {model}, Board-Profil: {profile or '?'}). "
            "Nur das BBQKees Gateway E32 V2.2 hat Core_voltage, Supply_voltage und Gateway_temperature."
        )

    done: list[str] = []
    if board == BOARD_V22 and profile != BOARD_V22:
        # wrong profile: Dallas / LED / system GPIOs 36+39 are not set up – fix and restart
        await api.set_settings({"board_profile": BOARD_V22, "analog_enabled": True})
        rt.extra["analog_enabled"] = True
        msg = (
            f"Board-Profil von {profile or '?'} auf E32V2_2 gestellt und Analogsensoren eingeschaltet – "
            "EMS-ESP startet neu. Danach die Taste bitte noch einmal drücken."
        )
        if rt.log is not None:
            rt.log.note(msg)
        try:
            await api.command("system", "restart")
        except EmsEspError:
            pass  # gateway drops the connection while rebooting
        return [msg]

    if not settings.get("analog_enabled"):
        await api.set_settings({"analog_enabled": True})
        rt.extra["analog_enabled"] = True
        done.append("Analogsensoren eingeschaltet")

    data = await api.get_sensor_data()
    analogs = data.get("as") or []
    used = {a.get("g"): a for a in analogs if isinstance(a, dict)}
    for spec in SYSTEM_ANALOGS:
        have = used.get(spec["gpio"])
        if have is not None:
            done.append(f"{spec['name']} (GPIO {spec['gpio']}) ist schon vorhanden ({have.get('n')})")
            continue
        await api.write_analog_sensor(
            {
                "gpio": spec["gpio"],
                "name": spec["name"],
                "factor": spec["factor"],
                "offset": 0,
                "uom": UOM_VOLTS,
                "type": TYPE_ADC,
                "deleted": False,
                "is_system": True,
            }
        )
        done.append(f"{spec['name']} (GPIO {spec['gpio']}) angelegt")

    temps = [t for t in (data.get("ts") or []) if isinstance(t, dict)]
    system_temp = next((t for t in temps if t.get("s") or t.get("n") == TEMP_NAME), None)
    if system_temp is not None:
        done.append(f"{TEMP_NAME} ist schon vorhanden ({system_temp.get('id')})")
    elif len(temps) == 1:
        t = temps[0]
        await api.write_temperature_sensor(
            {"id": t.get("id"), "name": TEMP_NAME, "offset": t.get("o") or 0, "is_system": True}
        )
        done.append(f"{TEMP_NAME} angelegt (Sensor {t.get('id')})")
    elif not temps:
        done.append(
            "Kein 1-Wire-Temperatursensor gefunden – Gateway_temperature kann nicht angelegt werden"
        )
    else:
        ids = ", ".join(str(t.get("id")) for t in temps)
        done.append(
            f"{len(temps)} Temperatursensoren gefunden ({ids}) – der eingebaute ist nicht eindeutig. "
            "Bitte in der EMS-ESP-Weboberfläche unter Sensoren den Gateway-Sensor benennen."
        )

    if rt.log is not None:
        rt.log.note("Gateway-Sensoren: " + "; ".join(done))
    return done
