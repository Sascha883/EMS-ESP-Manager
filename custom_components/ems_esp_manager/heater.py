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

import asyncio
from collections import deque
from datetime import timedelta
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import VENDOR_HEATER as VENDOR_HEATER  # v1.24.0: eigener Speicherblock (mgr.vdata)

_LOGGER = logging.getLogger(__name__)
_DATA = "ems_esp_manager_heater"

TICK = 5            # s – Grundtakt; geregelt wird alle cfg["interval"] Sekunden
STALE_MEAS = 60     # s – ohne Messwerte gilt der Heizstab als nicht erreichbar
STALE_SURPLUS = 300  # s – älterer Überschusswert wird nicht verwendet
DEVICE_MAX = 3000   # W – Grenze laut Einstellungsliste (heating.target_power 0–3000)

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "topic": "",               # Base-Topic des Geräts (Standard: Seriennummer)
    "host": "",                # IP/Hostname der Web-Konfiguration (Startseite + /expert)
    "log_enabled": False,      # Heizstab-Log nur bei Bedarf aufzeichnen
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
        self._stopped = False
        self._tick_lock = asyncio.Lock()
        self._check_task = None
        self._retry_unsub = None
        self._reply_waiters: list = []      # (Bedingung, Future) – warten auf settings/reply
        self.web_fail_t = 0.0               # letzte fehlgeschlagene Web-Abfrage (dann gleich MQTT nehmen)
        self.last_check: dict[str, Any] | None = None
        self.web_scan: dict[str, Any] | None = None    # letztes Ergebnis der Web-Suche
        self._web_task = None
        # Heizstab-Log: gesendete Befehle, Antworten, Verbindungsprüfungen (nur im Speicher)
        self.log: deque[dict[str, Any]] = deque(maxlen=1500)
        self._log_seq = 0
        self._conn_state: dict[str, Any] = {"mqtt": None, "web": None}
        self._last_web_check = 0.0
        self._last_meas_log = 0.0
        self._last_status: dict[str, Any] = {}

    # ------------------------------------------------------------- storage
    @property
    def cfg(self) -> dict[str, Any]:
        return {**DEFAULTS, **(self.mgr.vdata(VENDOR_HEATER).get("heater") or {})}

    @property
    def st(self) -> dict[str, Any]:
        return self.mgr.vdata(VENDOR_HEATER).setdefault("heater_state", {})

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
            elif k == "host":
                h = clean_host(v)
                if h is not None:
                    out[k] = h
            else:
                out[k] = str(v or "").strip()
        if out.get("control") not in (None, "off", "manual", "pv"):
            out.pop("control")
        return out

    async def async_save_cfg(self, cfg: dict[str, Any]) -> None:
        if self._stopped:
            return
        old = self.cfg
        new = {**old, **self._clean(cfg)}
        self.mgr.vdata(VENDOR_HEATER)["heater"] = new
        chg = {k: new[k] for k in new if new[k] != old.get(k) and k != "log_enabled"}
        if new.get("log_enabled") != old.get("log_enabled"):
            self._log("conn", "Aufzeichnung " + ("eingeschaltet" if new.get("log_enabled") else "ausgeschaltet"), force=True)
        if chg:
            self._log("ctrl", "Einstellung geändert: " + ", ".join(f"{k}={v}" for k, v in chg.items()))
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
        host = self.cfg.get("host") or ""
        return {
            "config": self.cfg,
            "web": {
                "host": host,
                "url": f"http://{host}/" if host else "",
                "expert_url": f"http://{host}/expert" if host else "",
                "scan": self.web_scan,
            },
            "state": dict(self.st),
            "check": self.last_check,
            "log_last": self._log_seq,
            "live": lv,
            "dev_max": self.dev_max(),
            "mqtt": self.mqtt_ok,
            "devices": [{"topic": t, **d, "age": round(now - d.get("seen", now))}
                        for t, d in sorted(self.devices.items())],
        }

    # ------------------------------------------------------------- Web-Konfiguration
    async def async_find_web(self, host: str | None = None) -> None:
        """Startseite des Heizstabs suchen (eine Adresse prüfen oder ganzes Heimnetz).

        Läuft im Hintergrund; der Fortschritt steht in ``web_scan`` (Panel fragt alle 5 s ab).
        Ein eindeutig erkannter novolto wird als ``host`` gespeichert.
        """
        import asyncio

        if self._web_task is not None and not self._web_task.done():
            return
        if host is not None:
            host = clean_host(host)
            if not host:
                self.web_scan = {"running": False, "error": "Ungültige Adresse", "hosts": [], "log": []}
                return
        scan: dict[str, Any] = {"running": True, "target": host or "Heimnetz", "hosts": [], "log": [],
                                "started": time.time()}
        self.web_scan = scan

        def log(msg: str) -> None:
            scan["log"].append(msg)
            del scan["log"][:-40]

        async def run() -> None:
            try:
                hosts = [host] if host else None
                res = await async_lan_scan(self.hass, hosts=hosts, log=log, timeout=1.5 if host else 0.7)
                scan["hosts"] = [
                    {k: d.get(k) for k in ("ip", "hostname", "title", "novolto", "expert", "ports")}
                    for d in res if host or d.get("novolto") or 80 in d.get("ports", [])
                ][:30]
                nov = [d for d in res if d.get("novolto")]
                if host and res and 80 in res[0].get("ports", []) and not nov:
                    log(f"{host}: Webseite erreichbar, aber nicht sicher als novolto erkannt – trotzdem übernommen")
                    nov = res[:1]
                if len(nov) == 1:
                    await self.async_save_cfg({"host": nov[0]["ip"]})
                    scan["selected"] = nov[0]["ip"]
                    log(f"Heizstab-Webseite: http://{nov[0]['ip']}/ – gespeichert")
                elif not nov:
                    log("Kein novolto gefunden" + ("" if host else " – Adresse von Hand eingeben (Router/DHCP-Liste)"))
                else:
                    log(f"{len(nov)} novolto gefunden – bitte einen auswählen")
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("Heizstab-Web-Suche")
                scan["error"] = f"{type(err).__name__}: {err}"
            finally:
                scan["running"] = False

        self._web_task = self.hass.async_create_task(run(), "ems_esp_manager heater web scan")
        await asyncio.sleep(0)

    # ------------------------------------------------------------- Heizstab-Log
    def _log(self, cat: str, msg: str, level: str = "INFO", detail: Any = None, force: bool = False) -> None:
        """cat: send | recv | conn | web | ctrl – aufgezeichnet nur bei eingeschaltetem Log."""
        if not force and not self.cfg.get("log_enabled"):
            if level in ("WARNING", "ERROR"):
                _LOGGER.debug("Heizstab [%s] %s", cat, msg)
            return
        self._log_seq += 1
        e: dict[str, Any] = {"id": self._log_seq, "ts": time.time(), "cat": cat, "level": level, "msg": msg}
        if detail is not None:
            try:
                e["detail"] = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)[:2000]
            except (TypeError, ValueError):
                e["detail"] = str(detail)[:2000]
        self.log.append(e)
        _LOGGER.log(logging.WARNING if level in ("WARNING", "ERROR") else logging.DEBUG, "Heizstab [%s] %s", cat, msg)

    def _log_raw(self, sub: str, msg) -> None:
        if not self.cfg.get("log_enabled"):
            return
        if sub in ("settings/get", "settings/set"):   # eigenes Echo – steht schon als „Gesendet“ im Log
            return
        if sub == "info":                              # Legacy-Messwerte alle 5 s – nur 1× pro Minute
            now = time.time()
            if now - getattr(self, "_last_info_log", 0) < 60:
                return
            self._last_info_log = now
        p = msg.payload
        if isinstance(p, (bytes, bytearray)):
            p = p.decode("utf-8", "replace")
        data = self._json(p)
        self._log("recv", f"← {sub} (unbekanntes Topic{', retained' if getattr(msg, 'retain', False) else ''})", "INFO",
                  detail=_mask(data) if isinstance(data, dict) else str(p)[:2000])

    def log_entries(self, cat: str | None = None, limit: int = 500, since: int = 0) -> list[dict[str, Any]]:
        out = [e for e in self.log if e["id"] > since and (not cat or cat == "all" or e["cat"] == cat)]
        return out[-limit:]

    def _set_conn(self, key: str, ok: bool, msg: str) -> None:
        """Verbindungszustand merken; ins Log nur bei Änderung."""
        if self._conn_state.get(key) is ok:
            return
        self._conn_state[key] = ok
        self._log("conn", msg, "INFO" if ok else "WARNING")

    async def async_check(self, manual: bool = True) -> dict[str, Any]:
        """Verbindung prüfen: MQTT-Broker, Messwerte vom Heizstab, Web-Oberfläche/Einstellungs-API."""
        res: dict[str, Any] = {"ts": time.time()}
        cfg, lv, now = self.cfg, self.live, time.time()
        # MQTT
        res["mqtt_client"] = self.mqtt_ok
        age = round(now - lv["t_meas"]) if lv.get("t_meas") else None
        res["meas_age"] = age
        res["topic"] = cfg["topic"]
        if not self.mqtt_ok:
            mq = (False, "MQTT: Home Assistant hat keine Verbindung zum Broker")
        elif not cfg["topic"]:
            mq = (False, "MQTT: kein Base-Topic gewählt")
        elif age is None:
            mq = (False, f"MQTT: noch keine Messwerte von {cfg['topic']} empfangen")
        elif lv.get("online") is False:
            mq = (False, "MQTT: Heizstab hat sich abgemeldet (offline) – z. B. nach Tastendruck: dann ist seine Weboberfläche erreichbar")
        elif age > STALE_MEAS:
            mq = (False, f"MQTT: letzte Messwerte vor {age} s – Heizstab sendet nicht mehr")
        else:
            mq = (True, f"MQTT: Heizstab sendet (letzte Messwerte vor {age} s)")
        res["mqtt"] = mq[0]
        if manual:
            self._log("conn", mq[1], "INFO" if mq[0] else "WARNING")
        else:
            self._set_conn("mqtt", mq[0], mq[1])
        # Web
        host = cfg.get("host") or ""
        res["host"] = host
        if host:
            t0 = time.monotonic()
            try:
                data = await async_web_get_settings(self.hass, host, timeout=5)
                ms = round((time.monotonic() - t0) * 1000)
                bu = (((data.get("mqtt") or {}).get("broker_uri") or {}).get("value")) or ""
                bu = re.sub(r"//[^@/]*@", "//***@", bu)   # evtl. Benutzer:Passwort in der URI
                res.update(web=True, web_ms=ms, broker_uri=bu)
                txt = f"Web: {host} antwortet ({ms} ms), Broker im Gerät: {bu or '–'}"
                if manual:
                    self._log("conn", txt)
                    if "novolto.de" in bu:
                        self._log("conn", "Heizstab ist mit der novolto-Cloud verbunden – Home Assistant bekommt keine MQTT-Daten. "
                                  "„MQTT auf Home Assistant umstellen“ verwenden.", "WARNING")
                else:
                    self._set_conn("web", True, txt)
            except Exception as err:  # noqa: BLE001
                res.update(web=False, web_error=f"{type(err).__name__}: {err}")
                txt = f"Web: {host} nicht erreichbar ({type(err).__name__}: {err})"
                if manual:
                    self._log("conn", txt, "ERROR")
                else:
                    self._set_conn("web", False, txt)
        elif manual:
            self._log("conn", "Web: keine Adresse gespeichert", "WARNING")
        self.last_check = res
        return res

    # ------------------------------------------------------------- Einstellungen über MQTT
    async def _mqtt_request(self, sub: str, payload: dict, pred, timeout: float = 6.0) -> dict:
        if not self.mqtt_ok or not self.cfg["topic"]:
            raise RuntimeError("MQTT ist nicht verbunden")
        fut = asyncio.get_running_loop().create_future()
        waiter = (pred, fut)
        self._reply_waiters.append(waiter)
        try:
            if not await self._publish(sub, payload):
                raise RuntimeError("Senden über MQTT fehlgeschlagen")
            async with asyncio.timeout(timeout):
                return await fut
        except TimeoutError:
            raise RuntimeError("Heizstab antwortet nicht über MQTT (keine settings/reply)") from None
        finally:
            self._reply_waiters.remove(waiter)

    async def async_mqtt_get_settings(self) -> dict[str, Any]:
        """Alle Einstellungen (wie die Experten-Seite) über {topic}/settings/get – leere Liste = alle Werte."""
        def full(d: dict) -> bool:
            st = d.get("settings")
            return isinstance(st, dict) and "broker_uri" in (st.get("mqtt") or {})

        data = await self._mqtt_request("settings/get", {"mqtt": [], "wifi": [], "heating": []}, full)
        out = {ns: v for ns, v in data["settings"].items() if isinstance(v, dict)}
        for vals in out.values():
            for d in vals.values():
                if isinstance(d, dict) and d.get("sensitive"):
                    d["value"] = None
        return out

    async def async_mqtt_set_settings(self, patch: dict[str, dict[str, Any]]) -> None:
        body = settings_body(await self.async_mqtt_get_settings(), patch)
        if not body:
            return
        async with self._tick_lock:          # Regelung pausiert, damit ihre Antworten nicht dazwischenfunken
            data = await self._mqtt_request("settings/set", body, lambda d: True)
        if data.get("result") == "error" or data.get("errors"):
            raise RuntimeError(f"Heizstab lehnt ab: {data.get('errors') or data.get('error')}")

    # ------------------------------------------------------------- start/stop
    @callback
    def start(self) -> None:
        # v1.22.4: Heizstab-Log startet nach jedem HA-Start ausgeschaltet
        if isinstance(self.mgr.vdata(VENDOR_HEATER).get("heater"), dict) and self.mgr.vdata(VENDOR_HEATER)["heater"].get("log_enabled"):
            self.mgr.vdata(VENDOR_HEATER)["heater"]["log_enabled"] = False
            self.hass.async_create_task(self.mgr.async_save())
        self._unsub.append(async_track_time_interval(self.hass, self._tick_cb, timedelta(seconds=TICK)))
        self._stop_unsub = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_ha_stop)
        self.hass.async_create_task(self._async_start_mqtt())
        self._log("conn", "Heizstab-Steuerung gestartet")

    @callback
    def stop(self) -> None:
        if self.st.get("set_mode") == "manual" and (self.st.get("set_topic") or self.cfg["topic"]):
            self.hass.async_create_task(self._switch_off("Manager entladen", topic=self.st.get("set_topic") or None))
        self._stopped = True
        for t in (self._web_task, self._check_task):
            if t is not None and not t.done():
                t.cancel()
        if self._retry_unsub:
            self._retry_unsub()
            self._retry_unsub = None
        self.hass.async_create_task(self.mgr.async_save())   # Tagesenergie & Zustand nicht verlieren
        while self._unsub:
            self._unsub.pop()()
        if self._stop_unsub:
            self._stop_unsub()
            self._stop_unsub = None
        self._unsub_device()

    async def _on_ha_stop(self, _event) -> None:
        self._stop_unsub = None
        # Sicherheit: ohne laufende Regelung keinen Netzbezug riskieren
        if self.st.get("set_mode") == "manual" and (self.st.get("set_topic") or self.cfg["topic"]):
            await self._switch_off("Home Assistant fährt herunter", topic=self.st.get("set_topic") or None)
        await self.mgr.async_save()

    async def _async_start_mqtt(self) -> None:
        try:
            from homeassistant.components import mqtt
        except ImportError:  # pragma: no cover
            return
        if self._stopped:
            return

        def retry() -> None:
            @callback
            def _retry(_now) -> None:
                self._retry_unsub = None
                if not self._stopped:
                    self.hass.async_create_task(self._async_start_mqtt())

            if self._retry_unsub:
                self._retry_unsub()
            self._retry_unsub = async_call_later(self.hass, 60, _retry)

        if "mqtt" not in self.hass.config.components:
            retry()   # MQTT-Integration wird ggf. später geladen
            return
        subs: list = []
        try:
            if not await mqtt.async_wait_for_mqtt_client(self.hass):
                retry()
                return
            for pat in ("+/device/info", "+/heating/status"):
                subs.append(await mqtt.async_subscribe(self.hass, pat, self._on_discovery, qos=0))
            if self._stopped:              # während des Wartens entladen → Abos sofort lösen
                for u in subs:
                    u()
                return
            self._unsub.extend(subs)
            self.mqtt_ok = True
            self._log("conn", "MQTT: mit dem Broker von Home Assistant verbunden")
            await self._subscribe_device()
        except Exception as err:  # noqa: BLE001
            for u in subs:
                u()
            _LOGGER.warning("Heizstab: MQTT nicht verfügbar – neuer Versuch in 60 s", exc_info=True)
            self._log("conn", f"MQTT nicht verfügbar: {err}", "ERROR")
            retry()

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
        self._log("conn", f"MQTT: abonniert {topic}/#")
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
            self._log("send", f"→ {topic}/{sub}", detail=_mask(payload))
            return True
        except Exception as err:  # noqa: BLE001
            self.st["last"] = f"Senden fehlgeschlagen: {err}"
            self._log("send", f"→ {topic}/{sub} fehlgeschlagen: {err}", "ERROR", detail=_mask(payload))
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
                self._log_raw(sub, msg)
                return
        else:
            # unbekannte Topics (z. B. nach Tastendruck, Neustart, AP-Modus) nur bei eingeschaltetem Log mitschreiben
            self._log_raw(sub, msg)
            return
        if sub == "mqtt/status":
            if lv.get("online") != bool(data.get("online")):
                self._log("recv", f"← {sub}: {'online' if data.get('online') else 'OFFLINE'}",
                          "INFO" if data.get("online") else "WARNING", detail=data)
            lv["online"] = bool(data.get("online"))
            if data.get("mqtt_api_version"):
                v = data["mqtt_api_version"]
                lv["api"] = f"{v.get('major')}.{v.get('minor')}"
        elif sub == "device/info":
            if not lv.get("info"):
                self._log("recv", f"← {sub}: Seriennr. {data.get('serial')}, FW {data.get('firmware_revision')}", detail=data)
            lv["info"] = {"serial": data.get("serial"), "hw": data.get("hardware_revision"), "fw": data.get("firmware_revision")}
        elif sub == "heating/status":
            stat = {k: data.get(k) for k in ("mode", "state", "error", "target_temp", "target_power")}
            if stat != self._last_status:
                chg = ", ".join(f"{k}={v}" for k, v in stat.items() if self._last_status.get(k) != v)
                self._log("recv", f"← {sub}: {chg}", "WARNING" if stat.get("state") == "error" else "INFO", detail=data)
                self._last_status = dict(stat)
            lv["status"] = stat
            lv["t_status"] = now
            lv.setdefault("online", True)
        elif sub == "heating/measurements":
            el = data.get("electricity") or {}
            p = _f(el.get("power"))
            lv["meas"] = {"power": p, "voltage": _f(el.get("voltage")), "water_temp": _f(data.get("water_temp"))}
            if now - self._last_meas_log >= 60:      # Messwerte nur 1× pro Minute ins Log
                self._last_meas_log = now
                self._log("recv", f"← {sub}: {p if p is not None else '–'} W, {lv['meas']['voltage'] or '–'} V, "
                          f"Wasser {lv['meas']['water_temp'] if lv['meas']['water_temp'] is not None else '–'} °C", "DEBUG", detail=data)
            self._set_conn("mqtt", True, "MQTT: Messwerte vom Heizstab kommen an")
            self._energy(p, now)
            lv["t_meas"] = now
            lv.setdefault("online", True)
        elif sub == "device/temperature":
            lv["dtemp"] = {"mcu": _f(data.get("mcu")), "chassis": _f(data.get("chassis"))}
        elif sub == "settings/reply":
            self._log("recv", f"← {sub}: {data.get('result') or 'Antwort'}",
                      "WARNING" if data.get("result") == "error" or data.get("errors") else "INFO", detail=_mask(data))
            self._on_reply(data)
            for pred, fut in list(self._reply_waiters):
                if not fut.done():
                    try:
                        ok = pred(data)
                    except Exception:  # noqa: BLE001
                        ok = False
                    if ok:
                        fut.set_result(data)

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
            self._below_since = None
            return 0, "Betriebsart Aus"
        if not ok:
            self._below_since = None
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
        # Mindestleistung auf das Raster aufrunden – abgerundet läge sie sonst darunter
        minp = min(-(-max(cfg["min_power"], step) // step) * step, maxp)
        if avail >= minp:
            self._below_since = None
            return int(max(minp, min(avail, maxp) // step * step)), why
        cur = self.st.get("set_power") or 0
        if cur > 0 and cfg["off_delay"] > 0:
            t = self._below_since or time.time()
            self._below_since = t
            if time.time() - t < cfg["off_delay"]:
                return int(minp), why + f" – halte Mindestleistung ({cfg['off_delay']} s)"
        self._below_since = None
        return 0, why + " – zu wenig"

    async def async_tick(self) -> None:
        if self._stopped or self._tick_lock.locked():
            return
        async with self._tick_lock:
            await self._tick()

    async def _tick(self) -> None:
        cfg, st = self.cfg, self.st
        now = time.time()
        if now - self._dirty_t > 600:
            self._dirty_t = now
            await self.mgr.async_save()
        # Verbindungsabfrage im Hintergrund (jede Minute; ins Log nur bei Änderung)
        if now - self._last_web_check >= 60:
            self._last_web_check = now
            if (cfg["topic"] or cfg.get("host")) and (self._check_task is None or self._check_task.done()):
                self._check_task = self.hass.async_create_task(self.async_check(manual=False))
        # v1.22.2: Ausschalten nachholen, falls es beim Abschalten/Gerätewechsel nicht ankam
        if self.mqtt_ok and st.get("set_mode") == "manual":
            old_t = st.get("set_topic")
            if not cfg["enabled"] and (old_t or cfg["topic"]):
                await self._switch_off("Nachholen: Regelung aus", topic=old_t or cfg["topic"])
                return
            if old_t and cfg["topic"] and old_t != cfg["topic"]:
                await self._switch_off("Nachholen: anderes Gerät gewählt", topic=old_t)
        if not cfg["enabled"] or not cfg["topic"] or not self.mqtt_ok:
            return
        if now - self._last_ctrl < cfg["interval"] - 0.5:
            return
        self._last_ctrl = now
        if not self._reachable():
            st["check"] = "Heizstab nicht erreichbar (keine Messwerte über MQTT)"
            self._set_conn("mqtt", False, "MQTT: keine Messwerte vom Heizstab – Regelung pausiert")
            # Sicherheit: kommen > 3 min keine Messwerte, eingeschalteten Heizstab ausschalten
            if st.get("set_mode") == "manual" and now - (self.live.get("t_meas") or 0) > 3 * STALE_MEAS:
                await self._switch_off("keine Messwerte mehr")
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
                self._log("ctrl", f"Regelung: {'Aus' if want == 0 else f'{want} W'} – {why}")
        if st.get("set_power") != want or st.get("set_mode") != mode:
            st.update(set_power=want, set_mode=mode, controlling=True)
        if want > 0:
            st["set_topic"] = cfg["topic"]

    async def _switch_off(self, why: str, topic: str | None = None) -> None:
        if await self._publish_set({"mode": "off"}, topic):
            self.st.update(set_power=0, set_mode="off",
                           last=f"{dt_util.now().strftime('%H:%M:%S')}: Aus ({why})")
            _LOGGER.info("Heizstab ausgeschaltet (%s)", why)
            self._log("ctrl", f"Ausgeschaltet: {why}")


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


# =====================================================================
# Heizstab als eigenes Gerät („Gerät hinzufügen → Heizstab“)
# =====================================================================
def parse_device_info(topic: str, payload: Any) -> dict[str, Any] | None:
    """``<base>/device/info`` eines novolto-Heizstabs → {topic, serial, hw, fw} (sonst None)."""
    if not topic.endswith("/device/info"):
        return None
    base = topic[: -len("/device/info")]
    if not base:
        return None
    data = Heater._json(payload)
    if not isinstance(data, dict) or not data.get("serial"):
        return None
    if "hardware_revision" not in data and "firmware_revision" not in data:
        return None
    return {
        "topic": base,
        "serial": str(data.get("serial")),
        "hw": data.get("hardware_revision"),
        "fw": data.get("firmware_revision"),
    }


def _nolog(_msg: str) -> None:
    return None


_RE_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}(:\d{1,5})?$")


def clean_host(value: Any) -> str | None:
    """'http://192.168.0.17/expert' → '192.168.0.17'; '' bleibt '' (löschen); ungültig → None."""
    h = str(value or "").strip()
    h = re.sub(r"^[a-zA-Z]+://", "", h).split("/", 1)[0].strip()
    if not h:
        return ""
    return h if _RE_HOST.match(h) else None


async def async_scan(
    hass: HomeAssistant, seconds: float = 4.0, log: Callable[[str], None] = _nolog
) -> tuple[bool, dict[str, dict[str, Any]]]:
    """Heizstäbe am MQTT-Broker von Home Assistant suchen.

    novolto meldet sich per WLAN/LAN beim Broker an und sendet ``<Seriennummer>/device/info``
    (retained) und alle 5 s ``…/heating/status``. Rückgabe: (MQTT verfügbar, {topic: info}).
    """
    import asyncio

    found: dict[str, dict[str, Any]] = {}
    h = get(hass)
    if h is not None:
        for t, d in h.devices.items():
            found[t] = {"topic": t, "serial": d.get("serial") or t, "hw": d.get("hw"), "fw": d.get("fw")}
            log(f"MQTT: bereits bekannt: {t}")
    if "mqtt" not in hass.config.components:
        log("MQTT: Integration nicht eingerichtet – MQTT-Suche übersprungen")
        return False, found
    from homeassistant.components import mqtt

    log("MQTT: warte auf Verbindung zum Broker …")
    try:
        async with asyncio.timeout(15):
            ok = await mqtt.async_wait_for_mqtt_client(hass)
    except Exception as err:  # noqa: BLE001
        log(f"MQTT: Broker nicht erreichbar ({type(err).__name__})")
        return False, found
    if not ok:
        log("MQTT: Broker nicht verbunden")
        return False, found
    log(f"MQTT: verbunden – lausche {seconds:g} s auf +/device/info und +/heating/status")

    @callback
    def _info(msg) -> None:
        if (d := parse_device_info(msg.topic, msg.payload)) is not None:
            if d["topic"] not in found:
                log(f"MQTT: Heizstab meldet sich: {d['topic']} (FW {d.get('fw') or '?'})")
            found[d["topic"]] = d
        else:
            log(f"MQTT: {msg.topic} – kein novolto-Format, ignoriert")

    @callback
    def _status(msg) -> None:
        base = msg.topic[: -len("/heating/status")]
        data = Heater._json(msg.payload)
        if isinstance(data, dict) and "state" in data and "mode" in data:
            if base not in found:
                log(f"MQTT: Status von {base} empfangen")
            found.setdefault(base, {"topic": base, "serial": base, "hw": None, "fw": None})

    unsubs = []
    try:
        unsubs.append(await mqtt.async_subscribe(hass, "+/device/info", _info, qos=0))
        unsubs.append(await mqtt.async_subscribe(hass, "+/heating/status", _status, qos=0))
        await asyncio.sleep(seconds)
    finally:
        for u in unsubs:
            u()
    log(f"MQTT: fertig – {len(found)} Heizstab/Heizstäbe")
    return True, found


async def async_setup_heater_entry(hass: HomeAssistant, entry) -> bool:
    """Eigenes Heizstab-Gerät: Regelung starten, Seitenleiste „Heizstab“."""
    from .const import CONF_HEATER_HOST, CONF_HEATER_TOPIC, VENDOR_HEATER
    from .panel import async_setup_panel

    mgr = await async_setup_panel(hass, dict(entry.options), VENDOR_HEATER)
    h = async_setup(hass, mgr)
    topic = (entry.data.get(CONF_HEATER_TOPIC) or "").strip()
    if topic and not h.cfg["topic"]:
        await h.async_save_cfg({"topic": topic})
    host = (entry.data.get(CONF_HEATER_HOST) or "").strip()
    if host and not h.cfg.get("host"):
        await h.async_save_cfg({"host": host})

    async def _updated(hass_: HomeAssistant, entry_) -> None:
        await async_setup_panel(hass_, dict(entry_.options), VENDOR_HEATER)
        from .ehs import async_refresh_ehs_page

        await async_refresh_ehs_page(hass_)

    entry.async_on_unload(entry.add_update_listener(_updated))
    from .ehs import async_setup_hub

    await async_setup_hub(hass, entry)   # „System & Panel“ immer (v1.20.3)
    return True


async def async_unload_heater_entry(hass: HomeAssistant, entry) -> bool:
    from .const import VENDOR_HEATER
    from .panel import async_remove_panel

    async_remove_panel(hass, VENDOR_HEATER)
    from .ehs import async_release_hub

    async_release_hub(hass, entry)
    return True


# =====================================================================
# Heizstab im Heimnetz suchen (ohne MQTT) – Port 80 (Web-Konfiguration)
# und 502 (Modbus) im eigenen /24-Netz von Home Assistant
# =====================================================================
LAN_PORTS = (80, 502)
_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_RE_ASSET_JS = re.compile(r"""src=["'](/assets/[A-Za-z0-9_.\-]+\.js)["']""", re.I)
# Kennzeichen der novolto-Weboberfläche (Expert-Seite: Namespaces mqtt/wifi/heating)
_RE_CHUNK = re.compile(r"""["'](?:\./|/assets/|assets/)([A-Za-z0-9_\-]+-[A-Za-z0-9_\-]{6,}\.js)["']""")
_NOVOLTO_MARKERS = ("novolto", "broker_uri", "heat_msmt_ivl", "legacy_info_ivl", "temp_hyst_mode")


async def _lan_hosts(hass: HomeAssistant, log: Callable[[str], None] = _nolog) -> list[str]:
    """Alle Adressen im /24-Netz der aktiven Netzwerkkarten von Home Assistant."""
    import ipaddress

    from homeassistant.components import network

    hosts: list[str] = []
    try:
        adapters = await network.async_get_adapters(hass)
    except Exception as err:  # noqa: BLE001
        log(f"Netz: Netzwerkkarten nicht lesbar ({err})")
        adapters = []
    for ad in adapters:
        if not ad.get("enabled"):
            continue
        for ip in ad.get("ipv4") or []:
            try:
                addr = ipaddress.ip_address(ip["address"])
            except (KeyError, ValueError):
                continue
            if addr.is_loopback or addr.is_link_local or not addr.is_private:
                continue
            prefix = max(int(ip.get("network_prefix") or 24), 24)   # nie mehr als 254 Adressen
            net = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
            log(f"Netz: {ad.get('name') or '?'} {addr}/{ip.get('network_prefix')} → durchsuche {net}")
            hosts += [str(h) for h in net.hosts() if h != addr]
    return list(dict.fromkeys(hosts))


async def _port_open(host: str, port: int, timeout: float) -> bool:
    import asyncio

    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except Exception:  # noqa: BLE001
        return False
    w.close()
    try:
        await w.wait_closed()
    except Exception:  # noqa: BLE001
        pass
    return True


async def _probe_http(hass: HomeAssistant, host: str) -> dict[str, Any]:
    """Startseite, /expert und das Haupt-Skript lesen: Titel, Server-Kennung, novolto erkannt?

    Die novolto-Oberfläche ist eine Single-Page-App: die Startseite enthält kaum Text
    (``<body id="app">`` + ``/assets/index-….js``). Erkannt wird sie am Skript, in dem die
    Einstellungen der Expert-Seite stehen (broker_uri, heat_msmt_ivl, …).
    """
    import aiohttp

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    sess = async_get_clientsession(hass, verify_ssl=False)
    tmo = aiohttp.ClientTimeout(total=4, sock_connect=1.5)
    out: dict[str, Any] = {"title": "", "server": "", "novolto": False, "expert": False}

    def mark(txt: str, headers) -> None:
        low = (txt + " " + " ".join(f"{k}:{v}" for k, v in headers.items())).lower()
        hits = [m for m in _NOVOLTO_MARKERS if m in low]
        if "novolto" in hits or len(hits) >= 2:
            out["novolto"] = True

    # sicherstes Merkmal: die Einstellungs-API der Expert-Seite
    try:
        data = await async_web_get_settings(hass, host, timeout=4)
        out.update(novolto=True, expert=True, title="novolto Heizstab")
        out["fw_settings"] = sorted(data)
        return out
    except Exception:  # noqa: BLE001
        pass

    scripts: list[str] = []
    for path in ("/", "/expert"):
        try:
            async with sess.get(f"http://{host}{path}", timeout=tmo, allow_redirects=True) as r:
                txt = (await r.text(errors="replace"))[:20000]
                if path == "/":
                    m = _RE_TITLE.search(txt)
                    out["title"] = (m.group(1).strip() if m else "")[:80]
                    out["server"] = r.headers.get("Server", "")[:60]
                    scripts = _RE_ASSET_JS.findall(txt)[:3]
                    if scripts and 'id="app"' in txt and not out["title"]:
                        out["title"] = "Web-App"
                mark(txt, r.headers)
                if path == "/expert" and r.status == 200:
                    out["expert"] = True
        except Exception:  # noqa: BLE001
            continue
    seen: set[str] = set()
    queue = list(scripts)
    while queue and not out["novolto"] and len(seen) < 6:
        src = queue.pop(0)
        if src in seen:
            continue
        seen.add(src)
        try:
            async with sess.get(f"http://{host}{src}", timeout=aiohttp.ClientTimeout(total=6, sock_connect=1.5)) as r:
                if r.status != 200:
                    continue
                js = (await r.content.read(3_000_000)).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        mark(js, {})
        # nachgeladene Teile (z. B. „Expert-….js“) zuerst prüfen – dort stehen die Einstellungen
        chunks = ["/assets/" + c for c in _RE_CHUNK.findall(js)]
        chunks.sort(key=lambda c: (("xpert" not in c.lower()) and ("setting" not in c.lower()), c))
        queue += [c for c in chunks if c not in seen]
    if out["novolto"]:
        out["title"] = out["title"] if out["title"] not in ("", "Web-App") else "novolto Heizstab"
    return out


async def async_lan_scan(
    hass: HomeAssistant,
    hosts: list[str] | None = None,
    timeout: float = 0.7,
    log: Callable[[str], None] = _nolog,
    progress: Callable[[float], None] | None = None,
) -> list[dict[str, Any]]:
    """Geräte im Heimnetz mit offenem Port 80/502 finden und novolto-Heizstäbe erkennen.

    Die Web-Konfiguration (Startseite und /expert) antwortet auch im Heimnetz, sobald der
    Heizstab per WLAN/LAN verbunden ist (getestet: 192.168.0.17), nicht nur im Hotspot 192.168.2.1.
    """
    import asyncio

    try:
        async with asyncio.timeout(25):          # Dialog nie länger blockieren
            return await _lan_scan(hass, hosts, timeout, log, progress)
    except TimeoutError:
        log("Netz: Suche nach 25 s abgebrochen (Zeitlimit)")
        _LOGGER.warning("Heimnetz-Suche nach 25 s abgebrochen")
        return []


async def _lan_scan(
    hass: HomeAssistant,
    hosts: list[str] | None,
    timeout: float,
    log: Callable[[str], None] = _nolog,
    progress: Callable[[float], None] | None = None,
) -> list[dict[str, Any]]:
    import asyncio
    import socket

    hosts = hosts if hosts is not None else await _lan_hosts(hass, log)
    total = len(hosts) * len(LAN_PORTS)
    log(f"Netz: prüfe {len(hosts)} Adressen auf Port {', '.join(map(str, LAN_PORTS))} ({total} Verbindungen)")
    if not hosts:
        log("Netz: keine passende Netzwerkkarte gefunden")
    sem = asyncio.Semaphore(128)
    found: dict[str, dict[str, Any]] = {}
    done = 0
    step = 0

    async def check(host: str, port: int) -> None:
        nonlocal done, step
        async with sem:
            if await _port_open(host, port, timeout):
                found.setdefault(host, {"ip": host, "ports": []})["ports"].append(port)
                log(f"Netz: offen {host}:{port}")
        done += 1
        if progress is not None:
            progress(0.8 * done / max(total, 1))
        if total and done * 4 // total > step:
            step = done * 4 // total
            log(f"Netz: {step * 25} % geprüft – {len(found)} Geräte antworten")

    await asyncio.gather(*(check(h, p) for h in hosts for p in LAN_PORTS))
    if found:
        log(f"Netz: lese Namen und Webseiten von {len(found)} Geräten …")

    async def detail(d: dict[str, Any]) -> None:
        try:
            name = await asyncio.wait_for(
                hass.async_add_executor_job(socket.gethostbyaddr, d["ip"]), 1.0
            )
            d["hostname"] = name[0]
        except Exception:  # noqa: BLE001
            d["hostname"] = ""
        if 80 in d["ports"]:
            try:
                d.update(await asyncio.wait_for(_probe_http(hass, d["ip"]), 12))
            except TimeoutError:
                pass
        d["novolto"] = bool(d.get("novolto")) or "novolto" in d.get("hostname", "").lower()
        d["modbus"] = 502 in d["ports"]
        info = [d["hostname"] or "kein Name"]
        if d.get("title"):
            info.append(f"Titel „{d['title']}“")
        if d.get("server"):
            info.append(f"Server {d['server']}")
        if 80 in d["ports"]:
            info.append("/expert " + ("ja" if d.get("expert") else "nein"))
        if d["modbus"]:
            info.append("Modbus")
        log(f"Netz: {d['ip']}: " + " · ".join(info) + (" → NOVOLTO" if d["novolto"] else ""))

    await asyncio.gather(*(detail(d) for d in found.values()))
    def _ipkey(v: str):
        import ipaddress

        try:
            return (0, int(ipaddress.ip_address(v)))
        except ValueError:
            return (1, v)

    res = sorted(found.values(), key=lambda d: (not d["novolto"], _ipkey(d["ip"])))
    return res


# =====================================================================
# Einstellungen des Heizstabs direkt lesen/schreiben (Expert-Seite)
#   GET  http://<host>/api/unstable/settings/get
#        → {namespace: {key: {type, value, default, min, max, allowed_values, sensitive, description}}}
#   POST http://<host>/api/unstable/settings/set  {namespace: {key: value}}
#        → {"error": {...}} bei Fehlern
# Namespaces (FW 2026): mqtt, wifi, heating. Passwörter liefert das Gerät nie zurück (value null).
# =====================================================================
WEB_API_GET = "/api/unstable/settings/get"
WEB_API_SET = "/api/unstable/settings/set"


async def async_web_get_settings(hass: HomeAssistant, host: str, timeout: float = 8) -> dict[str, Any]:
    import aiohttp

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    sess = async_get_clientsession(hass, verify_ssl=False)
    async with sess.get(f"http://{host}{WEB_API_GET}", timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=3)) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        data = await r.json(content_type=None)
    if not isinstance(data, dict) or not any(isinstance(v, dict) for v in data.values()):
        raise RuntimeError("keine novolto-Einstellungen")
    # Passwörter nie weitergeben (das Gerät liefert sie ohnehin nicht)
    for ns in data.values():
        if isinstance(ns, dict):
            for d in ns.values():
                if isinstance(d, dict) and d.get("sensitive"):
                    d["value"] = None
    return data


def _coerce_setting(meta: dict[str, Any], value: Any) -> Any:
    typ = meta.get("type")
    if typ == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "ja", "an")
        return bool(value)
    if typ == "int32":
        f = _f(value)
        if f is None or f != int(f):
            raise ValueError("ganze Zahl erwartet")
        v = int(f)
        lo, hi = meta.get("min"), meta.get("max")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise ValueError(f"Bereich {lo} … {hi}")
        return v
    if typ == "enum":
        if value not in (meta.get("allowed_values") or []):
            raise ValueError("ungültige Auswahl")
        return value
    if typ == "string":
        v = str(value if value is not None else "")
        if len(v) > 256:
            raise ValueError("zu lang")
        if meta.get("sensitive") and v == "":
            raise ValueError("leer – Passwort bleibt unverändert")
        return v
    raise ValueError(f"Typ {typ} nicht unterstützt")


async def async_web_set_settings(hass: HomeAssistant, host: str, patch: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Nur bekannte Einstellungen mit gültigen Werten senden. Rückgabe: Antwort des Geräts."""
    import aiohttp

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    cur = await async_web_get_settings(hass, host)
    body = settings_body(cur, patch)
    if not body:
        return {}
    sess = async_get_clientsession(hass, verify_ssl=False)
    async with sess.post(f"http://{host}{WEB_API_SET}", json=body, timeout=aiohttp.ClientTimeout(total=10, sock_connect=3)) as r:
        if r.status != 200:
            raise RuntimeError(f"Heizstab antwortet HTTP {r.status}")
        res = await r.json(content_type=None)
    if isinstance(res, dict) and res.get("error"):
        raise RuntimeError(f"Heizstab meldet Fehler: {res['error']}")
    _LOGGER.info("Heizstab %s: Einstellungen geschrieben %s", host, _mask(body))
    return res if isinstance(res, dict) else {}


_RE_SECRET = re.compile(r"pass|pw|psk|secret|token|key", re.I)


def _mask(payload: Any) -> Any:
    """Passwörter & Co. für Log/Protokoll durch *** ersetzen."""
    if isinstance(payload, dict):
        if payload.get("sensitive") and payload.get("value") is not None:
            return {**payload, "value": "***"}
        return {k: ("***" if _RE_SECRET.search(str(k)) and not isinstance(v, (dict, list)) else _mask(v)) for k, v in payload.items()}
    return payload


def settings_body(cur: dict[str, Any], patch: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Nur bekannte Einstellungen mit gültigen Werten (Typ, Grenzen, Auswahl) – sonst ValueError."""
    body: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for ns, vals in (patch or {}).items():
        if not isinstance(vals, dict) or not isinstance(cur.get(ns), dict):
            errors[ns] = "unbekannter Bereich"
            continue
        for key, val in vals.items():
            meta = cur[ns].get(key)
            if not isinstance(meta, dict):
                errors[f"{ns}.{key}"] = "unbekannte Einstellung"
                continue
            try:
                body.setdefault(ns, {})[key] = _coerce_setting(meta, val)
            except ValueError as err:
                errors[f"{ns}.{key}"] = str(err)
    if errors:
        raise ValueError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    return body
