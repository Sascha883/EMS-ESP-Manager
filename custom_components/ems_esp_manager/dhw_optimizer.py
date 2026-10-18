"""EMS-Regelung → Warmwasseroptimierung.

Heats the hot-water tank to a higher temperature (default 60 °C) when the sun is
expected to deliver enough PV power, so the heat pump runs on own solar power.

Once a day inside a time window, when
  * the forecast is good (PV-forecast entity in kWh ≥ threshold, or the weather
    forecast's average cloud coverage in the window ≤ threshold),
  * optionally the measured PV surplus is high enough (entity in W/kW), and
  * the tank is still below the target,
the hot-water mode is switched to "Komfort" and the comfort stop temperature is
raised to the target.  At the end of the window, or once the tank reaches the
target, the previous mode and stop temperature are written back.

Runs in Home Assistant (every 5 minutes), independent of an open panel.
Settings and state live in the panel store (``dhw_opt`` / ``dhw_opt_state``).
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
_DATA = "ems_esp_manager_dhwopt"

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "source": "pv",            # pv | weather
    "pv_entity": "",
    "pv_min": 10.0,            # kWh expected (today / remaining)
    "weather_entity": "",
    "cloud_max": 40,           # % average cloud coverage in the window
    "surplus_entity": "",      # optional, W or kW (positive = surplus / feed-in)
    "surplus_min": 1500,       # W
    "start": 11.0,             # hours (11.5 = 11:30)
    "end": 15.0,
    "target": 60,              # °C
    # resolved by the panel (user mapping / entity-id format respected)
    "mode_entity": "select.thermostat_dhw_mode",
    "comfort_option": "",
    "stop_entity": "number.boiler_dhw_comfstop",
    "temp_entity": "sensor.boiler_dhw_curtemp",
}


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hm(h: float) -> str:
    return f"{int(h):02d}:{int(round((h % 1) * 60)):02d}"


class DhwOptimizer:
    def __init__(self, hass: HomeAssistant, mgr) -> None:
        self.hass = hass
        self.mgr = mgr
        self._unsub: list = []

    # ------------------------------------------------------------- storage
    @property
    def cfg(self) -> dict[str, Any]:
        return {**DEFAULTS, **(self.mgr.data.get("dhw_opt") or {})}

    @property
    def st(self) -> dict[str, Any]:
        return self.mgr.data.setdefault("dhw_opt_state", {})

    async def async_save_cfg(self, cfg: dict[str, Any]) -> None:
        clean = {k: cfg[k] for k in DEFAULTS if k in cfg}
        self.mgr.data["dhw_opt"] = {**self.cfg, **clean}
        await self.mgr.async_save()
        await self.async_tick()

    def snapshot(self) -> dict[str, Any]:
        return {"config": self.cfg, "state": dict(self.st)}

    # ------------------------------------------------------------- timing
    @callback
    def start(self) -> None:
        self._unsub.append(async_track_time_interval(self.hass, self._tick_cb, timedelta(minutes=5)))
        self._unsub.append(async_call_later(self.hass, 60, self._tick_cb))

    @callback
    def stop(self) -> None:
        while self._unsub:
            self._unsub.pop()()

    async def _tick_cb(self, _now=None) -> None:
        try:
            await self.async_tick()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Warmwasseroptimierung")

    # ------------------------------------------------------------- checks
    def _state(self, entity_id: str) -> Any:
        s = self.hass.states.get(entity_id) if entity_id else None
        return None if s is None or s.state in ("unavailable", "unknown") else s

    async def _forecast(self, cfg: dict, now) -> tuple[bool | None, str]:
        if cfg["source"] == "weather":
            ent = cfg["weather_entity"]
            s = self._state(ent)
            if s is None:
                return None, "Wetter-Entität fehlt"
            covers: list[float] = []
            try:
                resp = await self.hass.services.async_call(
                    "weather", "get_forecasts", {"entity_id": ent, "type": "hourly"},
                    blocking=True, return_response=True)
                for fc in (resp or {}).get(ent, {}).get("forecast", []):
                    t = dt_util.parse_datetime(str(fc.get("datetime")))
                    if t is None:
                        continue
                    t = dt_util.as_local(t)
                    h = t.hour + t.minute / 60
                    if t.date() == now.date() and cfg["start"] <= h < cfg["end"] and fc.get("cloud_coverage") is not None:
                        covers.append(float(fc["cloud_coverage"]))
            except Exception:  # noqa: BLE001 – weather without hourly forecast
                _LOGGER.debug("hourly forecast not available", exc_info=True)
            if not covers and _f(s.attributes.get("cloud_coverage")) is not None:
                covers = [float(s.attributes["cloud_coverage"])]
            if not covers:
                ok = s.state in ("sunny", "clear", "partlycloudy")
                return ok, f"Wetter „{s.state}“"
            avg = sum(covers) / len(covers)
            return avg <= cfg["cloud_max"], f"Bewölkung Ø {avg:.0f} % (max. {cfg['cloud_max']} %)"
        s = self._state(cfg["pv_entity"])
        if s is None:
            return None, "PV-Prognose-Entität fehlt"
        v = _f(s.state)
        if v is None:
            return None, "PV-Prognose ohne Zahlenwert"
        if str(s.attributes.get("unit_of_measurement", "")).lower() == "wh":
            v /= 1000
        return v >= cfg["pv_min"], f"PV-Prognose {v:.1f} kWh (min. {cfg['pv_min']:g} kWh)"

    def _surplus(self, cfg: dict) -> tuple[bool, str]:
        if not cfg["surplus_entity"]:
            return True, "Überschuss nicht geprüft"
        s = self._state(cfg["surplus_entity"])
        v = _f(s.state) if s else None
        if v is None:
            return False, "Überschuss-Entität ohne Wert"
        if str(s.attributes.get("unit_of_measurement", "")).lower() == "kw":
            v *= 1000
        return v >= cfg["surplus_min"], f"Überschuss {v:.0f} W (min. {cfg['surplus_min']:g} W)"

    # ------------------------------------------------------------- actions
    async def _set(self, domain: str, entity_id: str, value: Any) -> None:
        if domain == "select":
            await self.hass.services.async_call("select", "select_option",
                                                {"entity_id": entity_id, "option": value}, blocking=True)
        else:
            await self.hass.services.async_call("number", "set_value",
                                                {"entity_id": entity_id, "value": value}, blocking=True)

    async def _activate(self, cfg: dict, why: str) -> None:
        st = self.st
        mode = self._state(cfg["mode_entity"])
        stop = self._state(cfg["stop_entity"])
        if stop is None:
            st["last"] = "Stopptemperatur-Entität fehlt – nicht gestartet"
            return
        st["prev"] = {"mode": mode.state if mode else None, "stop": _f(stop.state)}
        if mode and cfg["comfort_option"] and mode.state != cfg["comfort_option"]:
            await self._set("select", cfg["mode_entity"], cfg["comfort_option"])
        await self._set("number", cfg["stop_entity"], cfg["target"])
        st.update(active=True, since=dt_util.now().isoformat(),
                  last=f"Gestartet {dt_util.now().strftime('%H:%M')}: {why}")
        _LOGGER.info("Warmwasseroptimierung gestartet (%s)", why)

    async def _restore(self, why: str) -> None:
        st = self.st
        prev = st.get("prev") or {}
        cfg = self.cfg
        try:
            if prev.get("stop") is not None and self._state(cfg["stop_entity"]):
                await self._set("number", cfg["stop_entity"], prev["stop"])
            if prev.get("mode") and self._state(cfg["mode_entity"]):
                await self._set("select", cfg["mode_entity"], prev["mode"])
        except Exception as err:  # noqa: BLE001
            st["last"] = f"Zurückschreiben fehlgeschlagen: {err}"
            await self.mgr.async_save()
            return
        st.update(active=False, prev=None, done=str(dt_util.now().date()),
                  last=f"Beendet {dt_util.now().strftime('%H:%M')}: {why}")
        _LOGGER.info("Warmwasseroptimierung beendet (%s)", why)

    async def async_tick(self) -> None:
        cfg, st = self.cfg, self.st
        now = dt_util.now()
        h = now.hour + now.minute / 60
        in_win = cfg["start"] <= h < cfg["end"]
        temp_s = self._state(cfg["temp_entity"])
        temp = _f(temp_s.state) if temp_s else None
        changed = False

        if st.get("active"):
            if not cfg["enabled"]:
                await self._restore("Optimierung ausgeschaltet")
            elif temp is not None and temp >= cfg["target"] - 0.5:
                await self._restore(f"{temp:.1f} °C erreicht")
            elif not in_win:
                await self._restore("Zeitfenster vorbei")
            await self.mgr.async_save()
            return
        if not cfg["enabled"]:
            return
        checks: dict[str, str] = {}
        if not in_win:
            checks["window"] = f"außerhalb {_hm(cfg['start'])}–{_hm(cfg['end'])}"
        elif st.get("done") == str(now.date()):
            checks["window"] = "heute schon gelaufen"
        else:
            fc_ok, checks["forecast"] = await self._forecast(cfg, now)
            sp_ok, checks["surplus"] = self._surplus(cfg)
            hot = temp is not None and temp >= cfg["target"] - 2
            checks["temp"] = f"Speicher {temp:.1f} °C" if temp is not None else "Speichertemperatur unbekannt"
            if fc_ok and sp_ok and not hot:
                await self._activate(cfg, f"{checks['forecast']}, {checks['surplus']}")
                changed = True
            elif hot:
                checks["temp"] += " – schon warm genug"
        if st.get("checks") != checks or changed:
            st["checks"] = checks
            st["checked"] = now.isoformat()
            await self.mgr.async_save()
        else:
            st["checked"] = now.isoformat()


def get(hass: HomeAssistant) -> DhwOptimizer | None:
    return hass.data.get(_DATA)


@callback
def async_setup(hass: HomeAssistant, mgr) -> DhwOptimizer:
    opt = hass.data.get(_DATA)
    if opt is None:
        opt = DhwOptimizer(hass, mgr)
        hass.data[_DATA] = opt
        opt.start()
    return opt
