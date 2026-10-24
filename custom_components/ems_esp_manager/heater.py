"""EMS-Regelung → Heizstab (novolto P2300(G)/P3000(G) über die lokale MQTT-API).

Der Heizstab wird an den MQTT-Broker von Home Assistant angebunden
(Expert-Seite des Geräts: mqtt.broker_uri, docs.novolto.de/guides/setup/).
Gelesen wird (relativ zum Base-Topic, Standard = Seriennummer):

    mqtt/status           {"online": bool, "mqtt_api_version": {...}}   retained/LWT
    device/info           {"serial", "hardware_revision", "firmware_revision"}
    heating/status        {"mode": off|manual, "state", "error"?, "target_temp", "target_power"}
    heating/measurements  {"electricity": {"voltage", "power"}, "water_temp"}   alle 5 s
    device/temperature    {"mcu", "chassis"}   (nur wenn mqtt.device_temp_ivl > 0)
    settings/reply        Antwort auf settings/get und settings/set

Geschrieben wird nur über ``settings/set`` mit dem Namespace ``heating``
(mode, target_power, target_temp, temp_hyst).

Betriebsarten (``control``):
    off     Heizstab aus
    manual  feste Leistung
    pv      PV-Überschuss: Leistung = Überschuss + aktuelle Heizstab-Leistung − Reserve

Die Regelung läuft in Home Assistant (unabhängig vom Panel). Einstellungen und
Zustand liegen im Panel-Speicher (``heater`` / ``heater_state``).
"""

from __future__ import annotations

from datetime import timedelta
import json
import logging
import time
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
_DATA = "ems_esp_manager_heater"

TICK = 5            # s – Grundtakt; geregelt wird alle cfg["interval"] Sekunden
STALE_MEAS = 60     # s – ohne Messwerte gilt der Heizstab als nicht erreichbar
STALE_SURPLUS = 300  # s – älterer Überschusswert wird nicht verwendet
DEVICE_MAX = 3000   # W – Grenze laut Einstellungsliste (heating.target_power 0–3000)

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "topic": "",               # Base-Topic des Geräts (Standard: Seriennummer)
    "control": "pv",           # off | manual | pv
    "manual_power": 1000,      # W
    "surplus_entity": "",      # W oder kW
    "surplus_invert": False,   # True: negativer Wert = Einspeisung (Netzbezugszähler)
    "reserve": 100,            # W, bleibt als Einspeisung übrig
    "min_power": 300,          # W, darunter aus
    "max_power": 3000,         # W, zusätzlich durch das Gerät begrenzt
    "step": 50,                # W, Schrittweite und Mindeständerung
    "interval": 10,            # s
    "off_delay": 60,           # s, so lange wird bei zu wenig Überschuss die Mindestleistung gehalten
    "target_temp": 60,         # °C, heating.target_temp (Gerät regelt selbst)
    "temp_hyst": 5,            # K, heating.temp_hyst 4–10
    "release_entity": "",      # optional: binary_sensor/switch/input_boolean muss „an“ sein
    "soc_entity": "",          # optional: Akku-Ladestand in %
    "soc_min": 80,             # %
}

_INT_KEYS = ("manual_power", "reserve", "min_power", "max_power", "step", "interval",
             "off_delay", "target_temp", "temp_hyst", "soc_min")
_LIMITS = {"manual_power": (0, DEVICE_MAX), "reserve": (0, 5000), "min_power": (0, DEVICE_MAX),
           "max_power": (100, DEVICE_MAX), "step": (10, 500), "interval": (5, 300),
           "off_delay": (0, 900), "target_temp": (20, 80), "temp_hyst": (4, 10), "soc_min": (0, 100)}

STATE_TXT = {"disabled": "Aus", "initializing": "Startet", "heating": "Heizt",
             "max_temperature_reached": "Temperatur erreicht", "error": "Störung"}


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class Heater:
    def __init__(self, hass: HomeAssistant, mgr) -> None:
        self.hass = hass
        self.mgr = mgr
        self._unsub: list = []
        self._sub_dev: list = []
        self._sub_topic: str | None = None
        self.mqtt_ok = False
        self.devices: dict[str, dict[str, Any]] = {}   # base topic -> info
        self.live: dict[str, Any] = {}
        self._last_ctrl = 0.0
        self._below_since: float | None = None
        self._hyst_sent = False
        self._dirty_t = 0.0
        self._stop_unsub = None

    # ------------------------------------------------------------- storage
    @property
    def cfg(self) -> dict[str, Any]:
        return {**DEFAULTS, **(self.mgr.data.get("heater") or {})}

    @property
    def st(self) -> dict[str, Any]:
        return self.mgr.data.setdefault("heater_state", {})

    def _clean(self, cfg: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in cfg.items():
            if k not in DEFAULTS:
                continue
            if k in _INT_KEYS:
                f = _f(v)
                if f is None:
                    continue
                lo, hi = _LIMITS[k]
                out[k] = int(_clamp(round(f), lo, hi))
            elif isinstance(DEFAULTS[k], bool):
                out[k] = bool(v)
            else:
                out[k] = str(v or "").strip()
        if out.get("control") not in (None, "off", "manual", "pv"):
            out.pop("control")
        return out

    async def async_save_cfg(self, cfg: dict[str, Any]) -> None:
        old = self.cfg
        new = {**old, **self._clean(cfg)}
        self.mgr.data["heater"] = new
        if not new["enabled"] and old["enabled"]:
            await self._switch_off("Regelung ausgeschaltet")
        if new["topic"] != old["topic"]:
            if old["topic"] and old["enabled"]:
                await self._switch_off("anderes Gerät gewählt", topic=old["topic"])
            self.live = {}
            await self._subscribe_device()
        # Temperatur/Hysterese sind Geräteeinstellungen → bei Änderung sofort schreiben
        if new["enabled"] and new["topic"] and (new["target_temp"] != old["target_temp"] or new["temp_hyst"] != old["temp_hyst"]):
            await self._publish_set({"target_temp": new["target_temp"], "temp_hyst": new["temp_hyst"]})
            self._hyst_sent = True
        await self.mgr.async_save()
        self._last_ctrl = 0
        await self.async_tick()

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        lv = dict(self.live)
        for k in ("t_meas", "t_status"):
            if lv.get(k):
                lv[k.replace("t_", "age_")] = round(now - lv[k])
        return {
            "config": self.cfg,
            "state": dict(self.st),
            "live": lv,
            "dev_max": self.dev_max(),
            "mqtt": self.mqtt_ok,
            "devices": [{"topic": t, **d, "age": round(now - d.get("seen", now))}
                        for t, d in sorted(self.devices.items())],
        }

    # ------------------------------------------------------------- start/stop
    @callback
    def start(self) -> None:
        self._unsub.append(async_track_time_interval(self.hass, self._tick_cb, timedelta(seconds=TICK)))
        self._stop_unsub = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_ha_stop)
        self.hass.async_create_task(self._async_start_mqtt())

    @callback
    def stop(self) -> None:
        if self.cfg["enabled"] and self.st.get("set_mode") == "manual":
            self.hass.async_create_task(self._switch_off("Manager entladen"))
        while self._unsub:
            self._unsub.pop()()
        if self._stop_unsub:
            self._stop_unsub()
            self._stop_unsub = None
        self._unsub_device()

    async def _on_ha_stop(self, _event) -> None:
        self._stop_unsub = None
        # Sicherheit: ohne laufende Regelung keinen Netzbezug riskieren
        if self.cfg["enabled"] and self.st.get("set_mode") == "manual":
            await self._switch_off("Home Assistant fährt herunter")

    async def _async_start_mqtt(self) -> None:
        try:
            from homeassistant.components import mqtt
        except ImportError:  # pragma: no cover
            return
        if "mqtt" not in self.hass.config.components:
            # MQTT-Integration wird ggf. später geladen
            @callback
            def _retry(_now) -> None:
                self.hass.async_create_task(self._async_start_mqtt())

            self._unsub.append(async_call_later(self.hass, 60, _retry))
            return
        try:
            if not await mqtt.async_wait_for_mqtt_client(self.hass):
                return
            for pat in ("+/device/info", "+/heating/status"):
                self._unsub.append(await mqtt.async_subscribe(self.hass, pat, self._on_discovery, qos=0))
            self.mqtt_ok = True
            await self._subscribe_device()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Heizstab: MQTT nicht verfügbar", exc_info=True)

    # ------------------------------------------------------------- MQTT
    @staticmethod
    def _json(payload: Any) -> Any:
        try:
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode()
            return json.loads(payload)
        except (ValueError, TypeError):
            return None

    @callback
    def _on_discovery(self, msg) -> None:
        base, _, sub = msg.topic.rpartition("/")
        base = base.rsplit("/", 1)[0] if sub in ("info", "status") else base
        data = self._json(msg.payload)
        if not isinstance(data, dict):
            return
        if msg.topic.endswith("/heating/status") and "state" not in data:
            return
        d = self.devices.setdefault(base, {})
        d["seen"] = time.time()
        if msg.topic.endswith("/device/info"):
            d.update(serial=data.get("serial"), hw=data.get("hardware_revision"), fw=data.get("firmware_revision"))
        else:
            d.setdefault("serial", base)

    def _unsub_device(self) -> None:
        while self._sub_dev:
            self._sub_dev.pop()()
        self._sub_topic = None

    async def _subscribe_device(self) -> None:
        topic = self.cfg["topic"]
        if not self.mqtt_ok or topic == self._sub_topic:
            return
        self._unsub_device()
        self._hyst_sent = False
        if not topic:
            return
        from homeassistant.components import mqtt

        self._sub_dev.append(await mqtt.async_subscribe(self.hass, f"{topic}/#", self._on_device, qos=0))
        self._sub_topic = topic
        await self.async_request_settings()

    async def async_request_settings(self) -> None:
        await self._publish("settings/get", {"heating": [], "mqtt": ["heat_msmt_ivl", "ha_discovery", "base_topic"]})

    async def _publish(self, sub: str, payload: dict, topic: str | None = None) -> bool:
        topic = topic or self.cfg["topic"]
        if not self.mqtt_ok or not topic:
            return False
        from homeassistant.components import mqtt

        try:
            await mqtt.async_publish(self.hass, f"{topic}/{sub}", json.dumps(payload), qos=1, retain=False)
            return True
        except Exception as err:  # noqa: BLE001
            self.st["last"] = f"Senden fehlgeschlagen: {err}"
            return False

    async def _publish_set(self, heating: dict, topic: str | None = None) -> bool:
        return await self._publish("settings/set", {"heating": heating}, topic)

    @callback
    def _on_device(self, msg) -> None:
        sub = msg.topic[len(self._sub_topic or "") + 1:]
        lv = self.live
        now = time.time()
        if sub in ("mqtt/status", "device/info", "heating/status", "heating/measurements", "device/temperature", "settings/reply"):
            data = self._json(msg.payload)
            if not isinstance(data, dict):
                return
        else:
            return
        if sub == "mqtt/status":
            lv["online"] = bool(data.get("online"))
            if data.get("mqtt_api_version"):
                v = data["mqtt_api_version"]
                lv["api"] = f"{v.get('major')}.{v.get('minor')}"
        elif sub == "device/info":
            lv["info"] = {"serial": data.get("serial"), "hw": data.get("hardware_revision"), "fw": data.get("firmware_revision")}
        elif sub == "heating/status":
            lv["status"] = {k: data.get(k) for k in ("mode", "state", "error", "target_temp", "target_power")}
            lv["t_status"] = now
            lv.setdefault("online", True)
        elif sub == "heating/measurements":
            el = data.get("electricity") or {}
            p = _f(el.get("power"))
            lv["meas"] = {"power": p, "voltage": _f(el.get("voltage")), "water_temp": _f(data.get("water_temp"))}
            self._energy(p, now)
            lv["t_meas"] = now
            lv.setdefault("online", True)
        elif sub == "device/temperature":
            lv["dtemp"] = {"mcu": _f(data.get("mcu")), "chassis": _f(data.get("chassis"))}
        elif sub == "settings/reply":
            self._on_reply(data)

    def _on_reply(self, data: dict) -> None:
        if data.get("result") == "error":
            self.st["last"] = f"Gerät meldet Fehler: {data.get('error')}"
            return
        sets = data.get("settings")
        if isinstance(sets, dict):
            heat = sets.get("heating") or {}
            mq = sets.get("mqtt") or {}
            self.live["settings"] = {
                **{k: (v or {}).get("value") for k, v in heat.items() if isinstance(v, dict)},
                "target_power_max": ((heat.get("target_power") or {}).get("max")),
                **{f"mqtt_{k}": (v or {}).get("value") for k, v in mq.items() if isinstance(v, dict)},
            }
        errs = data.get("errors")
        if errs:
            self.st["last"] = "Gerät lehnt ab: " + "; ".join(
                f"{ns}: {e if isinstance(e, str) else ', '.join(f'{k} {v}' for k, v in e.items())}" for ns, e in errs.items())

    def _energy(self, p: float | None, now: float) -> None:
        st = self.st
        day = str(dt_util.now().date())
        e = st.get("energy") or {}
        if e.get("day") != day:
            if e.get("day"):
                st["energy_yesterday"] = round(e.get("wh", 0))
            e = {"day": day, "wh": 0.0}
        t0 = self.live.get("t_meas")
        if p and p > 0 and t0:
            e["wh"] = e.get("wh", 0.0) + p * min(now - t0, 120) / 3600
        st["energy"] = e

    # ------------------------------------------------------------- helpers
    def dev_max(self) -> int:
        m = _f((self.live.get("settings") or {}).get("target_power_max"))
        return int(m) if m and m > 0 else DEVICE_MAX

    def _ent(self, entity_id: str):
        s = self.hass.states.get(entity_id) if entity_id else None
        return None if s is None or s.state in ("unavailable", "unknown") else s

    def _surplus(self, cfg: dict) -> tuple[float | None, str]:
        s = self._ent(cfg["surplus_entity"])
        if s is None:
            return None, "Überschuss-Entität fehlt/ohne Wert"
        v = _f(s.state)
        if v is None:
            return None, "Überschuss ohne Zahlenwert"
        seen = getattr(s, "last_reported", None) or s.last_updated
        if (dt_util.utcnow() - seen).total_seconds() > STALE_SURPLUS:
            return None, "Überschusswert veraltet"
        if str(s.attributes.get("unit_of_measurement", "")).lower() == "kw":
            v *= 1000
        if cfg["surplus_invert"]:
            v = -v
        return v, f"Überschuss {v:.0f} W"

    def _release(self, cfg: dict) -> tuple[bool, str]:
        if cfg["release_entity"]:
            s = self._ent(cfg["release_entity"])
            if s is None or s.state not in ("on", "true", "1", "home", "open"):
                return False, "keine Freigabe"
        if cfg["soc_entity"]:
            s = self._ent(cfg["soc_entity"])
            v = _f(s.state) if s else None
            if v is None:
                return False, "Akku-Ladestand unbekannt"
            if v < cfg["soc_min"]:
                return False, f"Akku {v:.0f} % < {cfg['soc_min']} %"
        return True, ""

    def _reachable(self) -> bool:
        lv = self.live
        return lv.get("online") is not False and bool(lv.get("t_meas")) and time.time() - lv["t_meas"] <= STALE_MEAS

    # ------------------------------------------------------------- control
    async def _tick_cb(self, _now=None) -> None:
        try:
            await self.async_tick()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Heizstab-Regelung")

    def _want(self, cfg: dict, maxp: int) -> tuple[int, str]:
        """Gewünschte Leistung in W und Begründung."""
        ok, why = self._release(cfg)
        if cfg["control"] == "off":
            return 0, "Betriebsart Aus"
        if not ok:
            return 0, why
        step = max(10, cfg["step"])
        if cfg["control"] == "manual":
            return int(_clamp(cfg["manual_power"], 0, maxp) // step * step), "feste Leistung"
        surplus, why = self._surplus(cfg)
        if surplus is None:
            self._below_since = None
            return 0, why
        now_p = ((self.live.get("meas") or {}).get("power")) or 0.0
        avail = surplus + max(0.0, now_p) - cfg["reserve"]
        why = f"{why} + Heizstab {now_p:.0f} W − Reserve {cfg['reserve']} W = {avail:.0f} W"
        minp = min(cfg["min_power"], maxp)
        if avail >= minp and avail >= step:
            self._below_since = None
            return int(min(avail, maxp) // step * step), why
        cur = self.st.get("set_power") or 0
        if cur > 0 and cfg["off_delay"] > 0:
            t = self._below_since or time.time()
            self._below_since = t
            if time.time() - t < cfg["off_delay"]:
                return int(max(minp, step) // step * step), why + f" – halte Mindestleistung ({cfg['off_delay']} s)"
        self._below_since = None
        return 0, why + " – zu wenig"

    async def async_tick(self) -> None:
        cfg, st = self.cfg, self.st
        now = time.time()
        if now - self._dirty_t > 600:
            self._dirty_t = now
            await self.mgr.async_save()
        if not cfg["enabled"] or not cfg["topic"] or not self.mqtt_ok:
            return
        if now - self._last_ctrl < cfg["interval"] - 0.5:
            return
        self._last_ctrl = now
        if not self._reachable():
            st["check"] = "Heizstab nicht erreichbar (keine Messwerte über MQTT)"
            return
        maxp = min(cfg["max_power"], self.dev_max())
        want, why = self._want(cfg, maxp)
        st["check"] = why
        status = self.live.get("status") or {}
        mode = "manual" if want > 0 else "off"
        patch: dict[str, Any] = {}
        if status.get("mode") != mode:
            patch["mode"] = mode
        if want > 0 and abs((_f(status.get("target_power")) or 0) - want) >= max(10, cfg["step"]):
            patch["target_power"] = want
        if want > 0 and status.get("target_temp") is not None and _f(status.get("target_temp")) != cfg["target_temp"]:
            patch["target_temp"] = cfg["target_temp"]
        if want > 0 and not self._hyst_sent:
            patch["temp_hyst"] = cfg["temp_hyst"]
        if patch:
            if await self._publish_set(patch):
                if "temp_hyst" in patch:
                    self._hyst_sent = True
                st["last"] = f"{dt_util.now().strftime('%H:%M:%S')}: {'Aus' if want == 0 else f'{want} W'} ({why})"
        if st.get("set_power") != want or st.get("set_mode") != mode:
            st.update(set_power=want, set_mode=mode, controlling=True)

    async def _switch_off(self, why: str, topic: str | None = None) -> None:
        if await self._publish_set({"mode": "off"}, topic):
            self.st.update(set_power=0, set_mode="off",
                           last=f"{dt_util.now().strftime('%H:%M:%S')}: Aus ({why})")
            _LOGGER.info("Heizstab ausgeschaltet (%s)", why)


def get(hass: HomeAssistant) -> Heater | None:
    return hass.data.get(_DATA)


@callback
def async_setup(hass: HomeAssistant, mgr) -> Heater:
    h = hass.data.get(_DATA)
    if h is None:
        h = Heater(hass, mgr)
        hass.data[_DATA] = h
        h.start()
    return h
