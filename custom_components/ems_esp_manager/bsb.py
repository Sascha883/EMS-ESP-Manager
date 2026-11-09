"""EMS-Regelung → ESP-Bus-Adapter (Siemens-Regler am BSB/LPB: Brötje, Elco, Baxi, Atlantic, …).

Eigenentwicklung Sascha Matheis Haustechnik. Zwei Adapter-Arten werden unterstützt:

* **ESPHome-Adapter** (BSB-Atom). Ab Firmware 1.2 hat der Adapter die API-Aktion ``bsb_get``
  (beliebigen Parameter abfragen) und den Schalter „BSB-Bus-Mitschnitt“ (Telegramme im ESPHome-Log).
  Der Manager abonniert das ESPHome-Log und dekodiert jedes Telegramm selbst.
* **BSB-LAN** über dessen offene JSON-Schnittstelle (/JI, /JQ, /JK, /JS).

Abläufe (im Hintergrund, Fortschritt über ``snapshot()`` für das Panel):

    Adapter suchen   → ESPHome-Geräte mit BSB-Werten bzw. Aktion bsb_get, HA-Integration „BSB-LAN“,
                       optional Heimnetz (Port 80 + /JI)
    Regler erkennen  → 6224 Geräte-Identifikation, 6225 Gerätefamilie, 6226 Gerätevariante
    Parameter-Scan   → alle Werte der eigenen Tabelle (mit Ausweich-IDs je Gerätefamilie)
    ID-Suchlauf      → ganze ID-Bereiche abfragen, Antworten landen bei „Unbekannte Werte“
    Anlernen         → unbekannte Werte benennen (Datentyp wird vorgeschlagen) → Anlagen-Tabelle
    Bus-Abfrage      → Favoriten zyklisch abfragen (von Hand einschalten), als HA-Sensoren
    Bus-Mitschnitt   → Log aller Telegramme (von Hand einschalten), Bus-Teilnehmer

Speicher (Panel-Speicher): ``bsb`` Einstellungen, ``bsb_state`` Erkennung/Scan/Unbekannte/ID-Zuordnung,
``bsb_user_params`` angelernte Werte.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from datetime import timedelta
import base64
import json
import logging
from pathlib import Path
import re
import time
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from . import bsb_proto as P, esp_ota

from .const import VENDOR_BSB as VENDOR_BSB  # v1.24.0: eigener Speicherblock (mgr.vdata)

_LOGGER = logging.getLogger(__name__)
_DATA = "ems_esp_manager_bsb"
SIGNAL_UPDATE = "ems_esp_manager_bsb_update"
ESPHOME = "esphome"
ESPHOME_NEW = "esphome_new"   # ESPHome-Gerät, noch nicht in HA eingebunden (koppeln)
BSBLAN = "bsblan"
ADAPTER_ADDR = 0x42
USER_LINE_START = 100000      # Zeilennummern für angelernte Werte
MAX_UNKNOWN = 400

DEFAULTS: dict[str, Any] = {
    "kind": "",               # esphome | bsblan
    "device_id": "",          # HA-Geräte-ID des ESPHome-Adapters
    "node": "",               # ESPHome-Name (Aktion esphome.<node>_bsb_get)
    "title": "",              # Anzeigename des Adapters
    "host": "",               # BSB-LAN: IP[:Port]
    "passkey": "",
    "username": "",
    "password": "",
    "dest": 0,                # Zieladresse (0 = Regler)
    "poll_enabled": False,    # Bus-Abfrage der Favoriten (von Hand einschalten)
    "poll_interval": 60,      # s
    "log_enabled": False,     # Bus-Mitschnitt / Log (von Hand einschalten)
    "favorites": [],          # Zeilennummern
    "brand": "",              # Hersteller für das Dashboard („Brötje“ → „Brötje EHS“); leer = automatisch
    "ota_password": "",       # Firmware-Update: Passwort aus „ota: password“ (leer = keins)
    "ota_port": esp_ota.OTA_PORT,
}
_LIMITS = {"poll_interval": (10, 3600), "dest": (0, 127), "ota_port": (1, 65535)}
_SECRET = ("password", "ota_password")
FW_KEEP = 6                   # so viele hochgeladene Firmware-Dateien aufheben
_RE_SW = re.compile(r"^\s*([^\s(]+)\s*\(ESPHome\s+([^)]+)\)")

# ESPHome-Entitäten, an denen ein ESP-Bus-Adapter erkannt wird
_RE_BSB_ENTITY = re.compile(r"ger(ae|ä)te[_ -]?(familie|identifikation|variante)|\bbsb\b|bsb[_-]", re.I)
_RE_MONITOR = re.compile(r"(bus[_ -]?mitschnitt|bsb[_ -]?monitor|bus[_ -]?monitor)", re.I)


def _short(name: str) -> str:
    """„Viessmann (nur Vitotwin 300-W)“ → „Viessmann“."""
    return str(name or "").split(" (")[0].strip()


def brand_names() -> list[str]:
    """Wählbare Hersteller (Geräte mit Siemens-BSB/LPB-Regler)."""
    return list(P.MANUFACTURERS)


def dashboard_title(hass) -> str:
    a = get(hass)
    return a.brand_label() if a is not None else "Brötje EHS"


async def _refresh_dashboard_title(hass) -> None:
    """Seitenleisten-Titel des Dashboards nach Herstellerwechsel neu setzen."""
    from .ehs import async_refresh_bsb_dashboard

    await async_refresh_bsb_dashboard(hass)


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class BsbAdapter:
    """Ein ESP-Bus-Adapter pro Home Assistant (wie der Heizstab)."""

    def __init__(self, hass: HomeAssistant, mgr) -> None:
        self.hass = hass
        self.mgr = mgr
        self.cat: P.Catalog | None = None
        self.log: deque[dict[str, Any]] = deque(maxlen=3000)
        self._log_seq = 0
        self.values: dict[str, dict[str, Any]] = {}      # Zeile → letzter Wert (vom Bus/aus Abfragen)
        self.unknown: dict[int, dict[str, Any]] = {}     # Befehls-ID → unbekannter Wert (zum Anlernen)
        self.participants: dict[int, dict[str, Any]] = {}
        self.stats: dict[str, int] = {"rx": 0, "tx": 0, "inf": 0, "err": 0, "unknown": 0}
        self.find: dict[str, Any] | None = None           # Ergebnis der Adapter-Suche
        self.job: dict[str, Any] | None = None            # laufende Erkennung/Scan/Suchlauf
        self._task: asyncio.Task | None = None
        self._find_task: asyncio.Task | None = None
        self._find_results: dict[str, dict[str, Any]] = {}
        self._waiters: list[tuple[int, asyncio.Future]] = []
        self._bus_lock = asyncio.Lock()                   # immer nur eine eigene Abfrage auf dem Bus
        self._log_unsub: Callable[[], None] | None = None
        self._log_client = None
        self._unsubs: list[Callable[[], None]] = []
        self._poll_lock = asyncio.Lock()
        self._last_poll = 0.0
        self._last_notify = 0.0
        self._monitor_by_us = False
        self._unknown_dirty = False
        self.conn: dict[str, Any] = {}
        self._stopped = False
        self.fw_job: dict[str, Any] | None = None         # laufendes/letztes Firmware-Update
        self.fw_files: list[dict[str, Any]] = []
        self._fw_uploads: dict[str, dict[str, Any]] = {}
        self._fw_task: asyncio.Task | None = None

    # ------------------------------------------------------------- Speicher
    @property
    def cfg(self) -> dict[str, Any]:
        return {**DEFAULTS, **(self.mgr.vdata(VENDOR_BSB).get("bsb") or {})}

    def brand(self) -> str:
        """Hersteller fürs Dashboard: gewählt, sonst vom Regler erkannt, sonst Brötje."""
        b = self.cfg["brand"] or ((self.st.get("ident") or {}).get("manufacturer") or "")
        return _short(b) or "Brötje"

    def brand_label(self) -> str:
        return f"{self.brand()} EHS"

    @property
    def st(self) -> dict[str, Any]:
        return self.mgr.vdata(VENDOR_BSB).setdefault("bsb_state", {})

    @property
    def user_params(self) -> list[dict[str, Any]]:
        return self.mgr.vdata(VENDOR_BSB).setdefault("bsb_user_params", [])

    async def async_save_cfg(self, patch: dict[str, Any]) -> None:
        cur = dict(self.mgr.vdata(VENDOR_BSB).get("bsb") or {})
        before = self.cfg
        for k, v in patch.items():
            if k not in DEFAULTS:
                continue
            if k in _LIMITS:
                n = _f(v)
                if n is None:
                    continue
                lo, hi = _LIMITS[k]
                v = int(max(lo, min(hi, n)))
            elif isinstance(DEFAULTS[k], bool):
                v = bool(v)
            elif k == "brand":
                v = str(v or "").strip()
                if v and v not in brand_names():
                    continue
            elif k == "favorites":
                v = sorted({_key(x) for x in (v or [])}, key=lambda s: _f(s) or 0)[:60]
            else:
                v = str(v or "").strip()
            cur[k] = v
        self.mgr.vdata(VENDOR_BSB)["bsb"] = cur
        await self.mgr.async_save()
        after = self.cfg
        if (before["kind"], before["device_id"], before["host"]) != (after["kind"], after["device_id"], after["host"]):
            self._unsubscribe()
            self.conn = {}
            self.values.clear()
            self.unknown.clear()
            self.participants.clear()
            self.st.pop("ids", None)
            self._log("conn", f"Adapter gewählt: {self.title()}", force=True)
        for key, txt in (("log_enabled", "Bus-Mitschnitt"), ("poll_enabled", "Bus-Abfrage")):
            if before[key] != after[key]:
                self._log("conn", f"{txt} {'eingeschaltet' if after[key] else 'ausgeschaltet'}", force=True)
        if before["brand"] != after["brand"]:
            self._log("conn", f"Hersteller für das Dashboard: {self.brand_label()}", force=True)
            self.hass.async_create_task(_refresh_dashboard_title(self.hass))
        if before["favorites"] != after["favorites"]:
            _reload_entries(self.hass)
        await self._async_update_monitor()
        self._notify()

    async def async_catalog(self, reload: bool = False) -> P.Catalog:
        if self.cat is None or reload:
            user = [dict(u) for u in self.user_params]
            self.cat = await self.hass.async_add_executor_job(P.Catalog.load, user)
            if not self.unknown and self.st.get("unknown"):
                for u in self.st["unknown"]:
                    self.unknown[int(u["cmd"], 16)] = {**u, "payload": bytes.fromhex(u.get("raw", "").replace(" ", ""))}
        return self.cat

    # ------------------------------------------------------------- Log
    def _log(self, cat: str, msg: str, level: str = "INFO", detail: Any = None, force: bool = False) -> None:
        """cat: tx | rx | inf | scan | conn | http | err – aufgezeichnet nur bei eingeschaltetem Mitschnitt."""
        if not force and not self.cfg.get("log_enabled") and level not in ("WARNING", "ERROR"):
            return
        self._log_seq += 1
        e: dict[str, Any] = {"id": self._log_seq, "ts": time.time(), "cat": cat, "level": level, "msg": msg}
        if detail is not None:
            e["detail"] = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)[:1500]
        self.log.append(e)

    def log_entries(self, cat: str | None = None, limit: int = 500, since: int = 0) -> list[dict[str, Any]]:
        out = [e for e in self.log if e["id"] > since and (not cat or cat == "all" or e["cat"] == cat)]
        return out[-limit:]

    @callback
    def _notify(self) -> None:
        self._last_notify = time.time()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    # ------------------------------------------------------------- Status
    def title(self) -> str:
        c = self.cfg
        if c["kind"] == ESPHOME:
            return f"ESPHome {c['title'] or c['node'] or c['device_id']}"
        if c["kind"] == BSBLAN:
            return f"BSB-LAN {c['host']}"
        return "kein Adapter gewählt"

    def capabilities(self) -> dict[str, Any]:
        c = self.cfg
        if c["kind"] == ESPHOME:
            entry = self._esphome_entry()
            return {
                "get": self._has_get(),
                "monitor_switch": self._monitor_switch(),
                "online": bool(entry is not None and getattr(getattr(entry, "runtime_data", None), "available", False)),
                "log_stream": self._log_unsub is not None,
                "write": "entities",
                "probe": self._has_get(),
            }
        if c["kind"] == BSBLAN:
            return {"get": True, "monitor_switch": None, "online": self.conn.get("ok"), "log_stream": False, "write": "bsblan", "probe": False}
        return {"get": False, "online": False, "write": None, "probe": False}

    def ha_entry_info(self) -> dict[str, Any] | None:
        """Zustand des ESPHome-Eintrags in HA (für „Koppeln“ im Panel)."""
        if self.cfg.get("kind") != ESPHOME:
            return None
        e = self._esphome_entry()
        if e is None:
            return {"exists": False}
        return {"exists": True, "state": getattr(e.state, "value", str(e.state)), "host": e.data.get("host") or "",
                "has_key": bool(e.data.get("noise_psk")), "reauth": _reauth_pending(self.hass, e.entry_id),
                "online": bool(getattr(getattr(e, "runtime_data", None), "available", False))}

    def snapshot(self, full: bool = False) -> dict[str, Any]:
        now = time.time()
        st = self.st
        scan = st.get("scan") or {}
        out: dict[str, Any] = {
            "config": {k: v for k, v in self.cfg.items() if k not in _SECRET},
            "title": self.title(),
            "caps": self.capabilities(),
            "conn": self.conn,
            "ident": st.get("ident"),
            "job": self.job or st.get("last_job"),
            "find": self.find,
            "scan_info": {k: scan.get(k) for k in ("t", "scope", "total", "found", "empty", "errors", "duration", "kind")} if scan else None,
            "favorites": [{**self._row(ln), "line": ln} for ln in self.cfg["favorites"]],
            "participants": [
                {"addr": a, "name": P.addr_name(a), "count": d["count"], "age": round(now - d["last"]), "cmds": sorted(d["cmds"])}
                for a, d in sorted(self.participants.items())
            ],
            "stats": dict(self.stats),
            "unknown_count": len(self.unknown),
            "adapter": self.adapter_info(),
            "learned_count": len(self.user_params),
            "log_last": self._log_seq,
            "version": _manager_version(),
            "manufacturers": P.MANUFACTURERS,
            "brand": self.brand(),
            "brand_auto": not self.cfg["brand"],
            "brand_label": self.brand_label(),
            "brands": brand_names(),
            "not_supported": P.NOT_SUPPORTED,
            "probe_presets": [[n, f"{pre:04X}", f"{a:04X}", f"{b:04X}"] for n, pre, a, b in P.PROBE_PRESETS],
            "codecs": [[k, (v[4] or v[5])] for k, v in P.CODECS.items()],
            "fw": self.fw_snapshot(),
            "ha_entry": self.ha_entry_info(),
        }
        if full:
            out["params"] = self.param_rows()
            out["entities"] = self.adapter_entities()
            out["unknown"] = self.unknown_rows()
        return out

    def _row(self, line: str) -> dict[str, Any]:
        r = dict(((self.st.get("scan") or {}).get("results") or {}).get(line) or {})
        if line in self.values:
            r.update(self.values[line])
        if "name" not in r and self.cat is not None:
            p = self.cat.line(float(line))
            if p:
                r.update(self.cat.info(p))
        return r

    def param_rows(self) -> list[dict[str, Any]]:
        res = ((self.st.get("scan") or {}).get("results") or {})
        keys = set(res) | set(self.values)
        rows = [{**self._row(k), "line": k} for k in keys]
        rows.sort(key=lambda r: _f(r["line"]) or 0)
        return rows

    def unknown_rows(self) -> list[dict[str, Any]]:
        rows = sorted(self.unknown.values(), key=lambda u: (-int(u.get("found", False)), -u.get("count", 0)))[:MAX_UNKNOWN]
        return [{k: v for k, v in u.items() if k != "payload"} for u in rows]

    # ------------------------------------------------------------- ESPHome
    def _esphome_entry(self):
        dev_id = self.cfg["device_id"]
        if not dev_id:
            return None
        dev = dr.async_get(self.hass).async_get(dev_id)
        if dev is None:
            return None
        for eid in dev.config_entries:
            e = self.hass.config_entries.async_get_entry(eid)
            if e is not None and e.domain == ESPHOME:
                return e
        return None

    def _node(self) -> str:
        e = self._esphome_entry()
        name = (e.data.get("device_name") if e else None) or self.cfg["node"]
        return str(name or "").replace("-", "_")

    def _has_get(self) -> bool:
        node = self._node()
        return bool(node) and self.hass.services.has_service(ESPHOME, f"{node}_bsb_get")

    def _monitor_switch(self) -> str | None:
        e = self._esphome_entry()
        if e is None:
            return None
        for ent in er.async_entries_for_config_entry(er.async_get(self.hass), e.entry_id):
            if ent.domain == "switch" and _RE_MONITOR.search(f"{ent.original_name or ''} {ent.entity_id} {ent.unique_id}"):
                return ent.entity_id
        return None

    def adapter_info(self) -> dict[str, Any]:
        """Verbindungsdaten des Adapters (für „Verbindung“ wie bei EHS Sentinel)."""
        c = self.cfg
        if c["kind"] == BSBLAN:
            ai = self.st.get("adapter_info") or {}
            host = c["host"]
            return {"kind": BSBLAN, "host": host, "state": "BSB-LAN", "available": self.conn.get("ok"),
                    "sw": ai.get("version"), "model": ai.get("hardware") or "BSB-LAN", "bus": ai.get("bus"),
                    "web": f"http://{host}/", "ha_url": "/config/integrations/integration/bsblan"}
        e = self._esphome_entry()
        if e is None:
            return {"kind": c["kind"] or None}
        dev = dr.async_get(self.hass).async_get(c["device_id"]) if c["device_id"] else None
        rt = getattr(e, "runtime_data", None)
        host, port = e.data.get("host"), e.data.get("port")
        return {
            "kind": ESPHOME, "entry_id": e.entry_id, "host": host, "port": port, "state": str(getattr(e.state, "value", e.state)),
            "available": bool(getattr(rt, "available", False)), "name": e.data.get("device_name"),
            "sw": dev.sw_version if dev else None, "model": dev.model if dev else None, "hw": dev.hw_version if dev else None,
            "web": dev.configuration_url if dev and dev.configuration_url else None,
            "ha_url": f"/config/devices/device/{c['device_id']}" if c["device_id"] else "/config/integrations/integration/esphome",
        }

    async def async_reconnect(self) -> None:
        """ESPHome-Gerät neu laden bzw. BSB-LAN neu abfragen; danach Log neu abonnieren."""
        c = self.cfg
        if c["kind"] == BSBLAN:
            await self.bsblan("JI")
            return
        e = self._esphome_entry()
        if e is None:
            raise RuntimeError("ESPHome-Gerät nicht gefunden")
        self._unsubscribe()
        self._log("conn", "ESPHome-Gerät wird neu verbunden", force=True)
        await self.hass.config_entries.async_reload(e.entry_id)
        await asyncio.sleep(3)
        await self._async_update_monitor()

    def adapter_entities(self) -> list[dict[str, Any]]:
        """Entitäten des ESPHome-Adapters (Werte, die die Firmware fest abfragt)."""
        e = self._esphome_entry()
        if e is None:
            return []
        out = []
        for ent in er.async_entries_for_config_entry(er.async_get(self.hass), e.entry_id):
            if ent.disabled_by or ent.domain not in ("sensor", "number", "select", "switch", "binary_sensor", "text_sensor", "button"):
                continue
            s = self.hass.states.get(ent.entity_id)
            a = dict(s.attributes) if s else {}
            out.append({
                "entity_id": ent.entity_id, "domain": ent.domain,
                "name": a.get("friendly_name") or ent.original_name or ent.entity_id,
                "state": s.state if s else "unavailable", "unit": a.get("unit_of_measurement"),
                "options": a.get("options"), "min": a.get("min"), "max": a.get("max"), "step": a.get("step"),
                "category": str(ent.entity_category or ""),
            })
        out.sort(key=lambda x: (x["category"] != "", x["name"]))
        return out

    def _monitor_needed(self) -> bool:
        c = self.cfg
        return c["kind"] == ESPHOME and (c["log_enabled"] or c["poll_enabled"] or self.job is not None)

    async def _async_update_monitor(self) -> None:
        """Log-Abo und Mitschnitt-Schalter nach Bedarf ein-/ausschalten."""
        if self._stopped:
            return
        if self._monitor_needed():
            await self._async_monitor_switch(True)
            self._subscribe()
        else:
            if self._monitor_by_us:
                await self._async_monitor_switch(False)
            self._unsubscribe()

    async def _async_monitor_switch(self, on: bool) -> None:
        ent = self._monitor_switch()
        if not ent:
            return
        s = self.hass.states.get(ent)
        if s is None or s.state == ("on" if on else "off"):
            return
        try:
            await self.hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": ent}, blocking=True)
            self._monitor_by_us = on
            self._log("conn", f"{ent}: Bus-Mitschnitt im Adapter {'ein' if on else 'aus'}geschaltet", force=True)
        except Exception as err:  # noqa: BLE001
            self._log("err", f"{ent} nicht schaltbar: {err}", "WARNING")

    def _client(self):
        e = self._esphome_entry()
        rt = getattr(e, "runtime_data", None) if e else None
        if rt is None or not getattr(rt, "available", False):
            return None
        return getattr(rt, "client", None)

    @callback
    def _subscribe(self) -> None:
        client = self._client()
        if client is None:
            if self._log_unsub is not None:
                self._unsubscribe()
            self.conn = {"ok": False, "msg": "ESPHome-Adapter nicht verbunden"}
            return
        if self._log_unsub is not None and self._log_client is client:
            return
        self._unsubscribe()
        try:
            from aioesphomeapi import LogLevel

            self._log_unsub = client.subscribe_logs(self._on_log, LogLevel.LOG_LEVEL_DEBUG)
            self._log_client = client
            self.conn = {"ok": True, "msg": "ESPHome-Log abonniert", "t": time.time()}
            self._log("conn", f"ESPHome-Log von {self._node()} abonniert (Stufe DEBUG)", force=True)
        except Exception as err:  # noqa: BLE001
            self.conn = {"ok": False, "msg": f"Log-Abo fehlgeschlagen: {err}"}
            self._log("err", f"ESPHome-Log nicht abonnierbar: {type(err).__name__}: {err}", "ERROR")

    @callback
    def _unsubscribe(self) -> None:
        if self._log_unsub is not None:
            try:
                self._log_unsub()
            except Exception:  # noqa: BLE001
                pass
            self._log("conn", "ESPHome-Log abbestellt", force=True)
        self._log_unsub = None
        self._log_client = None

    @callback
    def _on_log(self, msg) -> None:
        raw = getattr(msg, "message", b"")
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        t = P.parse_esphome_log(line)
        if t is None:
            low = line.lower()
            if ("bsb" in low or "espbus" in low) and self.cfg["log_enabled"]:
                self._log("conn", P.strip_ansi(line).strip()[:300], "DEBUG")
            return
        self._on_telegram(t)

    @callback
    def _on_telegram(self, t: P.Telegram) -> None:
        cmd = t.canonical_field()
        now = time.time()
        if t.direction == "tx":
            self.stats["tx"] += 1
        else:
            self.stats["rx"] += 1
            p = self.participants.setdefault(t.src, {"count": 0, "last": now, "cmds": set()})
            p["count"] += 1
            p["last"] = now
            p["cmds"].add(t.cmd_name)
        if t.cmd == 2:
            self.stats["inf"] += 1
        if t.cmd in (5, 8):
            self.stats["err"] += 1
        # Antwort auf eine eigene Abfrage
        if t.direction == "rx" and t.cmd in (7, 5, 8) and t.dst == ADAPTER_ADDR:
            for want, fut in list(self._waiters):
                if want == cmd and not fut.done():
                    fut.set_result(t)
        texts: list[str] = []
        if t.cmd in (2, 7) and self.cat is not None:
            lines = self.cat.for_cmd(cmd)
            if not lines:
                self._remember_unknown(cmd, t, now)
            for p_ in lines:
                d = P.decode(self.cat, p_, t.payload)
                key = _key(p_["line"])
                self.values[key] = {**self.cat.info(p_), "text": d["text"], "value": d["value"], "valid": d["valid"],
                                    "raw": d["raw"], "ts": now, "src": t.src}
                texts.append(f"{key} {p_['name']} = {d['text']}")
            if texts and now - self._last_notify > 3:
                self._notify()
        if self.cfg["log_enabled"]:
            arrow = f"{t.src:02X}→{t.dst:02X}"
            cat = "tx" if t.direction == "tx" else ("inf" if t.cmd == 2 else ("err" if t.cmd in (5, 8) else "rx"))
            info = "; ".join(texts) or f"ID {cmd:08X}" + (" (unbekannt)" if t.cmd in (2, 7) else "")
            self._log(cat, f"{t.cmd_name} {arrow} {info}", "WARNING" if t.cmd in (5, 8) else "INFO",
                      detail=f"{P.addr_name(t.src)} → {P.addr_name(t.dst)} · ID {cmd:08X} · Nutzdaten {t.payload.hex(' ').upper() or '–'}")

    def _remember_unknown(self, cmd: int, t: P.Telegram, now: float, found: bool = False) -> None:
        self.stats["unknown"] += 1
        u = self.unknown.get(cmd)
        if u is None:
            if len(self.unknown) >= MAX_UNKNOWN:
                return
            u = self.unknown[cmd] = {"cmd": f"{cmd:08X}", "count": 0, "first": now}
        u.update(count=u["count"] + 1, last=now, src=t.src, type=t.cmd_name, payload=t.payload,
                 raw=t.payload.hex(" ").upper(), guess=P.guess(t.payload), found=u.get("found") or found)
        self._unknown_dirty = True

    async def esp_query(self, cmd: int, timeout: float = 2.0, retries: int = 1) -> P.Telegram | None:
        """Befehls-ID über die Firmware-Aktion bsb_get abfragen und auf die Antwort im Log warten.

        Abfragen laufen nacheinander (Sperre), damit sich Scan, Bus-Abfrage und Panel nicht auf dem Bus stören.
        """
        if not self._has_get():
            raise RuntimeError("Adapter-Firmware ohne Aktion „bsb_get“ – BSB-Atom-Firmware 1.2 aufspielen")
        async with self._bus_lock:
            self._subscribe()
            if self._log_unsub is None:
                raise RuntimeError(self.conn.get("msg") or "ESPHome-Adapter nicht verbunden")
            node = self._node()
            for attempt in range(retries + 1):
                fut: asyncio.Future = self.hass.loop.create_future()
                item = (cmd, fut)
                self._waiters.append(item)
                try:
                    await self.hass.services.async_call(
                        ESPHOME, f"{node}_bsb_get", {"field": f"{cmd:08X}", "dest": int(self.cfg["dest"])}, blocking=True
                    )
                    async with asyncio.timeout(timeout):
                        return await fut
                except TimeoutError:
                    if attempt == retries:
                        return None
                finally:
                    if item in self._waiters:
                        self._waiters.remove(item)
                await asyncio.sleep(0.3)
        return None

    async def esp_query_param(self, p: dict[str, Any], timeout: float = 2.0, retries: int = 1) -> P.Telegram | None:
        """Parameter abfragen – bei Ablehnung die Ausweich-IDs probieren; die passende ID wird gemerkt."""
        ids_map = self.st.setdefault("ids", {})
        key = _key(p["line"])
        order = list(p["ids"])
        if key in ids_map and int(ids_map[key], 16) in order:
            known = int(ids_map[key], 16)
            order.remove(known)
            order.insert(0, known)
        last = None
        for i in order:
            t = await self.esp_query(i, timeout, retries if i == order[0] else 0)
            if t is not None and t.cmd == 7:
                ids_map[key] = f"{i:08X}"
                return t
            last = t
        return last

    # ------------------------------------------------------------- BSB-LAN (JSON)
    def _bsblan_url(self, path: str) -> str:
        c = self.cfg
        pk = c["passkey"].strip("/")
        return f"http://{c['host']}/" + (f"{pk}/" if pk else "") + path.lstrip("/")

    async def bsblan(self, path: str, body: Any = None, timeout: float = 20) -> Any:
        import aiohttp
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        c = self.cfg
        if not c["host"]:
            raise RuntimeError("Keine BSB-LAN-Adresse")
        auth = aiohttp.BasicAuth(c["username"], c["password"]) if c["username"] else None
        url = self._bsblan_url(path)
        safe = url.replace(f"/{c['passkey']}/", "/***/") if c["passkey"] else url
        self._log("http", f"→ {'POST' if body is not None else 'GET'} {safe}", "DEBUG", detail=body)
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(timeout):
                req = session.get(url, auth=auth) if body is None else session.post(url, json=body, auth=auth)
                async with req as r:
                    if r.status == 401:
                        raise RuntimeError("BSB-LAN: Anmeldung nötig (Benutzer/Passwort oder Passkey)")
                    r.raise_for_status()
                    txt = await r.text()
        except Exception as err:
            self.conn = {"ok": False, "msg": f"{type(err).__name__}: {err}", "t": time.time()}
            self._log("http", f"✗ {safe}: {type(err).__name__}: {err}", "WARNING")
            raise
        self.conn = {"ok": True, "msg": "BSB-LAN erreichbar", "t": time.time()}
        try:
            data = json.loads(txt)
        except ValueError:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", txt))   # ältere Versionen: Komma am Ende
            except ValueError:
                msg = f"{c['host']} liefert kein BSB-LAN-JSON – das Gerät ist kein BSB-LAN-Adapter (z. B. Sonoff/ESPHome). Bitte anderen Adapter wählen."
                self.conn = {"ok": False, "msg": msg, "t": time.time()}
                self._log("http", f"✗ {safe}: {msg}", "WARNING")
                raise RuntimeError(msg) from None
        self._log("http", f"← {safe}", "DEBUG", detail=data)
        return data

    def _bsblan_row(self, line: str, d: dict[str, Any]) -> dict[str, Any]:
        val, unit, desc = d.get("value"), d.get("unit") or "", d.get("desc") or ""
        num = _f(val)
        if desc:
            text = desc
        elif num is not None:
            text = f"{val} {unit}".strip()
        else:
            text = str(val if val is not None else "—")
        info = {"line": line, "name": d.get("name"), "unit": unit, "text": text, "value": num if num is not None else val,
                "valid": str(val) not in ("---", "", "None") and int(d.get("error") or 0) == 0,
                "ro": int(d.get("readwrite", 0) or 0) == 1, "ts": time.time(),
                "cat": self.cat.category(float(line)) if self.cat is not None and _f(line) is not None else None}
        if d.get("possibleValues"):
            info["enum"] = [[_f(x.get("enumValue")), x.get("desc")] for x in d["possibleValues"]]
        return info

    # ------------------------------------------------------------- Abfragen
    async def query_lines(self, lines: list[str]) -> dict[str, dict[str, Any]]:
        """Werte einzelner Zeilen abfragen (Favoriten, Erkennung, Panel)."""
        cat = await self.async_catalog()
        c = self.cfg
        out: dict[str, dict[str, Any]] = {}
        if c["kind"] == BSBLAN:
            for i in range(0, len(lines), 20):
                data = await self.bsblan("JQ=" + ",".join(lines[i:i + 20]))
                for ln, d in (data or {}).items():
                    if isinstance(d, dict):
                        out[_key(ln)] = self._bsblan_row(_key(ln), d)
            self.values.update(out)
            return out
        if c["kind"] != ESPHOME:
            raise RuntimeError("Kein Adapter gewählt")
        for ln in lines:
            key = _key(ln)
            p = cat.line(float(ln)) if _f(ln) is not None else None
            if p is None:
                out[key] = {"line": key, "text": "nicht in der Tabelle", "valid": False}
                continue
            t = await self.esp_query_param(p)
            if t is None:
                out[key] = {**cat.info(p), "text": "keine Antwort", "valid": False, "ts": time.time()}
            elif t.cmd in (5, 8):
                out[key] = {**cat.info(p), "text": "vom Regler abgelehnt", "valid": False, "ts": time.time()}
            else:
                d = P.decode(cat, p, t.payload)
                out[key] = {**cat.info(p), "text": d["text"], "value": d["value"], "valid": d["valid"], "raw": d["raw"], "ts": time.time()}
                self.values[key] = out[key]
        return out

    def _start_job(self, kind: str, **extra: Any) -> dict[str, Any]:
        self.job = {"type": kind, "running": True, "started": time.time(), "log": [], "cancel": False,
                    "done": 0, "total": 0, "found": 0, **extra}
        return self.job

    def _jlog(self, msg: str) -> None:
        if self.job is not None:
            self.job.setdefault("log", []).append(f"{time.strftime('%H:%M:%S')}  {msg}")
            del self.job["log"][:-80]
        self._log("scan", msg, force=True)

    async def async_identify(self, background: bool = True) -> None:
        if self._busy():
            return
        self._start_job("identify")
        await self._async_update_monitor()
        coro = self._run(self._identify_core)
        if background:
            self._task = self.hass.async_create_background_task(coro, "ems_esp_manager bsb identify")
        else:
            await coro

    async def _run(self, fn, *args) -> None:
        """Ablauf mit Fehlerbehandlung und sauberem Ende."""
        try:
            res = await fn(*args)
            if self.job is not None and res is not None:
                self.job["result"] = res
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("BSB %s", fn.__name__, exc_info=True)
            self._jlog(f"FEHLER: {type(err).__name__}: {err}")
            if self.job is not None:
                self.job["error"] = f"{type(err).__name__}: {err}"
        finally:
            await self._finish_job()

    async def _identify_core(self) -> dict[str, Any]:
        """6224/6225/6226 lesen (BSB-LAN: zusätzlich /JI); ohne bsb_get aus den Adapter-Entitäten."""
        cat = await self.async_catalog()
        c = self.cfg
        self._jlog(f"Regler-Erkennung über {self.title()}")
        if c["kind"] == BSBLAN:
            info = await self.bsblan("JI")
            self.st["adapter_info"] = {k: info.get(k) for k in ("name", "version", "hardware", "bus", "busaddr", "busdest", "buswritable", "uptime") if k in info}
            self._jlog(f"BSB-LAN {info.get('version', '?')} · Bus {info.get('bus', '?')}")
        vals: dict[str, Any] = {}
        try:
            vals = await self.query_lines(["6225", "6226", "6224"])
        except RuntimeError as err:
            self._jlog(f"Direkte Abfrage nicht möglich: {err}")
        fam = _int((vals.get("6225") or {}).get("value"))
        var = _int((vals.get("6226") or {}).get("value"))
        idt = (vals.get("6224") or {}).get("value")
        if fam is None and c["kind"] == ESPHOME:
            for e in self.adapter_entities():           # ältere Firmware: feste Entitäten auswerten
                n = e["name"].lower()
                if "familie" in n:
                    fam = _int(e["state"])
                elif "variante" in n:
                    var = _int(e["state"])
                elif "identifikation" in n and e["state"] not in ("unknown", "unavailable"):
                    idt = e["state"]
            if fam is not None:
                self._jlog("Werte aus den Entitäten des Adapters übernommen")
        ident = P.identify(cat, idt if isinstance(idt, str) else None, fam, var)
        ident.update(t=time.time(), adapter=self.title())
        self.st["ident"] = ident
        await self.mgr.async_save()
        if fam is None and not ident.get("ident"):
            self._jlog("⚠ Regler antwortet nicht – Bus angeschlossen? Adapter-Firmware mit „bsb_get“?")
        else:
            self._jlog(f"Erkannt: {ident['title']}" + (f" – {ident['known_device']}" if ident.get("known_device") else ""))
            if ident.get("series"):
                self._jlog(ident["series"])
        self._notify()
        return ident

    async def async_scan(self, scope: str = "quick") -> None:
        if self._busy():
            return
        self._start_job("scan", scope=scope)
        await self._async_update_monitor()
        self._task = self.hass.async_create_background_task(self._run(self._scan, scope), "ems_esp_manager bsb scan")

    async def async_probe(self, prefix: int, start: int, end: int, name: str = "") -> None:
        if self._busy():
            return
        if self.cfg["kind"] != ESPHOME:
            raise RuntimeError("ID-Suchlauf nur mit ESPHome-Adapter (BSB-LAN hat eine eigene Parameterliste)")
        if not 0 <= start <= end <= 0xFFFF or end - start > 0x1000:
            raise RuntimeError("Bereich ungültig (höchstens 4096 IDs je Suchlauf)")
        self._start_job("probe", name=name, range=f"{prefix:04X}{start:04X}–{prefix:04X}{end:04X}")
        await self._async_update_monitor()
        self._task = self.hass.async_create_background_task(self._run(self._probe, prefix, start, end), "ems_esp_manager bsb probe")

    def cancel(self) -> None:
        if self.job is not None:
            self.job["cancel"] = True

    async def _scan(self, scope: str) -> dict[str, Any]:
        cat = await self.async_catalog()
        c = self.cfg
        t0 = time.time()
        results: dict[str, dict[str, Any]] = {}
        errors = empty = 0
        job = self.job or {}
        if c["kind"] == ESPHOME and not (self.st.get("ident") or {}).get("ident"):
            self._jlog("Regler noch nicht erkannt – zuerst Erkennung")
            await self._identify_core()
        if c["kind"] == BSBLAN:
            cats = await self.bsblan("JK=ALL")
            ids = sorted(cats.keys(), key=lambda k: int(k))
            if scope == "quick":
                ids = [k for k in ids if cat.category(float(cats[k].get("min", -1))) != "Sonstige"]
            job["total"] = len(ids)
            self._jlog(f"BSB-LAN: {len(ids)} Kategorien werden gelesen")
            for i, k in enumerate(ids):
                if job.get("cancel"):
                    self._jlog("Abgebrochen")
                    break
                params = await self.bsblan(f"JK={k}")
                lines = [ln for ln, d in (params or {}).items() if isinstance(d, dict)]
                for j in range(0, len(lines), 20):
                    data = await self.bsblan("JQ=" + ",".join(lines[j:j + 20]))
                    for ln, d in (data or {}).items():
                        if not isinstance(d, dict):
                            continue
                        if int(d.get("error") or 0):
                            errors += 1
                            continue
                        row = self._bsblan_row(_key(ln), d)
                        row["cat"] = cats[k].get("name") or row.get("cat")
                        results[_key(ln)] = row
                        empty += 0 if row["valid"] else 1
                job.update(done=i + 1, found=len(results))
                self._jlog(f"{cats[k].get('name', k)}: {len(lines)} Parameter")
        else:
            todo = sorted(cat.params, key=lambda p: p["line"])
            job["total"] = len(todo)
            self._jlog(f"{len(todo)} Werte aus der Tabelle ({len(self.user_params)} davon angelernt)")
            last = None
            for i, p in enumerate(todo):
                if job.get("cancel"):
                    self._jlog("Abgebrochen")
                    break
                cname = cat.category(float(p["line"]))
                if cname != last:
                    self._jlog(f"→ {cname}")
                    last = cname
                t = await self.esp_query_param(p, timeout=1.2, retries=1)
                job["done"] = i + 1
                if t is None or t.cmd in (5, 8):
                    errors += 1
                    continue
                d = P.decode(cat, p, t.payload)
                key = _key(p["line"])
                results[key] = {**cat.info(p), "text": d["text"], "value": d["value"], "valid": d["valid"], "raw": d["raw"], "ts": time.time()}
                self.values[key] = results[key]
                empty += 0 if d["valid"] else 1
                job["found"] = len(results)
                await asyncio.sleep(0.05)
        dur = round(time.time() - t0)
        self.st["scan"] = {"t": time.time(), "scope": scope, "kind": c["kind"], "total": job.get("total"),
                           "found": len(results), "empty": empty, "errors": errors, "duration": dur, "results": results}
        await self.mgr.async_save()
        self._jlog(f"Fertig nach {dur} s: {len(results)} Werte ({empty} ohne Wert), {errors} vom Regler nicht unterstützt")
        return {"found": len(results)}

    async def _probe(self, prefix: int, start: int, end: int) -> dict[str, Any]:
        """ID-Suchlauf: jede ID im Bereich einmal abfragen; Antworten → Unbekannte Werte / Tabelle."""
        cat = await self.async_catalog()
        job = self.job or {}
        ids = [(prefix << 16) | lo for lo in range(start, end + 1)]
        job["total"] = len(ids)
        self._jlog(f"Suchlauf {prefix:04X}{start:04X} … {prefix:04X}{end:04X} ({len(ids)} IDs)")
        new = known = 0
        for i, cmd in enumerate(ids):
            if job.get("cancel"):
                self._jlog("Abgebrochen")
                break
            t = await self.esp_query(cmd, timeout=0.8, retries=0)
            job["done"] = i + 1
            if t is None or t.cmd != 7:
                continue
            if cat.for_cmd(cmd):
                known += 1
                continue
            self._remember_unknown(cmd, t, time.time(), found=True)
            new += 1
            job["found"] = new
            g = P.guess(t.payload)
            self._jlog(f"neu: {cmd:08X}  Nutzdaten {t.payload.hex(' ').upper()}" + (f"  (z. B. {g[0]['text']})" if g else ""))
        await self._save_unknown()
        self._jlog(f"Fertig: {new} neue Werte, {known} schon in der Tabelle – unter „Unbekannte Werte“ benennen")
        return {"new": new, "known": known}

    async def _save_unknown(self) -> None:
        self.st["unknown"] = self.unknown_rows()
        self._unknown_dirty = False
        await self.mgr.async_save()

    async def async_learn(self, cmd_hex: str, name: str, codec: str, ro: bool = True) -> dict[str, Any]:
        """Unbekannten Wert benennen → Anlagen-Tabelle (gilt wie die eigene Tabelle, auch für Sensoren)."""
        cmd = int(cmd_hex, 16)
        if codec not in P.CODECS:
            raise RuntimeError("Unbekannter Datentyp")
        name = name.strip()[:60]
        if not name:
            raise RuntimeError("Name fehlt")
        lines = [float(u["line"]) for u in self.user_params]
        line = int(max(lines) + 1) if lines else USER_LINE_START
        self.user_params.append({"line": line, "cmd": f"{cmd:08X}", "codec": codec, "name": name, "ro": ro,
                                 "enum": "onoff" if codec == "onoff" else None})
        u = self.unknown.pop(cmd, None)
        cat = await self.async_catalog(reload=True)
        if u and u.get("payload") is not None:
            p = cat.line(line)
            d = P.decode(cat, p, u["payload"])
            self.values[_key(line)] = {**cat.info(p), "text": d["text"], "value": d["value"], "valid": d["valid"], "raw": d["raw"], "ts": u.get("last")}
        await self._save_unknown()
        self._log("scan", f"Angelernt: {cmd:08X} → {line} {name} ({codec})", force=True)
        self._notify()
        return {"line": line}

    async def async_forget(self, line: str) -> None:
        before = len(self.user_params)
        self.mgr.vdata(VENDOR_BSB)["bsb_user_params"] = [u for u in self.user_params if _key(u["line"]) != _key(line)]
        if len(self.user_params) != before:
            self.values.pop(_key(line), None)
            await self.async_catalog(reload=True)
            if _key(line) in self.cfg["favorites"]:
                await self.async_save_cfg({"favorites": [x for x in self.cfg["favorites"] if x != _key(line)]})
            await self.mgr.async_save()

    async def _finish_job(self) -> None:
        if self.job is not None:
            self.job.update(running=False, finished=time.time())
            self.job.pop("cancel", None)
        job, self.job = self.job, None
        self.st["last_job"] = job
        await self._async_update_monitor()
        self._notify()

    def _busy(self) -> bool:
        return bool(self.job is not None and self.job.get("running")) or self._fw_running()

    # ------------------------------------------------------------- Firmware-Update (OTA)
    def _fw_running(self) -> bool:
        return bool(self.fw_job and self.fw_job.get("running"))

    @property
    def _fw_dir(self) -> Path:
        return Path(self.hass.config.path("ems_esp_manager", "adapter_firmware"))

    def _fw_context(self) -> dict[str, Any]:
        """Gerätename, Projekt und API-Schlüssel des gewählten Adapters (zum Prüfen der Datei)."""
        e = self._esphome_entry()
        dev = dr.async_get(self.hass).async_get(self.cfg["device_id"]) if self.cfg["device_id"] else None
        project = None
        if dev and dev.manufacturer and dev.model and " " not in f"{dev.manufacturer}{dev.model}":
            project = f"{dev.manufacturer}.{dev.model}"
        return {"node": (e.data.get("device_name") if e else None) or self.cfg["node"] or None,
                "project": project, "psk": (e.data.get("noise_psk") if e else None) or None}

    def fw_installed(self) -> dict[str, Any]:
        dev = dr.async_get(self.hass).async_get(self.cfg["device_id"]) if self.cfg["device_id"] else None
        sw = (dev.sw_version if dev else None) or ""
        m = _RE_SW.match(sw)
        return {"sw": sw or None, "version": m.group(1) if m else None, "esphome": m.group(2) if m else (sw or None),
                "project": self._fw_context()["project"]}

    def fw_snapshot(self) -> dict[str, Any]:
        c = self.cfg
        return {"files": self.fw_files, "job": self.fw_job, "installed": self.fw_installed() if c["kind"] == ESPHOME else None,
                "port": c["ota_port"], "has_password": bool(c["ota_password"])}

    def _fw_index_path(self) -> Path:
        return self._fw_dir / "index.json"

    def _fw_load_blocking(self) -> list[dict[str, Any]]:
        try:
            idx = json.loads(self._fw_index_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [f for f in idx if (self._fw_dir / f"{f['id']}.bin").is_file()]

    def _fw_store_blocking(self, data: bytes, entry: dict[str, Any]) -> list[dict[str, Any]]:
        self._fw_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._fw_dir / f"{entry['id']}.tmp"
        tmp.write_bytes(data)
        tmp.replace(self._fw_dir / f"{entry['id']}.bin")
        files = [f for f in self._fw_load_blocking() if f["id"] != entry["id"]]
        files.insert(0, entry)
        for old in files[FW_KEEP:]:
            (self._fw_dir / f"{old['id']}.bin").unlink(missing_ok=True)
        files = files[:FW_KEEP]
        self._fw_index_path().write_text(json.dumps(files, indent=1, ensure_ascii=False), encoding="utf-8")
        return files

    def _fw_delete_blocking(self, fid: str) -> list[dict[str, Any]]:
        files = [f for f in self._fw_load_blocking() if f["id"] != fid]
        (self._fw_dir / f"{fid}.bin").unlink(missing_ok=True)
        if self._fw_dir.is_dir():
            self._fw_index_path().write_text(json.dumps(files, indent=1, ensure_ascii=False), encoding="utf-8")
        return files

    async def async_fw_reload(self) -> None:
        self.fw_files = await self.hass.async_add_executor_job(self._fw_load_blocking)

    async def async_fw_put(self, uid: str, filename: str, offset: int, total: int, b64: str) -> dict[str, Any]:
        """Firmware-Datei in Stücken empfangen (Websocket); am Ende prüfen und speichern."""
        now = time.time()
        for k in [k for k, u in self._fw_uploads.items() if now - u["t"] > 600]:
            self._fw_uploads.pop(k, None)
        if total <= 0 or total > esp_ota.MAX_SIZE:
            raise esp_ota.OtaError("Datei größer als 4 MB – keine OTA-Firmware.")
        if offset == 0:
            self._fw_uploads[uid] = {"t": now, "data": bytearray()}
        up = self._fw_uploads.get(uid)
        if up is None or len(up["data"]) != offset:
            self._fw_uploads.pop(uid, None)
            raise esp_ota.OtaError("Hochladen unterbrochen – bitte erneut versuchen.")
        up["data"] += base64.b64decode(b64)
        up["t"] = now
        if len(up["data"]) > total:
            self._fw_uploads.pop(uid, None)
            raise esp_ota.OtaError("Mehr Daten als angekündigt – bitte erneut versuchen.")
        if len(up["data"]) < total:
            return {"received": len(up["data"])}
        data = bytes(self._fw_uploads.pop(uid)["data"])
        ctx = self._fw_context()
        name = Path(filename or "firmware.bin").name[:80]
        info = await self.hass.async_add_executor_job(
            lambda: esp_ota.inspect(data, name, ctx["node"], ctx["project"], ctx["psk"]))
        entry = {"id": info["sha"], "filename": name, "uploaded": now, "node": ctx["node"], "info": info}
        self.fw_files = await self.hass.async_add_executor_job(self._fw_store_blocking, data, entry)
        self._log("conn", f"Firmware-Datei gespeichert: {name} ({info.get('version') or 'Version unbekannt'}, "
                          f"{round(info['size'] / 1024)} KB)", force=True)
        return {"done": True, "file": entry}

    async def async_fw_delete(self, fid: str) -> None:
        if self._fw_running() and (self.fw_job or {}).get("id") == fid:
            raise esp_ota.OtaError("Diese Datei wird gerade aufgespielt.")
        self.fw_files = await self.hass.async_add_executor_job(self._fw_delete_blocking, fid)

    async def async_fw_install(self, fid: str) -> None:
        c = self.cfg
        if c["kind"] != ESPHOME:
            raise esp_ota.OtaError("Firmware-Update gibt es nur für den ESPHome-Adapter.")
        if self._fw_running():
            raise esp_ota.OtaError("Es läuft bereits ein Firmware-Update.")
        if self.job is not None and self.job.get("running"):
            raise esp_ota.OtaError("Erst den laufenden Scan / die Erkennung abwarten oder abbrechen.")
        ai = self.adapter_info()
        host = ai.get("host")
        if not host:
            raise esp_ota.OtaError("Adresse des Adapters unbekannt – ESPHome-Gerät in Home Assistant prüfen.")
        meta = next((f for f in self.fw_files if f["id"] == fid), None)
        if meta is None:
            raise esp_ota.OtaError("Firmware-Datei nicht gefunden – bitte neu hochladen.")
        path = self._fw_dir / f"{fid}.bin"
        data = await self.hass.async_add_executor_job(path.read_bytes)
        before = self.fw_installed()
        self.fw_job = {"running": True, "id": fid, "file": meta["filename"], "version": (meta.get("info") or {}).get("version"),
                       "from": before.get("version") or before.get("sw"), "host": host, "port": c["ota_port"],
                       "phase": "connect", "pct": 0, "t": time.time(), "msg": ""}
        self._log("conn", f"Firmware-Update gestartet: {meta['filename']} → {host}:{c['ota_port']}", force=True)
        self._notify()
        e = self._esphome_entry()
        psk = (e.data.get("noise_psk") if e else None) if (meta.get("info") or {}).get("psk_runtime") else None
        self._fw_task = self.hass.async_create_background_task(
            self._fw_run(host, c["ota_port"], data, c["ota_password"] or None, before.get("sw"), psk), "ems_esp_manager bsb firmware")

    async def _fw_run(self, host: str, port: int, data: bytes, password: str | None, sw_before: str | None,
                      psk: str | None = None) -> None:
        job = self.fw_job
        assert job is not None
        last = [0.0]
        key_done = psk is None

        def prog(phase: str, pct: float) -> None:
            job["phase"], job["pct"] = phase, round(pct * 100)
            if time.time() - last[0] > 1.0:
                last[0] = time.time()
                self._notify()

        try:
            res = await esp_ota.flash(host, data, port, password, prog)
            job.update(phase="reboot", pct=100, sent=time.time(), ota=res)
            self._log("conn", f"Firmware übertragen ({round(len(data) / 1024)} KB) – Adapter startet neu", force=True)
            self._notify()
            t0, seen_off, back = time.time(), False, False
            while time.time() - t0 < 150 and not self._stopped:
                await asyncio.sleep(2)
                if not key_done and time.time() - t0 > 8:
                    # Firmware ab 2.4 hat den Schlüssel nicht mehr in der Datei: bisherigen HA-Schlüssel in den Adapter
                    # schreiben, damit Home Assistant verbunden bleibt (nur solange der Adapter noch keinen hat)
                    r = await _provision_key(self.hass, host, psk)
                    if r is not None:
                        key_done = True
                        self._log("conn", r, force=True)
                        if r.startswith("API-Schlüssel übertragen"):
                            ent = self._esphome_entry()
                            if ent is not None:
                                self.hass.async_create_task(self.hass.config_entries.async_reload(ent.entry_id))
                e = self._esphome_entry()
                avail = bool(e is not None and getattr(getattr(e, "runtime_data", None), "available", False))
                if not avail:
                    seen_off = True
                elif seen_off or time.time() - t0 > 30:
                    sw = self.fw_installed().get("sw")
                    if seen_off or sw != sw_before:
                        back = True
                        break
            now = self.fw_installed()
            job["now"] = now.get("version") or now.get("sw")
            if back:
                await asyncio.sleep(1)
                now = self.fw_installed()
                job["now"] = now.get("version") or now.get("sw")
                want = job.get("version")
                if want and now.get("version") and now["version"] != want:
                    job.update(phase="done", ok=True, msg=f"Adapter läuft wieder – meldet Version {now['version']} (erwartet {want}).")
                else:
                    job.update(phase="done", ok=True, msg=f"Fertig – Adapter läuft mit {job['now'] or 'der neuen Firmware'}.")
            else:
                job.update(phase="done", ok=True, warn=True,
                           msg="Firmware übertragen, der Adapter meldet sich aber noch nicht zurück – Strom und WLAN prüfen.")
            if not key_done:
                job["msg"] += (" Hinweis: Den bisherigen API-Schlüssel bitte in der Adapter-Weboberfläche unter System & Update → "
                               "Home Assistant eintragen (oder neu erzeugen und hier unter Adapter → „Koppeln“).")
                job["warn"] = True
            self._log("conn", job["msg"], force=True)
        except esp_ota.OtaError as err:
            job.update(phase="error", ok=False, msg=str(err))
            self._log("err", f"Firmware-Update fehlgeschlagen: {err}", "WARNING", force=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("ESP-Bus-Adapter: Firmware-Update")
            job.update(phase="error", ok=False, msg=f"{type(err).__name__}: {err}")
            self._log("err", f"Firmware-Update fehlgeschlagen: {err}", "WARNING", force=True)
        finally:
            job["running"] = False
            job["end"] = time.time()
            if not self._stopped:
                await self._async_update_monitor()
            self._notify()

    # ------------------------------------------------------------- Schreiben
    async def async_write(self, line: str, value: Any) -> dict[str, Any]:
        c = self.cfg
        if c["kind"] != BSBLAN:
            raise RuntimeError("Direktes Schreiben nur mit BSB-LAN – beim ESPHome-Adapter über dessen Entitäten")
        data = await self.bsblan("JS", {"Parameter": str(line), "Value": str(value), "Type": "1", "Destination": str(c["dest"])})
        r = (data or {}).get(str(line)) or {}
        ok = int(r.get("status", 0) or 0) == 1
        self._log("tx", f"SET {line} = {value} → {'OK' if ok else 'abgelehnt'}", "INFO" if ok else "WARNING", force=True)
        await self.query_lines([str(line)])
        return {"ok": ok, "result": r}

    # ------------------------------------------------------------- zyklisch
    async def _tick(self, _now=None) -> None:
        if self._stopped:
            return
        c = self.cfg
        if c["kind"] == ESPHOME and self._monitor_needed():
            # Adapter neu verbunden (neuer Client) oder Abo verloren → neu abonnieren
            if self._log_unsub is None or self._client() is not self._log_client:
                self._subscribe()
        if self._unknown_dirty and not self._busy() and int(time.time()) % 300 < 5:
            await self._save_unknown()
        if not c["poll_enabled"] or not c["favorites"] or self._busy() or self._poll_lock.locked():
            return
        if time.time() - self._last_poll < c["poll_interval"] - 1:
            return
        async with self._poll_lock:
            self._last_poll = time.time()
            try:
                await self.query_lines(list(c["favorites"]))
            except Exception as err:  # noqa: BLE001
                self._log("err", f"Bus-Abfrage: {type(err).__name__}: {err}", "WARNING")
            self._notify()

    # ------------------------------------------------------------- Start/Stopp
    def start(self) -> None:
        # v1.22.4: Bus-Mitschnitt startet nach jedem HA-Start ausgeschaltet
        if isinstance(self.mgr.vdata(VENDOR_BSB).get("bsb"), dict) and self.mgr.vdata(VENDOR_BSB)["bsb"].get("log_enabled"):
            self.mgr.vdata(VENDOR_BSB)["bsb"]["log_enabled"] = False
            self.hass.async_create_task(self.mgr.async_save())
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=5)))
        self._stop_unsub = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_stop)
        self.hass.async_create_background_task(self._start(), "ems_esp_manager bsb start")

    async def _start(self) -> None:
        await self.async_catalog()
        await self.async_fw_reload()
        await self._async_update_monitor()

    async def _on_stop(self, _event) -> None:
        self._stop_unsub = None   # Listener hat bereits gefeuert – nicht mehr entfernen
        if self._unknown_dirty:
            await self._save_unknown()
        self.stop()

    def stop(self) -> None:
        self._stopped = True
        for u in self._unsubs:
            try:
                u()
            except Exception:  # noqa: BLE001
                pass
        self._unsubs.clear()
        if getattr(self, "_stop_unsub", None):
            self._stop_unsub()
            self._stop_unsub = None
        self._unsubscribe()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self._fw_task is not None and not self._fw_task.done():
            self._fw_task.cancel()
        ft = getattr(self, "_find_task", None)
        if ft is not None and not ft.done():
            ft.cancel()


def _manager_version() -> str:
    from .const import MANAGER_VERSION

    return MANAGER_VERSION


def _key(line: Any) -> str:
    f = _f(line)
    if f is None:
        return str(line)
    return str(int(f)) if f == int(f) else str(f)


def _int(v: Any) -> int | None:
    f = _f(v)
    return int(f) if f is not None else None


def get(hass: HomeAssistant) -> BsbAdapter | None:
    return hass.data.get(_DATA)


@callback
def async_setup(hass: HomeAssistant, mgr) -> BsbAdapter:
    a = hass.data.get(_DATA)
    if a is None:
        a = BsbAdapter(hass, mgr)
        hass.data[_DATA] = a
        a.start()
    return a


@callback
def _reload_entries(hass: HomeAssistant) -> None:
    """Favoriten geändert → BSB-Gerät neu laden (Sensoren anlegen/entfernen)."""
    from .const import DOMAIN, VENDOR_BSB, entry_vendor

    for e in hass.config_entries.async_entries(DOMAIN):
        if entry_vendor(e) == VENDOR_BSB:
            fn = getattr(hass.config_entries, "async_schedule_reload", None)
            if fn is not None:
                fn(e.entry_id)
            else:
                hass.async_create_task(hass.config_entries.async_reload(e.entry_id))


# =====================================================================
# Adapter suchen (Config-Flow, Panel, automatische Erkennung)
# =====================================================================
def _nolog(_msg: str) -> None:
    return None


@callback
def find_esphome(hass: HomeAssistant, log: Callable[[str], None] = _nolog) -> list[dict[str, Any]]:
    """ESPHome-Geräte, die ein ESP-Bus-Adapter sind (Aktion bsb_get oder BSB-Werte)."""
    out: list[dict[str, Any]] = []
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    for e in hass.config_entries.async_entries(ESPHOME):
        name = e.data.get("device_name") or e.title
        node = str(name).replace("-", "_")
        has_get = hass.services.has_service(ESPHOME, f"{node}_bsb_get")
        ents = er.async_entries_for_config_entry(ent_reg, e.entry_id)
        hits = [x for x in ents if _RE_BSB_ENTITY.search(f"{x.original_name or ''} {x.entity_id}")]
        devs = dr.async_entries_for_config_entry(dev_reg, e.entry_id)
        dev = devs[0] if devs else None
        own = _is_own_adapter(dev)
        if not has_get and not hits and not own:
            continue
        rt = getattr(e, "runtime_data", None)
        item = {
            "kind": ESPHOME, "id": dev.id if dev else e.entry_id, "device_id": dev.id if dev else "",
            "node": name, "title": (dev.name_by_user or dev.name) if dev else e.title,
            "model": dev.model if dev else None, "sw": dev.sw_version if dev else None,
            "has_get": has_get, "entities": len(ents), "bsb_entities": len(hits),
            "online": bool(getattr(rt, "available", False)),
            "host": e.data.get("host") or "", "ha_state": getattr(e.state, "value", str(e.state)),
            "reauth": _reauth_pending(hass, e.entry_id),
            "monitor": any(x.domain == "switch" and _RE_MONITOR.search(f"{x.original_name or ''} {x.entity_id}") for x in ents),
        }
        log(f"ESPHome: {item['title']} ({name}) – {'Aktion bsb_get ✓' if has_get else 'ohne bsb_get (offline oder Firmware < 1.2)'}, {len(hits)} BSB-Werte"
            + (" – Schlüssel passt nicht (in HA neu koppeln)" if item["reauth"] else ""))
        out.append(item)
    # Von HA gefundene, aber noch nicht eingebundene ESPHome-Geräte (Zeroconf/DHCP) → koppeln
    known = {str(x.get("node") or "").lower() for x in out}
    for fl in hass.config_entries.flow.async_progress_by_handler(ESPHOME):
        ctx = fl.get("context") or {}
        if ctx.get("source") not in ("zeroconf", "dhcp", "mqtt", "integration_discovery"):
            continue
        nm = str((ctx.get("title_placeholders") or {}).get("name") or "").strip()
        if not nm or nm.lower() in known:
            continue
        host = f"{nm}.local" if "." not in nm and " " not in nm else ""
        out.append({"kind": ESPHOME_NEW, "id": f"esphome_new_{fl['flow_id']}", "host": host, "node": nm,
                    "title": nm, "pair": True, "source": "von Home Assistant gefunden, noch nicht eingebunden"})
        log(f"ESPHome (noch nicht eingebunden): {nm} – mit API-Schlüssel koppeln")
    return out


def _is_own_adapter(dev) -> bool:
    """ESP-Bus-Adapter-Firmware (ESPHome-Projekt matheis.esp-bus-adapter / -lan)."""
    return bool(dev is not None and str(dev.manufacturer or "").lower() == "matheis"
                and str(dev.model or "").lower().startswith("esp-bus-adapter"))


@callback
def _reauth_pending(hass: HomeAssistant, entry_id: str) -> bool:
    for fl in hass.config_entries.flow.async_progress_by_handler(ESPHOME):
        ctx = fl.get("context") or {}
        if ctx.get("source") == "reauth" and ctx.get("entry_id") == entry_id:
            return True
    return False


async def _esphome_port_open(host: str, port: int = 6053, timeout: float = 2.0) -> bool:
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


@callback
def find_bsblan_entries(hass: HomeAssistant, log: Callable[[str], None] = _nolog) -> list[dict[str, Any]]:
    """BSB-LAN-Adapter, die schon in der HA-Integration „BSB-LAN“ eingerichtet sind."""
    out = []
    for e in hass.config_entries.async_entries(BSBLAN):
        host = e.data.get("host")
        if not host:
            continue
        port = e.data.get("port") or 80
        hp = host if int(port) == 80 else f"{host}:{port}"
        out.append({"kind": BSBLAN, "id": f"bsblan_{hp}", "host": hp, "title": e.title or f"BSB-LAN {hp}",
                    "passkey": e.data.get("passkey") or "", "username": e.data.get("username") or "",
                    "password": e.data.get("password") or "", "online": True, "has_get": True, "source": "HA-Integration BSB-LAN"})
        log(f"BSB-LAN (HA-Integration): {hp}")
    return out


_BSB_BUSSE = {"BSB", "LPB", "PPS"}
_BSB_JI_KEYS = ("buswritable", "busaddr", "busdest", "monitor", "verbose", "protectedGPIO", "loginterval")


def _is_bsblan_ji(info: Any) -> bool:
    """Echte BSB-LAN-Antwort auf /JI? (JSON-Objekt mit BSB-LAN-typischen Feldern)

    Früher reichte „BSB“ irgendwo im Text – das traf auch Sonoff/Tasmota/ESPHome-
    Webseiten. Jetzt: JSON-Objekt UND (name beginnt mit „BSB“ ODER bus = BSB/LPB/PPS
    zusammen mit mindestens einem weiteren BSB-LAN-Feld).
    """
    if not isinstance(info, dict):
        return False
    if str(info.get("name") or "").strip().upper().startswith("BSB"):
        return True
    bus = str(info.get("bus") or "").strip().upper()
    return bus in _BSB_BUSSE and any(k in info for k in _BSB_JI_KEYS)


async def _ha_known_hosts(hass: HomeAssistant) -> set[str]:
    """IP-Adressen von Geräten, die HA schon über andere Integrationen kennt
    (ESPHome, Shelly, Tasmota, Sonoff …) – das sind keine BSB-LAN-Adapter."""
    import ipaddress
    import socket

    names: set[str] = set()
    for e in hass.config_entries.async_entries():
        if e.domain == BSBLAN:
            continue
        for k in ("host", "ip_address", "ip", "address"):
            v = e.data.get(k)
            if isinstance(v, str) and v.strip():
                names.add(v.strip().split(":")[0])
    ips: set[str] = set()
    loop = asyncio.get_running_loop()

    async def res(n: str) -> None:
        try:
            ipaddress.ip_address(n)
            ips.add(n)
            return
        except ValueError:
            pass
        try:
            async with asyncio.timeout(1.5):
                infos = await loop.getaddrinfo(n, None, family=socket.AF_INET)
            ips.update(i[4][0] for i in infos)
        except Exception:  # noqa: BLE001
            pass

    await asyncio.gather(*(res(n) for n in names))
    return ips


async def _probe_bsblan(hass: HomeAssistant, host: str, timeout: float = 2.5,
                        manual: bool = False) -> dict[str, Any] | None:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(timeout):
            r = await session.get(f"http://{host}/JI", allow_redirects=False)
            async with r:
                status = r.status
                auth = r.headers.get("WWW-Authenticate", "")
                txt = await r.text(errors="replace") if status == 200 else ""
    except Exception:  # noqa: BLE001
        return None
    if status == 401:
        # BSB-LAN schützt mit HTTP-Basic; ESPHome/Sonoff & Co. nutzen Digest → bei der
        # Heimnetz-Suche nicht anzeigen (bei manuell eingegebener Adresse schon)
        if not manual and not auth.strip().lower().startswith("basic"):
            return None
        return {"kind": BSBLAN, "id": f"bsblan_{host}", "host": host, "title": f"Webgerät {host} (Anmeldung nötig – evtl. BSB-LAN)", "protected": True}
    t = txt.lstrip()
    if status != 200 or not t.startswith("{"):
        return None
    try:
        info = json.loads(re.sub(r",\s*([}\]])", r"\1", t))
    except ValueError:
        return None
    if not _is_bsblan_ji(info):
        return None
    return {"kind": BSBLAN, "id": f"bsblan_{host}", "host": host, "title": f"BSB-LAN {host}", "version": info.get("version"),
            "bus": info.get("bus"), "online": True, "has_get": True}


async def find_adapters(hass: HomeAssistant, lan: bool = False, host: str | None = None,
                        log: Callable[[str], None] = _nolog) -> list[dict[str, Any]]:
    """Alle ESP-Bus-Adapter: ESPHome, HA-Integration BSB-LAN, optional Heimnetz/Adresse."""
    found = find_esphome(hass, log) + find_bsblan_entries(hass, log)
    known = {f.get("host") for f in found if f.get("host")}
    if host:
        log(f"Prüfe {host} …")
        r = await _probe_bsblan(hass, host, 4, manual=True)
        hp = host.split("/")[0].split(":")[0]
        esp_known = [f for f in found if f.get("kind") == ESPHOME and str(f.get("host") or "").lower() == hp.lower()]
        if r and host not in known:
            found.append(r)
            log(f"BSB-LAN gefunden: {host}")
        elif esp_known:
            log(f"{hp}: ESPHome-Adapter „{esp_known[0]['title']}“ ist schon in Home Assistant eingebunden")
        elif await _esphome_port_open(hp):
            found.append({"kind": ESPHOME_NEW, "id": f"esphome_new_{hp}", "host": hp, "node": "", "title": f"ESPHome-Gerät {hp}",
                          "pair": True, "source": "nicht in Home Assistant eingebunden"})
            log(f"{hp}: ESPHome-Gerät (Port 6053) – nicht in Home Assistant eingebunden. Mit dem API-Schlüssel aus der "
                "Adapter-Weboberfläche koppeln.")
        elif not r:
            log(f"{host}: weder BSB-LAN (/JI) noch ESPHome (Port 6053) erreichbar")
    if lan:
        from .heater import _lan_hosts, _port_open

        hosts = await _lan_hosts(hass, log)
        log(f"Heimnetz: {len(hosts)} Adressen, suche Webserver (Port 80) …")
        sem = asyncio.Semaphore(48)
        web: list[str] = []

        async def chk(h: str) -> None:
            async with sem:
                if await _port_open(h, 80, 0.6):
                    web.append(h)

        await asyncio.gather(*(chk(h) for h in hosts))
        other = await _ha_known_hosts(hass)
        skip = [h for h in web if h in other]
        web = [h for h in web if h not in other]
        if skip:
            log(f"Heimnetz: {len(skip)} Webserver übersprungen (in HA schon als anderes Gerät bekannt, z. B. ESPHome/Sonoff)")
        log(f"Heimnetz: {len(web)} Webserver – frage /JI ab …")

        async def probe(h: str) -> None:
            async with sem:
                r = await _probe_bsblan(hass, h)
                if r and h not in known:
                    if r.get("protected"):
                        log(f"Heimnetz: {h} verlangt eine Anmeldung – falls das ein BSB-LAN ist: Adresse oben eintragen")
                        return
                    found.append(r)
                    log(f"Heimnetz: {r['title']}")

        await asyncio.gather(*(probe(h) for h in web))
    log(f"Fertig – {len(found)} Adapter")
    return found


async def async_find_into(adapter: BsbAdapter, lan: bool, host: str | None) -> None:
    """Suche für das Panel (Fortschritt in adapter.find)."""
    if adapter._find_task is not None and not adapter._find_task.done():  # noqa: SLF001
        return
    scan: dict[str, Any] = {"running": True, "lan": lan, "host": host, "log": [], "found": [], "started": time.time()}
    adapter.find = scan

    def log(msg: str) -> None:
        scan["log"].append(msg)
        del scan["log"][:-40]

    async def run() -> None:
        try:
            res = await find_adapters(adapter.hass, lan=lan, host=host, log=log)
            scan["found"] = [{k: v for k, v in f.items() if k != "password"} for f in res]
            adapter._find_results = {f["id"]: f for f in res}  # noqa: SLF001
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("ESP-Bus-Adapter-Suche")
            scan["error"] = f"{type(err).__name__}: {err}"
        finally:
            scan["running"] = False

    adapter._find_task = adapter.hass.async_create_background_task(run(), "ems_esp_manager bsb find")  # noqa: SLF001
    await asyncio.sleep(0)


async def async_select(adapter: BsbAdapter, found_id: str) -> None:
    f = (getattr(adapter, "_find_results", None) or {}).get(found_id)
    if f is not None and f.get("pair"):
        raise RuntimeError("Dieser Adapter ist noch nicht in Home Assistant eingebunden – unten mit dem API-Schlüssel „Koppeln“")
    if f is None:
        for x in find_esphome(adapter.hass) + find_bsblan_entries(adapter.hass):
            if x["id"] == found_id:
                f = x
    if f is None:
        raise RuntimeError("Adapter nicht mehr gefunden – bitte neu suchen")
    await adapter.async_save_cfg(config_from_found(f))
    adapter.st.pop("ident", None)
    adapter.st.pop("scan", None)
    await adapter.mgr.async_save()
    await adapter.async_identify()


async def async_pair(adapter: BsbAdapter, host: str, key: str) -> dict[str, Any]:
    """ESPHome-Adapter mit API-Schlüssel koppeln: Schlüssel prüfen, ESPHome-Eintrag in HA anlegen oder
    aktualisieren, danach als ESP-Bus-Adapter wählen."""
    from homeassistant.helpers.device_registry import format_mac

    hass = adapter.hass
    host = (host or "").strip().replace("http://", "").replace("https://", "").strip("/")
    key = re.sub(r"[\s\"']", "", key or "")
    if not host:
        raise RuntimeError("Adresse des Adapters fehlt (z. B. 192.168.0.65 oder bsb-heizung.local)")
    try:
        raw = base64.b64decode(key, validate=True)
    except (ValueError, TypeError):
        raw = b""
    if len(raw) != 32:
        raise RuntimeError("Schlüssel ungültig – 44 Zeichen, endet mit „=“ (Adapter-Weboberfläche → System & Update → Home Assistant)")
    port = 6053
    if host.count(":") == 1:
        host, p = host.split(":")
        port = int(p) if p.isdigit() else 6053

    info = await _esphome_device_info(hass, host, port, key)
    mac = format_mac(info.mac_address) if getattr(info, "mac_address", None) else ""
    name = getattr(info, "name", "") or ""
    adapter._jlog(f"Koppeln: {name or host} ({host}) – Schlüssel passt")  # noqa: SLF001

    entry = None
    for e in hass.config_entries.async_entries(ESPHOME):
        if (mac and e.unique_id == mac) or (name and e.data.get("device_name") == name) or e.data.get("host") == host:
            entry = e
            break
    created = False
    if entry is not None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, "host": host, "port": port, "noise_psk": key})
        for fl in hass.config_entries.flow.async_progress_by_handler(ESPHOME):
            ctx = fl.get("context") or {}
            if ctx.get("source") == "reauth" and ctx.get("entry_id") == entry.entry_id:
                hass.config_entries.flow.async_abort(fl["flow_id"])
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        entry = await _esphome_create_entry(hass, host, port, key, mac)
        created = True

    # Gerät abwarten (legt ESPHome nach dem Verbinden an) und als Adapter wählen
    dev = None
    for _ in range(30):
        devs = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if devs:
            dev = devs[0]
            break
        await asyncio.sleep(0.5)
    if dev is not None:
        await adapter.async_save_cfg(config_from_found({"kind": ESPHOME, "device_id": dev.id, "node": entry.data.get("device_name") or name,
                                                        "title": dev.name_by_user or dev.name or name}))
        adapter.st.pop("ident", None)
        await adapter.mgr.async_save()
        adapter.hass.async_create_task(adapter.async_identify())
    msg = (f"Gekoppelt: {name or host} – in Home Assistant {'neu eingebunden' if created else 'Schlüssel aktualisiert'}"
           + ("" if dev else " (Gerät erscheint gleich – dann hier „Neu suchen“)"))
    adapter._jlog(msg)  # noqa: SLF001
    return {"ok": True, "msg": msg, "name": name, "created": created, "selected": dev is not None}


async def _provision_key(hass: HomeAssistant, host: str, key: str) -> str | None:
    """Bisherigen Schlüssel in einen Adapter ohne Schlüssel schreiben (nach Update auf Firmware 2.4).
    None = Adapter noch nicht erreichbar (später erneut versuchen), sonst Meldung."""
    try:
        from aioesphomeapi import APIClient
    except ImportError:
        return "API-Schlüssel nicht übertragen (aioesphomeapi fehlt)"
    from homeassistant.components import zeroconf

    try:
        raw = base64.b64decode(key)
    except (ValueError, TypeError):
        return "API-Schlüssel nicht übertragen (Schlüssel in HA ungültig)"
    zc = await zeroconf.async_get_instance(hass)
    # Adapter ohne Schlüssel nimmt den bekannten Null-Schlüssel an – so geht der echte Schlüssel verschlüsselt raus
    zero = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    cli = APIClient(host, 6053, None, noise_psk=zero, zeroconf_instance=zc)
    try:
        async with asyncio.timeout(10):
            await cli.connect(login=True)
            fn = getattr(cli, "noise_encryption_set_key", None)
            if fn is None:
                return ("API-Schlüssel nicht automatisch übertragen – bitte in der Adapter-Weboberfläche unter "
                        "System & Update → Home Assistant „Schlüssel eintragen“")
            ok = await fn(raw)
            return ("API-Schlüssel übertragen – Home Assistant bleibt verbunden" if ok else
                    "Adapter hat den Schlüssel abgelehnt – in der Weboberfläche eintragen")
    except TimeoutError:
        return None
    except Exception as err:  # noqa: BLE001
        n = type(err).__name__
        if "Encryption" in n or n == "InvalidEncryptionKeyAPIError":
            return "Adapter hat schon einen Schlüssel – nichts zu übertragen"
        return None
    finally:
        try:
            await cli.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def _esphome_device_info(hass: HomeAssistant, host: str, port: int, key: str):
    try:
        from aioesphomeapi import APIClient, InvalidEncryptionKeyAPIError
    except ImportError as err:  # pragma: no cover
        raise RuntimeError("ESPHome-Integration fehlt in Home Assistant") from err
    from homeassistant.components import zeroconf

    zc = await zeroconf.async_get_instance(hass)
    cli = APIClient(host, port, None, noise_psk=key, zeroconf_instance=zc)
    try:
        async with asyncio.timeout(25):
            await cli.connect(login=True)
            return await cli.device_info()
    except InvalidEncryptionKeyAPIError as err:
        raise RuntimeError("Der Schlüssel passt nicht zum Adapter. Im Adapter unter System & Update → Home Assistant "
                           "den Schlüssel prüfen bzw. erst einen erzeugen und dann kopieren.") from err
    except TimeoutError as err:
        raise RuntimeError(f"Adapter unter {host} antwortet nicht (Port {port}) – Adresse und WLAN prüfen") from err
    except Exception as err:  # noqa: BLE001
        n = type(err).__name__
        if "Encryption" in n or "Handshake" in n:
            raise RuntimeError("Verschlüsselte Verbindung abgelehnt – Schlüssel im Adapter prüfen bzw. neu erzeugen "
                               f"({n})") from err
        raise RuntimeError(f"Adapter unter {host} nicht erreichbar ({n}: {err})") from err
    finally:
        try:
            await cli.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def _esphome_create_entry(hass: HomeAssistant, host: str, port: int, key: str, mac: str):
    """ESPHome-Eintrag über den normalen Einrichtungs-Ablauf von Home Assistant anlegen."""
    res = await hass.config_entries.flow.async_init(ESPHOME, context={"source": "user"}, data={"host": host, "port": port})
    for _ in range(5):
        t = str(res.get("type"))
        if t.endswith("create_entry"):
            e = res.get("result")
            if e is not None:
                return e
            break
        if t.endswith("abort"):
            if res.get("reason") == "already_configured" and mac:
                for e in hass.config_entries.async_entries(ESPHOME):
                    if e.unique_id == mac:
                        hass.config_entries.async_update_entry(e, data={**e.data, "host": host, "port": port, "noise_psk": key})
                        await hass.config_entries.async_reload(e.entry_id)
                        return e
            raise RuntimeError(f"Home Assistant hat das Einbinden abgebrochen ({res.get('reason')})")
        if t.endswith("form"):
            step = res.get("step_id")
            if step == "encryption_key":
                res = await hass.config_entries.flow.async_configure(res["flow_id"], {"noise_psk": key})
                continue
            errs = res.get("errors") or {}
            hass.config_entries.flow.async_abort(res["flow_id"])
            raise RuntimeError(f"Home Assistant fragt im Schritt „{step}“ nach weiteren Angaben"
                               + (f" ({errs})" if errs else "") + " – bitte unter Einstellungen → Geräte & Dienste → ESPHome einbinden.")
        break
    for e in hass.config_entries.async_entries(ESPHOME):
        if (mac and e.unique_id == mac) or e.data.get("host") == host:
            return e
    raise RuntimeError("ESPHome-Eintrag wurde nicht angelegt")


def config_from_found(f: dict[str, Any]) -> dict[str, Any]:
    if f.get("kind") == ESPHOME:
        return {"kind": ESPHOME, "device_id": f.get("device_id", ""), "node": f.get("node", ""), "title": f.get("title", ""), "host": ""}
    return {"kind": BSBLAN, "host": f.get("host", ""), "title": f.get("title", ""), "device_id": "", "node": "",
            "passkey": f.get("passkey", ""), "username": f.get("username", ""), "password": f.get("password", "")}


# =====================================================================
# Eigenes Gerät „ESP-Bus-Adapter“ (Karte unter Geräte & Dienste)
# =====================================================================
async def async_setup_bsb_entry(hass: HomeAssistant, entry) -> bool:
    from homeassistant.const import Platform

    from .const import VENDOR_BSB
    from .panel import async_setup_panel

    mgr = await async_setup_panel(hass, dict(entry.options), VENDOR_BSB)
    a = async_setup(hass, mgr)
    data = {k: v for k, v in entry.data.items() if k in DEFAULTS}
    if data.get("kind") and not a.cfg.get("kind"):
        await a.async_save_cfg(data)
        if not a.st.get("ident"):
            await a.async_identify()

    async def _updated(hass_: HomeAssistant, entry_) -> None:
        from .const import CONF_ADMIN_ONLY, CONF_SHOW_IN_SIDEBAR
        from .ehs import async_register_bsb_dashboard

        await async_setup_panel(hass_, dict(entry_.options), VENDOR_BSB)
        await async_register_bsb_dashboard(
            hass_, show_in_sidebar=entry_.options.get(CONF_SHOW_IN_SIDEBAR, True), admin_only=entry_.options.get(CONF_ADMIN_ONLY, False)
        )

    entry.async_on_unload(entry.add_update_listener(_updated))
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    from .const import CONF_ADMIN_ONLY, CONF_SHOW_IN_SIDEBAR
    from .ehs import async_register_bsb_dashboard

    await async_register_bsb_dashboard(
        hass, show_in_sidebar=entry.options.get(CONF_SHOW_IN_SIDEBAR, True), admin_only=entry.options.get(CONF_ADMIN_ONLY, False)
    )
    from .ehs import async_setup_hub

    await async_setup_hub(hass, entry)   # „System & Panel“ immer (v1.20.3)
    return True


async def async_unload_bsb_entry(hass: HomeAssistant, entry) -> bool:
    from homeassistant.const import Platform

    from .const import VENDOR_BSB
    from .panel import async_remove_panel

    ok = await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])
    from .ehs import async_remove_bsb_dashboard

    async_remove_bsb_dashboard(hass)
    async_remove_panel(hass, VENDOR_BSB)
    from .ehs import async_release_hub

    async_release_hub(hass, entry)
    return ok


# =====================================================================
# Automatische Erkennung (wie bei EMS-ESP: „Neues Gerät gefunden“)
# =====================================================================
@callback
def async_setup_discovery(hass: HomeAssistant) -> None:
    from homeassistant.helpers.event import async_call_later
    from homeassistant.helpers.start import async_at_started

    async def _check(_now=None) -> None:
        from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
        from homeassistant.helpers import discovery_flow

        from .const import DOMAIN, VENDOR_BSB, entry_vendor

        if any(entry_vendor(e) == VENDOR_BSB for e in hass.config_entries.async_entries(DOMAIN, include_ignore=True)):
            return
        found = [f for f in find_esphome(hass) if f.get("has_get") or f.get("bsb_entities")] + find_bsblan_entries(hass)
        if not found:
            return
        f = found[0]
        discovery_flow.async_create_flow(
            hass, DOMAIN, context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={k: v for k, v in f.items() if k in ("kind", "id", "device_id", "node", "title", "host", "passkey", "username", "password", "has_get")},
        )

    @callback
    def _started(_hass: HomeAssistant) -> None:
        # ESPHome-Geräte verbinden sich nach dem Start – kurz warten, dann alle 30 min erneut
        async_call_later(hass, 90, _check)
        async_track_time_interval(hass, _check, timedelta(minutes=30))

    async_at_started(hass, _started)
