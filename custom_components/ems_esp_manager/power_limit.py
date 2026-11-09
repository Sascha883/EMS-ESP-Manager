"""EMS-Regelung → Leistungsbegrenzung (Dimmung der Bosch-Wärmepumpe über EMS-ESP).

Two ways to make the heat pump reduce its power, both only over the EMS bus:

* ``input`` – the external input N of the heat pump is configured (once, at the
  UI 800) as power limitation / EVU input and stays unwired.  The manager flips
  the *invert* bit of that input (EMS-ESP ``boiler/hpinNopt`` with a single
  ``1``/``0`` → telegram 0x486).  The heat pump then sees the open contact as
  "active" and uses its own dimming logic.  This is a setting stored in the
  controller, so it is not switched more often than every ``min_gap`` seconds.
* ``limit`` – the power limit in W is written directly (EMS-ESP
  ``boiler/hppowerlimit`` → telegram 0x4A7).  Not every heat pump answers it.

The test (button in the panel) reads everything that matters, requests the raw
telegrams 0x486 / 0x484 / 0x4A7, writes each way once, watches the readback and
the compressor power, puts everything back and writes a log that is shown in
the panel (and can be copied), so a remote plant can be checked without access.

Settings and state live in the panel store (``plim`` / ``plim_state``).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import re
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import VENDOR_BOSCH as VENDOR_BOSCH  # v1.24.0: eigener Speicherblock (mgr.vdata)

_LOGGER = logging.getLogger(__name__)
_DATA = "ems_esp_manager_plim"

DEVICE = "boiler"
MAX_LOG = 400
MAX_LEARN_LOG = 600

DEFAULTS: dict[str, Any] = {
    "method": "input",        # input | limit
    "input": 1,               # external input 1..4 (1 recommended, see EMS-ESP offset 27)
    "limit_w": 4200,          # W written to hppowerlimit while dimmed
    "trigger_entity": "",     # optional: on = dim, off = normal
    "max_minutes": 120,       # automatic end of a manual dimming (0 = never)
    "min_gap": 300,           # s between two writes of the input setting (controller memory)
    "observe_s": 60,          # s the test keeps each way active
}

# values read for the test / status (EMS-ESP short names)
WATCH = (
    "hpin1", "hpin2", "hpin3", "hpin4",
    "hpin1opt", "hpin2opt", "hpin3opt", "hpin4opt",
    "hppowerlimit", "hpmaxpower", "powerreduction", "pvmaxcomp", "maxheatcomp",
    "hpcurrpower", "hppower", "hpcompspd", "hpcompon", "hpactivity",
)
WRITE_KEYS = ("hpin1opt", "hpin2opt", "hpin3opt", "hpin4opt", "hppowerlimit", "hpmaxpower", "powerreduction", "pvmaxcomp", "maxheatcomp")
UNITS = {"hpcurrpower": "W", "hppowerlimit": "W", "hppower": "kW", "hpmaxpower": "%", "powerreduction": "%",
         "pvmaxcomp": "kW", "hpcompspd": "%"}
POWER_KEYS = ("hpcurrpower", "hppower", "hpcompspd", "hpcompon", "hpactivity")

# hpinNopt: inputs 1-3 "<inv><evu1><evu2><evu3><comp><aux><cool><heat><dhw><pv><prot><pres><mod>"
#           input 4   "<inv><comp><aux><cool><heat><dhw><pv><prot><pres><mod>"
BITS_123 = ("inv", "evu1", "evu2", "evu3", "comp", "aux", "cool", "heat", "dhw", "pv", "prot", "pres")
BITS_4 = ("inv", "comp", "aux", "cool", "heat", "dhw", "pv", "prot", "pres")
BIT_TEXT = {
    "inv": "Eingang invertiert",
    "evu1": "EVU-Sperrzeit 1", "evu2": "EVU-Sperrzeit 2", "evu3": "EVU-Sperrzeit 3",
    "comp": "Kompressor sperren", "aux": "Zuheizerbetr. sperren", "cool": "Kühlbetrieb sperren",
    "heat": "Heizbetrieb sperren", "dhw": "Warmwasser sperren", "pv": "Photovoltaikanlage",
    "prot": "Überhitzungsschutz", "pres": "Druckschalter",
}
# Menüpunkte je Eingang, wie sie das UI 800 an der CS5800i zeigt (Fotos + Bus-Log 04.11.2026).
# Jeder Punkt = 1 Byte in 0x486, das UI 800 schreibt ihn einzeln, die WP bestätigt per Broadcast.
UI_MENU = {1: ("inv", "aux", "evu1"), 2: ("inv", "aux", "heat"), 3: ("inv", "aux", "prot"), 4: ("inv", "aux", "pv")}
# 0x486-Offsets (EMS-ESP boiler.cpp, bestätigt im Bus-Log 04.11.2026): Eingang 1..3 = Basis + (n-1)
OFF_123 = {"inv": 0, "evu1": 3, "evu2": 6, "evu3": 9, "comp": 12, "aux": 15, "cool": 18,
           "heat": 21, "dhw": 24, "pres": 30, "prot": 36, "pv": 39}
OFF_4 = {"inv": 42, "comp": 43, "aux": 44, "cool": 45, "heat": 46, "dhw": 47, "pres": 49, "prot": 50, "pv": 52}


def bit_offset(n: int, bit: str) -> int:
    """0x486-Offset der Funktion ``bit`` an Eingang n."""
    return OFF_4[bit] if n == 4 else OFF_123[bit] + n - 1
# Dimm-Testprogramm: Schritte, die nacheinander an einem freien Eingang ausprobiert werden
DIM_STEPS: dict[str, dict[str, Any]] = {
    "evu1": {"label": "EVU-Sperrzeit 1", "bits": ("evu1",)},
    "evu2": {"label": "EVU-Sperrzeit 2", "bits": ("evu2",)},
    "evu3": {"label": "EVU-Sperrzeit 3", "bits": ("evu3",)},
    "comp": {"label": "Kompressor sperren", "bits": ("comp",)},
    "heat": {"label": "Heizbetrieb sperren", "bits": ("heat",)},
    "dhw": {"label": "Warmwasser sperren", "bits": ("dhw",)},
    "pv": {"label": "Photovoltaikanlage (PV max. Kompressorleistung)", "bits": ("pv",)},
    "mod": {"label": "Begrenzungswert (ohne Funktion)", "bits": (), "mod": True},
}
DIM_DEFAULTS: dict[str, Any] = {
    "input": 2,             # freier Eingang (keine Funktion, nicht verdrahtet)
    "steps": ["evu1", "evu2", "evu3", "comp", "heat"],
    "pr": 30,               # Leistungsverringerung (%) während des Programms, 0 = nicht ändern
    "active_s": 300,        # s je Schritt aktiv
    "base_s": 60,           # s Grundlast messen
    "wait_run_s": 600,      # s max. warten, bis der Kompressor läuft
    "recover_s": 900,       # s max. Erholung nach einem Schritt
    "mod": 50,              # Begrenzungswert für Schritt „mod“
}
DIM_LIMITS = {"input": (1, 4), "active_s": (60, 1800), "base_s": (30, 300), "wait_run_s": (0, 3600),
              "recover_s": (60, 3600), "mod": (0, 100), "pr": (0, 60)}
EVU_STEPS = ("evu1", "evu2", "evu3")
RUN_W = 300   # ab dieser Leistung gilt der Kompressor als laufend

_RE_TEL = re.compile(r"\(0x0?(486|484|4A7)\)|\b(?:0x)?0?(486|484|4A7)\b", re.I)


def err_text(err: BaseException | None) -> str:
    """Readable error – a timeout has an empty str()."""
    if err is None:
        return "unbekannter Fehler"
    txt = str(err).strip()
    if not txt or isinstance(err, (TimeoutError, asyncio.TimeoutError)) or "timeout" in type(err).__name__.lower():
        txt = (txt + " – " if txt else "") + "Zeitüberschreitung, EMS-ESP antwortet nicht (15 s)"
    return f"{type(err).__name__}: {txt}"


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("on", "true", "1", "an", "ein"):
        return True
    if s in ("off", "false", "0", "aus"):
        return False
    return None


def decode_opt(opt: Any, n: int) -> dict[str, Any] | None:
    """hpinNopt string -> {bit: bool, ..., mod: int|None}; None if not readable."""
    s = str(opt or "").strip()
    names = BITS_4 if n == 4 else BITS_123
    if len(s) < len(names) or any(c not in "01" for c in s[: len(names)]):
        return None
    out: dict[str, Any] = {k: s[i] == "1" for i, k in enumerate(names)}
    rest = s[len(names):]
    out["mod"] = int(rest) if rest.isdigit() else None
    return out


def opt_text(d: dict[str, Any] | None) -> str:
    if d is None:
        return "nicht lesbar"
    on = [BIT_TEXT[k] for k in BIT_TEXT if d.get(k) and k != "inv"]
    txt = ", ".join(on) if on else "keine Funktion eingestellt"
    if d.get("mod"):
        txt += f", Begrenzungswert {d['mod']}"
    return txt + (" · invertiert" if d.get("inv") else "")


class PowerLimit:
    def __init__(self, hass: HomeAssistant, mgr) -> None:
        self.hass = hass
        self.mgr = mgr
        self._unsub: list = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.live: dict[str, Any] = {}
        self._meta: dict[str, dict] = {}
        self._ltask: asyncio.Task | None = None
        self._learn_stop: asyncio.Event | None = None

    # ------------------------------------------------------------- storage
    @property
    def cfg(self) -> dict[str, Any]:
        return {**DEFAULTS, **(self.mgr.vdata(VENDOR_BOSCH).get("plim") or {})}

    @property
    def st(self) -> dict[str, Any]:
        return self.mgr.vdata(VENDOR_BOSCH).setdefault("plim_state", {})

    async def async_save_cfg(self, cfg: dict[str, Any]) -> None:
        clean = {k: cfg[k] for k in DEFAULTS if k in cfg}
        if "input" in clean:
            clean["input"] = min(4, max(1, int(clean["input"])))
        if "method" in clean and clean["method"] not in ("input", "limit"):
            clean.pop("method")
        self.mgr.vdata(VENDOR_BOSCH)["plim"] = {**self.cfg, **clean}
        await self.mgr.async_save()

    def snapshot(self) -> dict[str, Any]:
        st = dict(self.st)
        st["testing"] = bool(self._task and not self._task.done())
        st["learning"] = self.learning
        return {"config": self.cfg, "dimprog": self.dcfg, "state": st, "live": self.live}

    @property
    def dcfg(self) -> dict[str, Any]:
        return {**DIM_DEFAULTS, **(self.mgr.vdata(VENDOR_BOSCH).get("dimprog") or {})}

    async def async_save_dcfg(self, cfg: dict[str, Any]) -> None:
        clean: dict[str, Any] = {}
        for k, dv in DIM_DEFAULTS.items():
            if k not in cfg:
                continue
            v = cfg[k]
            if k == "steps":
                clean[k] = [x for x in v if x in DIM_STEPS] if isinstance(v, list) else dv
            else:
                lo, hi = DIM_LIMITS[k]
                clean[k] = min(hi, max(lo, int(v)))
        self.mgr.vdata(VENDOR_BOSCH)["dimprog"] = {**self.dcfg, **clean}
        await self.mgr.async_save()

    # ------------------------------------------------------------- timing
    @callback
    def start(self) -> None:
        self._ticking = False
        self._unsub.append(async_track_time_interval(self.hass, self._tick_cb, timedelta(seconds=30)))
        self._unsub.append(async_call_later(self.hass, 45, self._tick_cb))

    @callback
    def stop(self) -> None:
        while self._unsub:
            self._unsub.pop()()
        for task in (self._task, self._ltask):
            if task and not task.done():
                task.cancel()

    async def _tick_cb(self, _now=None) -> None:
        try:
            await self.async_tick()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Leistungsbegrenzung")

    # ------------------------------------------------------------- EMS-ESP
    def _rt(self):
        from homeassistant.config_entries import ConfigEntryState

        from .const import ems_entries

        for e in ems_entries(self.hass):
            if e.state is ConfigEntryState.LOADED:
                return e.runtime_data
        return None

    def _api(self):
        rt = self._rt()
        if rt is None:
            raise RuntimeError("Kein EMS-ESP verbunden")
        return rt.api

    async def _get(self, path: str) -> Any:
        """GET with one retry – the ESP32 is sometimes busy for a moment."""
        api = self._api()
        last: Exception | None = None
        for attempt in range(2):
            try:
                status, data = await api._request("GET", path)  # noqa: SLF001
            except Exception as err:  # noqa: BLE001
                last = err
                await asyncio.sleep(2)
                continue
            if status == 200 and isinstance(data, dict) and "message" not in data:
                return data
            last = RuntimeError(f"{path}: HTTP {status} {data if isinstance(data, (str, dict)) else ''}".strip())
            if attempt == 0:
                await asyncio.sleep(1)
        raise RuntimeError(f"{path}: {err_text(last)}")

    async def _meta_for(self, keys: list[str]) -> None:
        """Writeable flag per value – one small GET /api/boiler/<key> each, cached."""
        for key in keys:
            if key in self._meta:
                continue
            try:
                info = await self._get(f"/api/{DEVICE}/{key}")
                self._meta[key] = {"w": bool(info.get("writeable")), "u": info.get("uom")}
            except Exception:  # noqa: BLE001 – unknown, writing will show it
                self._meta[key] = {"w": None, "u": None}

    async def read(self, meta: bool = False) -> dict[str, dict]:
        """Values of WATCH from EMS-ESP → {short: {v, w, u}} (missing = not delivered).

        Uses /api/boiler/values (light, also used by the 30-s poll). The full
        /api/boiler/entities took longer than 15 s on Sascha's CS5800i (25.10.).
        """
        try:
            data = await self._get(f"/api/{DEVICE}/values")
        except Exception as err:  # noqa: BLE001 – fall back to single values
            import aiohttp

            if isinstance(err, (TimeoutError, aiohttp.ClientConnectionError)):
                raise   # Gateway nicht erreichbar: 18 weitere Abfragen à 15 s bringen nichts
            data = {}
            for key in WATCH:
                try:
                    info = await self._get(f"/api/{DEVICE}/{key}")
                except Exception:  # noqa: BLE001 – not delivered / unknown command
                    continue
                if "value" in info:
                    data[key] = info["value"]
                    self._meta.setdefault(key, {"w": bool(info.get("writeable")), "u": info.get("uom")})
            if not data:
                raise
        if meta:
            await self._meta_for([k for k in WRITE_KEYS if k in data])
        out: dict[str, dict] = {}
        for key in WATCH:
            if key in data and not isinstance(data[key], dict):
                m = self._meta.get(key) or {}
                out[key] = {"v": data[key], "w": m.get("w", key in WRITE_KEYS), "u": m.get("u") or UNITS.get(key)}
        self.live = {k: v.get("v") for k, v in out.items()}
        self.live["t"] = time.time()
        return out

    async def write(self, cmd: str, value: Any) -> None:
        await self._api().command(DEVICE, cmd, value)

    # ------------------------------------------------------------- 0x486 über den Bus bestätigen
    async def _bus_on(self, rt) -> int:
        """Telegramme ins EMS-ESP-Log holen (watch on, Log-Level 5). Gibt den alten Log-Level zurück."""
        old = rt.log_level
        await rt.api.command("system", "watch", rt.watch_mode if rt.watch_mode in ("on", "raw") else "on")
        if rt.log_level < 5:
            await rt.api.set_log_level(5)
        await asyncio.sleep(0.3)
        return old

    async def _bus_off(self, rt, old_level: int | None) -> None:
        try:
            if rt.watch_mode == "off":
                await rt.api.command("system", "watch", "off")
            if old_level is not None and old_level < 5:
                await rt.api.set_log_level(old_level)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("restoring log settings failed", exc_info=True)

    @staticmethod
    def _hp_listener(off: int, sink: list):
        """Listener: Telegramme der WP (0x08) mit 0x486, die Offset ``off`` enthalten → sink (Zeile, Wert)."""
        from .input_learn import parse_line

        def _listen(line: str) -> None:
            tel = parse_line(line)
            if tel is None or tel.type != 0x486 or tel.src != 0x08 or not tel.data:
                return
            if tel.offset <= off < tel.offset + len(tel.data):
                sink.append((line.strip(), tel.data[off - tel.offset]))
        return _listen

    async def _read_bit_bus(self, rt, off: int, timeout: float = 5.0) -> int | None:
        """Ein Byte aus 0x486 direkt bei der WP lesen (system/read 08 486 off 1)."""
        got: list = []
        lis = self._hp_listener(off, got)
        rt.log.listeners.append(lis)
        try:
            await rt.api.command("system", "read", f"08 486 {off} 1")
            t0 = time.time()
            while time.time() - t0 < timeout and not got:
                await asyncio.sleep(0.3)
        except Exception:  # noqa: BLE001 – dann gilt der EMS-ESP-Wert
            pass
        finally:
            if lis in rt.log.listeners:
                rt.log.listeners.remove(lis)
        return got[-1][1] if got else None

    async def _write_bit_bus(self, rt, n: int, bit: str, on: bool, timeout: float = 10.0) -> tuple[bool | None, list[str]]:
        """Eine Funktion schreiben (EMS-ESP hpinNopt mit x-Maske = genau 1 Byte in 0x486, wie das UI 800)
        und auf das eigene 0x486-Telegramm der WP warten.

        Rückgabe: True = WP meldet den neuen Wert, False = WP meldet einen anderen Wert,
        None = keine Antwort; dazu die gesehenen Bus-Zeilen.
        """
        off = bit_offset(n, bit)
        want = 1 if on else 0
        seen: list = []
        lis = self._hp_listener(off, seen)
        names = BITS_4 if n == 4 else BITS_123
        rt.log.listeners.append(lis)
        try:
            await self.write(f"hpin{n}opt", ("x" * names.index(bit)) + ("1" if on else "0"))
            t0, asked = time.time(), False
            while time.time() - t0 < timeout:
                await asyncio.sleep(0.5)
                if any(v == want for _, v in seen):
                    break
                if not asked and time.time() - t0 > 3:
                    asked = True   # keine Bestätigung von selbst: gezielt nachfragen
                    try:
                        await rt.api.command("system", "read", f"08 486 {off} 1")
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            if lis in rt.log.listeners:
                rt.log.listeners.remove(lis)
        lines = list(dict.fromkeys(x for x, _ in seen))
        if any(v == want for _, v in seen):
            return True, lines
        return (False if seen else None), lines

    async def _inv(self, n: int) -> bool | None:
        vals = await self.read()
        d = decode_opt((vals.get(f"hpin{n}opt") or {}).get("v"), n)
        return None if d is None else d["inv"]

    # ------------------------------------------------------------- switching
    async def async_set(self, on: bool, why: str = "manuell", force: bool = False) -> str:
        """Dim (on) or end dimming (off). Returns a short status text."""
        if self._task and not self._task.done():
            raise RuntimeError("Ein Test läuft gerade – bitte warten oder Test abbrechen")
        async with self._lock:
            return await self._set(on, why, force)

    async def _set(self, on: bool, why: str, force: bool) -> str:
        cfg, st = self.cfg, self.st
        now = time.time()
        if on and st.get("active"):
            return "schon aktiv"
        if not on and not st.get("active"):
            return "nicht aktiv"
        if on:
            method = cfg["method"]
            if method == "input":
                n = int(cfg["input"])
                last = float(st.get("last_write") or 0)
                if not force and now - last < cfg["min_gap"]:
                    msg = f"wartet – Eingang zuletzt vor {int(now - last)} s geschaltet (min. {cfg['min_gap']} s)"
                    st["last"] = f"{_hm_now()}: {msg}"
                    return msg
                inv = await self._inv(n)
                if inv is None:
                    raise RuntimeError(f"hpin{n}opt nicht lesbar – liefert EMS-ESP den Wert?")
                if inv:
                    # offener Kontakt + invertiert = Funktion schon aktiv; Umdrehen würde sie AUSschalten
                    raise RuntimeError(f"Eingang {n} ist bereits invertiert – bei offenem Kontakt ist seine Funktion damit "
                                       f"dauerhaft aktiv. Erst unter Externer Eingang {n} „Eingang invertiert“ auf Nein stellen.")
                try:
                    await self.write(f"hpin{n}opt", "1")
                except Exception:
                    # Zeitüberschreitung, aber evtl. übernommen: nachlesen statt beim nächsten Tick zurückzudrehen
                    if (await self._inv(n)) is not True:
                        raise
                st["prev"] = {"method": "input", "input": n, "inv": inv}
                st["last_write"] = now
                what = f"Eingang {n} invertiert"
            else:
                vals = await self.read()
                cur = _f((vals.get("hppowerlimit") or {}).get("v"))
                if "hppowerlimit" not in vals:
                    raise RuntimeError("hppowerlimit wird von dieser Anlage nicht geliefert")
                if cur is None:   # sonst würde „zurücksetzen“ später 0 W schreiben
                    raise RuntimeError("hppowerlimit nicht lesbar – Dimmung nicht gestartet")
                await self.write("hppowerlimit", int(cfg["limit_w"]))
                st["prev"] = {"method": "limit", "value": cur if cur is not None else 0}
                what = f"Leistungsgrenze {int(cfg['limit_w'])} W"
            until = now + cfg["max_minutes"] * 60 if cfg["max_minutes"] and why == "manuell" else None
            st.update(active=True, since=dt_util.now().isoformat(), until=until, why=why,
                      last=f"{_hm_now()}: Dimmung an ({what}, {why})")
            self._log_add(f"Dimmung AN: {what} ({why})")
            _LOGGER.info("Leistungsbegrenzung an: %s (%s)", what, why)
        else:
            prev = st.get("prev") or {}
            await self._restore(prev)
            st["last_write"] = now if prev.get("method") == "input" else st.get("last_write")
            st.update(active=False, prev=None, until=None, why=None,
                      last=f"{_hm_now()}: Dimmung aus ({why})")
            self._log_add(f"Dimmung AUS: {prev or '-'} zurückgeschrieben ({why})")
            _LOGGER.info("Leistungsbegrenzung aus (%s)", why)
        await self.mgr.async_save()
        return st["last"]

    async def async_set_input(self, n: int, bit: str | None = None, on: bool | None = None,
                              mod: int | None = None) -> str:
        """Change the function of external input n (hpinNopt) – one bit or the mod value.

        EMS-ESP writes only the characters '0'/'1' of the option string, an 'x'
        leaves that bit alone; a single character changes only the invert bit.
        The mod value is written to offset 27 for inputs 1-3 (EMS-ESP), so it is
        only offered for input 1 (and input 4, offset 48).
        """
        n = int(n)
        if n not in (1, 2, 3, 4):
            raise ValueError("Eingang 1–4")
        names = BITS_4 if n == 4 else BITS_123
        if self._task and not self._task.done():
            raise RuntimeError("Ein Test läuft gerade – bitte warten oder Test abbrechen")
        async with self._lock:
            st = self.st
            if bit is not None:
                if bit not in names:
                    raise ValueError(f"unbekannte Funktion {bit}")
                prev = st.get("prev") or {}
                if bit == "inv" and st.get("active") and prev.get("method") == "input" and int(prev.get("input", 0)) == n:
                    raise RuntimeError("Die Dimmung nutzt gerade diesen Eingang – erst Dimmung ausschalten")
                idx = names.index(bit)
                value = ("x" * idx) + ("1" if on else "0")
                what = f"{BIT_TEXT.get(bit, bit)} {'ein' if on else 'aus'}"
                check = lambda v: (decode_opt(v, n) or {}).get(bit) == bool(on)  # noqa: E731
            elif mod is not None:
                if n not in (1, 4):
                    raise RuntimeError("Begrenzungswert nur für Eingang 1 und 4 (EMS-ESP schreibt sonst in Eingang 1)")
                mod = max(0, min(255, int(mod)))
                value = "x" * len(names) + str(mod)
                what = f"Begrenzungswert {mod}"
                check = lambda v: (decode_opt(v, n) or {}).get("mod") == mod  # noqa: E731
            else:
                raise ValueError("nichts zu ändern")
            rt = self._rt()
            if bit is not None and rt is not None and rt.log is not None:
                # wie das UI 800: 1 Byte schreiben, Bestätigung der WP (0x486-Telegramm) abwarten
                off = bit_offset(n, bit)
                old = await self._bus_on(rt)
                try:
                    res, _lines = await self._write_bit_bus(rt, n, bit, bool(on))
                finally:
                    await self._bus_off(rt, old)
                st["last_write"] = time.time()
                try:
                    await self.read()   # Anzeige aktualisieren
                except Exception:  # noqa: BLE001
                    pass
                ok = res is True
                msg = (f"Eingang {n}: {what} → "
                       + {True: f"übernommen (WP bestätigt 0x486 Offset {off})",
                          False: f"NICHT übernommen (WP meldet für 0x486 Offset {off} den alten Wert)",
                          None: f"keine Bestätigung der WP (0x486 Offset {off})"}[res])
            else:
                await self.write(f"hpin{n}opt", value)
                st["last_write"] = time.time()
                got = await self._wait_value(f"hpin{n}opt", check, timeout=20)
                ok = check(got)
                msg = f"Eingang {n}: {what} → {'übernommen' if ok else 'NICHT übernommen'} ({got})"
            self._log_add(msg)
            st["last"] = f"{_hm_now()}: {msg}"
            await self.mgr.async_save()
            if not ok:
                raise RuntimeError(msg + " – „Funktionen testen“ im Menü Externer Eingang zeigt die Telegramme.")
            return msg

    async def _restore(self, prev: dict | list) -> None:
        if isinstance(prev, list):   # Eingangstest: offene Punkte
            for p in prev:
                await self._restore(p)
            return
        if prev.get("method") == "value":
            await self.write(str(prev["key"]), prev.get("value"))
        elif prev.get("method") == "mod":
            await self.write(f"hpin{int(prev['input'])}opt", "x" * len(BITS_4 if int(prev["input"]) == 4 else BITS_123)
                             + str(int(prev.get("value") or 0)))
        elif prev.get("method") == "bit":
            n, bit = int(prev["input"]), str(prev["bit"])
            names = BITS_4 if n == 4 else BITS_123
            await self.write(f"hpin{n}opt", ("x" * names.index(bit)) + ("1" if prev.get("on") else "0"))
        elif prev.get("method") == "input":
            await self.write(f"hpin{int(prev['input'])}opt", "1" if prev.get("inv") else "0")
        elif prev.get("method") == "limit":
            await self.write("hppowerlimit", int(prev.get("value") or 0))

    async def async_tick(self) -> None:
        cfg, st = self.cfg, self.st
        # nie mehrere Ticks gleichzeitig: bei offline-EMS-ESP stauten sich sonst alle 30 s neue Aufgaben
        if (self._task and not self._task.done()) or self._lock.locked() or self._ticking:
            return
        self._ticking = True
        try:
            await self._async_tick(cfg, st)
        finally:
            self._ticking = False

    async def _async_tick(self, cfg: dict, st: dict) -> None:
        # a test interrupted by a restart: put the original values back first
        if st.get("restore"):
            try:
                await self._restore(st["restore"])
                self._log_add(f"Nach Neustart zurückgesetzt: {st['restore']}")
                st["restore"] = None
                st.pop("restore_tries", None)
                await self.mgr.async_save()
            except Exception as err:  # noqa: BLE001
                st["restore_tries"] = int(st.get("restore_tries") or 0) + 1
                st["last"] = f"{_hm_now()}: Zurücksetzen nach Test wartet ({st['restore_tries']}/120): {err}"
                if st["restore_tries"] >= 120:   # ~1 h: aufgeben, Regelung nicht dauerhaft blockieren
                    self._log_add(f"Zurücksetzen aufgegeben: {st['restore']} – bitte am UI 800 prüfen ({err})")
                    _LOGGER.warning("Leistungsbegrenzung: Zurücksetzen nach Test aufgegeben: %s", st["restore"])
                    st["restore"] = None
                    st.pop("restore_tries", None)
                    await self.mgr.async_save()
            return
        try:
            if st.get("active") and st.get("until") and time.time() >= float(st["until"]):
                await self.async_set(False, f"nach {cfg['max_minutes']} min automatisch beendet")
                return
            trig = cfg["trigger_entity"]
            if trig:
                s = self.hass.states.get(trig)
                want = _bool(s.state) if s else None
                # Auslöser unbekannt/entfernt: Dimmung nach 15 min sicher beenden
                if want is None and st.get("active") and str(st.get("why", "")).startswith("Auslöser"):
                    since = st.get("trig_unknown") or time.time()
                    st["trig_unknown"] = since
                    if time.time() - since > 900:
                        st.pop("trig_unknown", None)
                        await self.async_set(False, f"Auslöser {trig} seit 15 min unbekannt")
                        return
                else:
                    st.pop("trig_unknown", None)
                if want is True and not st.get("active"):
                    await self.async_set(True, f"Auslöser {trig}")
                elif want is False and st.get("active") and str(st.get("why", "")).startswith("Auslöser"):
                    await self.async_set(False, f"Auslöser {trig} aus")
        except Exception as err:  # noqa: BLE001
            st["last"] = f"{_hm_now()}: Fehler: {err_text(err)}"
            _LOGGER.warning("Leistungsbegrenzung: %s", err)

    # ------------------------------------------------------------- test
    def _log_add(self, text: str) -> None:
        log = self.st.setdefault("log", [])
        log.append(f"{dt_util.now().strftime('%H:%M:%S')}  {text}")
        if len(log) > MAX_LOG:
            del log[: len(log) - MAX_LOG]

    def async_start_test(self, writes: bool = True) -> bool:
        if (self._task and not self._task.done()) or self.learning:
            return False
        self.st["log"] = []
        self.st["test_result"] = None
        self.st["test_done"] = None
        self.st["test_kind"] = "plim"
        self._log_add("Test angefordert – startet …")
        self._task = self.hass.async_create_background_task(self._test(writes), "ems_esp_manager power limit test")
        return True

    def async_start_input_test(self, n: int | None = None, all_bits: bool = False) -> bool:
        """Test „Externe Eingänge“: jede Funktion einmal umschalten, Bestätigung der WP prüfen, zurückstellen."""
        if (self._task and not self._task.done()) or self.learning:
            return False
        if n is not None and int(n) not in (1, 2, 3, 4):
            n = None
        self.st["log"] = []
        self.st["test_result"] = None
        self.st["test_done"] = None
        self.st["test_kind"] = "inputs"
        self._log_add("Eingangstest angefordert – startet …")
        self._task = self.hass.async_create_background_task(
            self._itest(int(n) if n else None, bool(all_bits)), "ems_esp_manager input test")
        return True

    async def _itest(self, only: int | None, all_bits: bool) -> None:
        L = self._log_add
        st = self.st
        res: dict[str, str] = {}
        rt = None
        old_level: int | None = None
        st["test_started"] = dt_util.now().isoformat()
        try:
            try:
                await asyncio.wait_for(self._lock.acquire(), 60)
            except TimeoutError:
                L("FEHLER: Schaltvorgang blockiert seit 60 s – Test abgebrochen.")
                res["gesamt"] = "blockiert"
                return
            try:
                L("=== Test Externe Eingänge: Funktionen schreiben (Telegramm 0x486) ===")
                await self._t_header(L, plim=False)
                rt = self._rt()
                if rt is None or rt.log is None:
                    L("FEHLER: Kein EMS-ESP bzw. keine Log-Verbindung – ohne Mitschnitt ist keine Bestätigung prüfbar.")
                    res["gesamt"] = "kein EMS-ESP / kein Log"
                    return
                inputs = [only] if only else [1, 2, 3, 4]
                L(f"Umfang: Eingang {', '.join(map(str, inputs))} · "
                  + ("alle Funktionen aus 0x486" if all_bits else "Menüpunkte wie am UI 800"))
                L("Ablauf je Punkt: Ausgangswert lesen → umschalten (1 Byte, wie das UI 800) → Bestätigung der WP "
                  "abwarten (max. 10 s) → 3 s halten → zurückstellen → Bestätigung prüfen.")
                try:
                    vals = await self.read()
                except Exception as err:  # noqa: BLE001
                    L(f"EMS-ESP-Werte nicht lesbar ({err_text(err)}) – Ausgangswerte nur über den Bus.")
                    vals = {}
                old_level = await self._bus_on(rt)
                prev = st.get("prev") or {}
                failed: list[dict] = []   # Zurückstellen nicht bestätigt → bleibt in st["restore"]
                for n in inputs:
                    raw0 = (vals.get(f"hpin{n}opt") or {}).get("v")
                    d = decode_opt(raw0, n)
                    L(f"--- Eingang {n}: vorher {raw0 if raw0 is not None else '–'} → {opt_text(d)} ---")
                    bits = (BITS_4 if n == 4 else BITS_123) if all_bits else UI_MENU[n]
                    for bit in bits:
                        label = f"Eingang {n}: {BIT_TEXT[bit]}"
                        off = bit_offset(n, bit)
                        if (bit == "inv" and st.get("active") and prev.get("method") == "input"
                                and int(prev.get("input", 0)) == n):
                            L(f"{label} (Offset {off}): übersprungen – die Dimmung nutzt diesen Eingang gerade")
                            res[label] = "übersprungen (Dimmung aktiv)"
                            continue
                        cur = await self._read_bit_bus(rt, off)
                        src = "WP direkt"
                        if cur is None and d is not None:
                            cur, src = (1 if d.get(bit) else 0), "EMS-ESP"
                        if cur is None:
                            L(f"{label} (Offset {off}): Ausgangswert nicht lesbar – übersprungen")
                            res[label] = "Ausgangswert nicht lesbar"
                            continue
                        old_on = bool(cur)
                        L(f"{label} (Offset {off}): vorher {'Ja' if old_on else 'Nein'} ({src}) → schreibe {'Nein' if old_on else 'Ja'}")
                        item = {"method": "bit", "input": n, "bit": bit, "on": old_on}
                        st["restore"] = failed + [item]
                        await self.mgr.async_save()
                        try:
                            ok, lines = await self._write_bit_bus(rt, n, bit, not old_on)
                        except Exception as err:  # noqa: BLE001
                            L(f"  Schreiben abgelehnt: {err_text(err)}")
                            res[label] = f"Schreiben abgelehnt ({err_text(err)})"
                            st["restore"] = failed or None
                            continue
                        for x in lines[:3]:
                            L("  BUS " + x)
                        L("  → " + {True: "ANGENOMMEN – WP bestätigt den neuen Wert",
                                    False: "NICHT angenommen – WP meldet den alten Wert",
                                    None: "NICHT bestätigt – keine Antwort der WP"}[ok])
                        if ok:
                            await asyncio.sleep(3)
                        back, blines = await self._write_bit_bus(rt, n, bit, old_on)
                        if back is not True:   # ein zweiter Versuch
                            await asyncio.sleep(2)
                            back, blines = await self._write_bit_bus(rt, n, bit, old_on)
                        for x in blines[:2]:
                            L("  BUS " + x)
                        if back:
                            L(f"  zurückgestellt auf {'Ja' if old_on else 'Nein'} – bestätigt")
                            st["restore"] = failed or None
                        else:
                            failed.append(item)
                            L("  FEHLER: Zurückstellen NICHT bestätigt – der Manager versucht es weiter, bitte am UI 800 prüfen")
                        res[label] = ({True: "angenommen", False: "NICHT angenommen", None: "keine Antwort"}[ok]
                                      + ("" if back else " · Zurückstellen NICHT bestätigt"))
                        await self.mgr.async_save()
                        await asyncio.sleep(1)
                L("--- Endzustand (EMS-ESP) ---")
                try:
                    vals2 = await self.read()
                    for n in inputs:
                        a = (vals.get(f"hpin{n}opt") or {}).get("v")
                        b = (vals2.get(f"hpin{n}opt") or {}).get("v")
                        L(f"Eingang {n}: {b} → {opt_text(decode_opt(b, n))}"
                          + ("" if a is None or a == b else f"  (vorher {a} – ABWEICHUNG)"))
                except Exception as err:  # noqa: BLE001
                    L(f"Endzustand nicht lesbar: {err_text(err)}")
            finally:
                if rt is not None and old_level is not None:
                    await self._bus_off(rt, old_level)
                self._lock.release()
        except asyncio.CancelledError:
            L("Test abgebrochen (Home Assistant wird beendet) – offene Punkte werden nach dem Start zurückgestellt.")
            raise
        except Exception as err:  # noqa: BLE001
            L(f"FEHLER: {err_text(err)}")
            res["gesamt"] = f"Fehler: {err_text(err)}"
        finally:
            L("=== Ergebnis ===")
            for k, txt in res.items():
                L(f"{k}: {txt}")
            acc = sum(1 for v in res.values() if v.startswith("angenommen"))
            tested = sum(1 for v in res.values() if v.startswith(("angenommen", "NICHT", "keine")))
            if tested:
                L(f"{acc} von {tested} Funktionen von der Wärmepumpe übernommen."
                  + (" Alle Funktionen lassen sich aus Home Assistant einstellen." if acc == tested else
                     " Nicht übernommene Funktionen bitte am UI 800 einstellen und dieses Protokoll weitergeben."))
            elif not res:
                L("Keine Funktion getestet.")
            L("=== Ende ===")
            st["test_result"] = res
            st["test_done"] = dt_util.now().isoformat()
            try:
                await self.mgr.async_save()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("saving input test log failed", exc_info=True)

    async def _test(self, writes: bool) -> None:
        L = self._log_add
        res: dict[str, str] = {}
        self.st["test_started"] = dt_util.now().isoformat()
        rt = None
        captured: list[str] = []

        def _listen(line: str) -> None:
            if _RE_TEL.search(line):
                captured.append(line.strip())

        try:
            try:
                await asyncio.wait_for(self._lock.acquire(), 60)
            except TimeoutError:
                L("FEHLER: Schaltvorgang blockiert seit 60 s – Test abgebrochen.")
                res["gesamt"] = "blockiert"
                return
            try:
                L("=== Test Leistungsbegrenzung (Dimmung) ===")
                await self._t_header(L)
                rt = self._rt()
                if rt is None:
                    L("FEHLER: Kein EMS-ESP verbunden – Test abgebrochen.")
                    res["gesamt"] = "kein EMS-ESP"
                    return
                if rt.log is not None:
                    rt.log.listeners.append(_listen)
                try:
                    vals = await self._t_values(L)
                except Exception as err:  # noqa: BLE001 – telegrams are still worth reading
                    L(f"FEHLER beim Lesen der Werte: {err_text(err)}")
                    L("Schreibtests werden ausgelassen, weil der Ausgangszustand nicht bekannt ist.")
                    res["werte"] = f"nicht lesbar ({err_text(err)})"
                    vals, writes = {}, False
                await self._t_telegrams(L, rt, captured)
                if self.st.get("active") and writes:
                    st = self.st
                    L(f"Hinweis: Die Dimmung ist gerade aktiv ({st.get('why') or '?'}, seit {str(st.get('since') or '?')[:16].replace('T', ' ')}) "
                      "– sie wird für den Test beendet und der Ausgangszustand neu gelesen.")
                    try:
                        await self._set(False, "für Funktionstest beendet", True)
                        await asyncio.sleep(5)
                        vals = await self._t_values(L)
                    except Exception as err:  # noqa: BLE001
                        L(f"FEHLER: Dimmung konnte nicht beendet werden ({err_text(err)}) – Schreibtests ausgelassen.")
                        writes = False
                if not writes:
                    L("Schreibtests: ausgelassen.")
                else:
                    res["limit"] = await self._t_limit(L, vals)
                    res["input"] = await self._t_input(L, vals)
                await self._t_telegrams(L, rt, captured, again=True)
            finally:
                self._lock.release()
        except asyncio.CancelledError:
            L("Test abgebrochen (Home Assistant wird beendet).")
            raise
        except Exception as err:  # noqa: BLE001
            L(f"FEHLER: {err_text(err)}")
            res["gesamt"] = f"Fehler: {err_text(err)}"
        finally:
            if rt is not None and rt.log is not None and _listen in rt.log.listeners:
                rt.log.listeners.remove(_listen)
            L("=== Ergebnis ===")
            for k, txt in res.items():
                L(f"{ {'limit': 'Weg B Leistungsgrenze (hppowerlimit)', 'input': 'Weg A Eingang invertieren (hpinNopt)'}.get(k, k)}: {txt}")
            L(self._t_advice(res))
            L("=== Ende ===")
            self.st["test_result"] = res
            self.st["test_done"] = dt_util.now().isoformat()
            try:
                await self.mgr.async_save()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("saving test log failed", exc_info=True)

    async def _t_header(self, L, plim: bool = True) -> None:
        from homeassistant.loader import async_get_integration

        from .const import DOMAIN

        try:
            ver = (await async_get_integration(self.hass, DOMAIN)).version
        except Exception:  # noqa: BLE001
            ver = "?"
        L(f"EMS-ESP Manager {ver} · Home Assistant {getattr(self.hass.config, 'version', '') or ''}".rstrip(" ·"))
        cfg = self.cfg
        if plim:
            L(f"Einstellung: Weg {'A (Eingang ' + str(cfg['input']) + ' invertieren)' if cfg['method'] == 'input' else 'B (Leistungsgrenze ' + str(cfg['limit_w']) + ' W)'}"
              f" · Beobachtung je Weg {cfg['observe_s']} s")
        rt = self._rt()
        if rt is None:
            return
        try:
            status = await rt.api.get_status()
            L(f"EMS-ESP {status.get('emsesp_version') or status.get('version') or '?'}"
              f" · Bus-ID {('0x%02X' % rt.bus_id) if isinstance(getattr(rt, 'bus_id', None), int) else '?'}"
              f" · Tx-Modus {getattr(rt, 'tx_mode', '?')}")
        except Exception as err:  # noqa: BLE001
            L(f"EMS-ESP-Status nicht lesbar: {err}")
        try:
            from .entity_map import ems_devices

            for d in await ems_devices(rt.api, force=True) or []:
                L(f"Gerät: {d.get('type')} · {d.get('name')} · ID {d.get('deviceID')} · Produkt {d.get('productID')} · Version {d.get('version')}")
        except Exception as err:  # noqa: BLE001
            L(f"Geräteliste nicht lesbar: {err}")

    async def _t_values(self, L) -> dict[str, dict]:
        L("--- Werte aus EMS-ESP (/api/boiler/values) ---")
        t0 = time.time()
        vals = await self.read(meta=True)
        L(f"gelesen in {time.time() - t0:.1f} s")
        for key in WATCH:
            v = vals.get(key)
            if v is None:
                L(f"{key:<15} – nicht geliefert")
                continue
            extra = f" {v['u']}" if v.get("u") not in (None, "") else ""
            L(f"{key:<15} = {v['v']}{extra}" + ("" if key not in WRITE_KEYS else f"  ({'schreibbar' if v['w'] else 'nur lesen' if v['w'] is False else 'Schreibrecht unbekannt'})"))
        for n in range(1, 5):
            if f"hpin{n}opt" in vals:
                d = decode_opt(vals[f"hpin{n}opt"]["v"], n)
                L(f"Eingang {n}: {opt_text(d)} · Zustand {vals.get(f'hpin{n}', {}).get('v', '?')}")
        return vals

    async def _t_telegrams(self, L, rt, captured: list[str], again: bool = False) -> None:
        L("--- Rohtelegramme " + ("nach den Tests" if again else "(0x486 Eingänge, 0x484 Leistung, 0x4A7 Grenze)") + " ---")
        keep = rt.watch_mode if rt.watch_mode in ("on", "raw") else "on"
        old_level = rt.log_level
        start = len(captured)
        try:
            await rt.api.command("system", "watch", keep)
            if rt.log_level < 5:
                await rt.api.set_log_level(5)
            await asyncio.sleep(0.3)
            for req in ("08 486 0 27", "08 486 27 27", "08 484 0 27", "08 484 27 27", "08 484 54 12", "08 4A7 0 2"):
                try:
                    await rt.api.command("system", "read", req)
                except Exception as err:  # noqa: BLE001
                    L(f"system/read {req}: {err}")
                await asyncio.sleep(1.5)
            await asyncio.sleep(2)
        except Exception as err:  # noqa: BLE001
            L(f"Mitschnitt nicht möglich: {err}")
        finally:
            try:
                if rt.watch_mode == "off":
                    await rt.api.command("system", "watch", "off")
                if old_level < 5:
                    await rt.api.set_log_level(old_level)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("restoring log settings failed", exc_info=True)
        lines = captured[start:]
        if not lines:
            L("keine Telegramme 0x486/0x484/0x4A7 im Log gesehen (Log-Verbindung? Anlage antwortet nicht?)")
        seen: set[str] = set()
        for line in lines:
            if line not in seen:
                seen.add(line)
                L("BUS " + line)
        if not any("4A7" in x.upper() for x in lines):
            L("0x4A7 (Leistungsgrenze): keine Antwort gesehen")

    async def _wait_value(self, key: str, check, timeout: float = 25) -> Any:
        t0 = time.time()
        v = None
        while time.time() - t0 < timeout:
            await asyncio.sleep(3)
            vals = await self.read()
            v = (vals.get(key) or {}).get("v")
            if check(v):
                return v
        return v

    async def _observe(self, L, label: str, secs: int) -> dict[str, list]:
        L(f"{label}: beobachte {secs} s …")
        seen: dict[str, list] = {k: [] for k in POWER_KEYS}
        t0 = time.time()
        while time.time() - t0 < secs:
            await asyncio.sleep(min(10, max(1, secs - (time.time() - t0))))
            try:
                vals = await self.read()
            except Exception as err:  # noqa: BLE001
                L(f"  Lesen fehlgeschlagen: {err}")
                continue
            row = []
            for k in POWER_KEYS:
                if k in vals:
                    seen[k].append(vals[k]["v"])
                    row.append(f"{k}={vals[k]['v']}")
            ins = " ".join(f"in{n}={vals[f'hpin{n}']['v']}" for n in range(1, 5) if f"hpin{n}" in vals)
            L(f"  +{int(time.time() - t0):>3} s  {' '.join(row)}  {ins}")
        return seen

    def _power_txt(self, before: dict[str, list], during: dict[str, list]) -> str:
        def avg(lst):
            nums = [x for x in (_f(v) for v in lst) if x is not None]
            return sum(nums) / len(nums) if nums else None
        b, d = avg(before.get("hpcurrpower", [])), avg(during.get("hpcurrpower", []))
        if b is None or d is None:
            return "Kompressorleistung nicht vergleichbar"
        if b < 100:
            return f"Kompressor lief nicht (Ø {b:.0f} W) – Wirkung nicht prüfbar"
        drop = b - d
        verdict = "WP hat reagiert" if drop >= max(150, b * 0.15) else "keine Reaktion der WP"
        return f"Kompressorleistung Ø {b:.0f} W → {d:.0f} W ({-drop:+.0f} W) – {verdict}"

    async def _t_limit(self, L, vals: dict[str, dict]) -> str:
        L("--- Weg B: Leistungsgrenze direkt (hppowerlimit, Telegramm 0x4A7) ---")
        info = vals.get("hppowerlimit")
        if info is None:
            L("hppowerlimit wird nicht geliefert → Weg B an dieser Anlage nicht möglich.")
            return "nicht vorhanden"
        if info["w"] is False:
            L("hppowerlimit ist nur lesbar → Weg B nicht möglich.")
            return "nur lesbar"
        v0 = _f(info["v"])
        if v0 is None:   # v1.22.2: sonst würde beim Zurücksetzen 0 W geschrieben
            L(f"hppowerlimit nicht lesbar ({info['v']!r}) → Weg B übersprungen, nichts geschrieben.")
            return "nicht lesbar"
        test = int(self.cfg["limit_w"])
        cur = _f((vals.get("hpcurrpower") or {}).get("v"))
        if cur is not None and cur >= 300 and test >= cur:
            # a limit above the current power would show no effect – test with half the power
            test = max(500, int(round(cur * 0.5, -2)))
            L(f"Hinweis: eingestellte Grenze {int(self.cfg['limit_w'])} W liegt über der aktuellen Leistung ({cur:.0f} W) – Test mit {test} W")
        if v0 is not None and int(v0) == test:
            test = max(0, test - 500)
        L(f"Vorher: {info['v']} W · schreibe {test} W")
        self.st["restore"] = {"method": "limit", "value": v0 or 0}
        await self.mgr.async_save()
        before = await self._observe(L, "Vorher", 10)
        try:
            await self.write("hppowerlimit", test)
        except Exception as err:  # noqa: BLE001
            L(f"Schreiben abgelehnt: {err_text(err)}")
            self.st["restore"] = None
            return f"Schreiben abgelehnt ({err_text(err)})"
        got = await self._wait_value("hppowerlimit", lambda v: _f(v) is not None and int(_f(v)) == test)
        ok = _f(got) is not None and int(_f(got)) == test
        L(f"Rückmeldung: {got} W → {'übernommen' if ok else 'NICHT übernommen'}")
        during = await self._observe(L, "Mit Grenze", int(self.cfg["observe_s"])) if ok else {}
        L("Zurücksetzen …")
        await self.write("hppowerlimit", int(v0 or 0))
        back = await self._wait_value("hppowerlimit", lambda v: _f(v) is not None and int(_f(v)) == int(v0 or 0))
        L(f"Zurückgesetzt auf {back} W")
        self.st["restore"] = None
        await self.mgr.async_save()
        if not ok:
            return "geschrieben, aber nicht übernommen"
        return "übernommen · " + self._power_txt(before, during)

    async def _t_input(self, L, vals: dict[str, dict]) -> str:
        """Weg A: Eingang inaktiv (nicht invertiert) messen, dann aktiv (invertiert, Kontakt offen) messen."""
        n = int(self.cfg["input"])
        L(f"--- Weg A: Eingang {n} per Software aktivieren (hpin{n}opt „Eingang invertiert“, Telegramm 0x486) ---")
        info = vals.get(f"hpin{n}opt")
        if info is None:
            L(f"hpin{n}opt wird nicht geliefert → Weg A an dieser Anlage nicht möglich.")
            return "nicht vorhanden"
        if info["w"] is False:
            L(f"hpin{n}opt ist nur lesbar → Weg A nicht möglich.")
            return "nur lesbar"
        d = decode_opt(info["v"], n)
        if d is None:
            L(f"hpin{n}opt = „{info['v']}“ nicht auswertbar.")
            return "Wert nicht auswertbar"
        L(f"Eingang {n} vorher: {info['v']} → {opt_text(d)}")
        if not any(d.get(k) for k in BIT_TEXT if k != "inv") and not d.get("mod"):
            L(f"Hinweis: Eingang {n} hat keine Funktion – die Wärmepumpe wird nicht reagieren. Für einen echten Dimmtest "
              "das Dimm-Testprogramm verwenden.")
        inv0 = bool(d["inv"])
        self.st["restore"] = {"method": "input", "input": n, "inv": inv0}
        await self.mgr.async_save()
        if inv0:
            L(f"Eingang {n} ist invertiert: bei offenem Kontakt ist die Funktion damit SCHON AKTIV. "
              "Für die Vergleichsmessung wird er zuerst inaktiv geschaltet.")
            await self.write(f"hpin{n}opt", "0")
            got0 = await self._wait_value(f"hpin{n}opt", lambda v: (decode_opt(v, n) or {}).get("inv") is False)
            L(f"Rückmeldung: {got0}")
            L("Inaktiv – warte 60 s, bis sich die Wärmepumpe darauf eingestellt hat …")
            await asyncio.sleep(60)
        before = await self._observe(L, "Eingang INAKTIV", 30)
        try:
            await self.write(f"hpin{n}opt", "1")
        except Exception as err:  # noqa: BLE001
            L(f"Schreiben abgelehnt: {err_text(err)}")
            return f"Schreiben abgelehnt ({err_text(err)})"
        self.st["last_write"] = time.time()
        got = await self._wait_value(f"hpin{n}opt", lambda v: (decode_opt(v, n) or {}).get("inv") is True)
        ok = (decode_opt(got, n) or {}).get("inv") is True
        L(f"Rückmeldung: {got} → Eingang {'AKTIV (invertiert)' if ok else 'NICHT umgeschaltet'}")
        during = await self._observe(L, "Eingang AKTIV", int(self.cfg["observe_s"])) if ok else {}
        L("Zurücksetzen …")
        await self.write(f"hpin{n}opt", "1" if inv0 else "0")
        back = await self._wait_value(f"hpin{n}opt", lambda v: (decode_opt(v, n) or {}).get("inv") == inv0)
        L(f"Zurückgesetzt: {back}" + (" – ACHTUNG: Eingang ist wieder invertiert, die Funktion ist damit dauerhaft aktiv!" if inv0 else ""))
        self.st["restore"] = None
        await self.mgr.async_save()
        if not ok:
            return "geschrieben, aber nicht übernommen"
        acts = [str(v) for v in during.get("hpactivity", [])]
        offs = [v for v in during.get("hpcompon", []) if _bool(v) is False]
        extra = f" · Betriebsart: {', '.join(dict.fromkeys(acts))}" if acts else ""
        if offs and len(offs) >= max(1, len(during.get("hpcompon", [])) // 2):
            return "übernommen · Kompressor AUS – das ist eine Sperre, keine Dimmung" + extra
        return "übernommen · " + self._power_txt(before, during) + extra

    @staticmethod
    def _t_advice(res: dict[str, str]) -> str:
        a, b = res.get("input", ""), res.get("limit", "")
        if b.startswith("übernommen") and "WP hat reagiert" in b:
            return "Empfehlung: Weg B (Leistungsgrenze) verwenden – direkt in W, ohne Einstellungsspeicher zu belasten."
        if a.startswith("übernommen") and "WP hat reagiert" in a:
            return "Empfehlung: Weg A (Eingang invertieren) – die Wärmepumpe hat auf den umgeschalteten Eingang reagiert."
        if b.startswith("übernommen") or a.startswith("übernommen"):
            return ("Werte wurden übernommen, eine Reaktion der Wärmepumpe war aber nicht zu sehen. Test wiederholen, wenn der "
                    "Kompressor läuft (z. B. Warmwasserladung), ggf. „Beobachtung je Weg“ verlängern. Weg A wirkt nur, wenn der "
                    "Eingang am UI 800 auf Leistungsbegrenzung/EVU eingestellt ist.")
        if a.startswith("übernommen"):
            return ("Empfehlung: Weg A (Eingang invertieren). Ob die Wärmepumpe wirklich dimmt, zeigt die Kompressorleistung "
                    "– sie reagiert nur, wenn der Eingang am UI 800 auf Leistungsbegrenzung/EVU eingestellt ist und der Kompressor läuft.")
        if not res:
            return "Keine Schreibtests ausgeführt."
        return "Keiner der beiden Wege hat funktioniert – bitte dieses Protokoll weitergeben."


    # ------------------------------------------------------------- Dimm-Testprogramm
    def async_start_dim_program(self) -> bool:
        """Mehrere Dimm-Varianten nacheinander an einem freien Eingang ausprobieren und die Wirkung messen."""
        if (self._task and not self._task.done()) or self.learning:
            return False
        st = self.st
        st["log"] = []
        st["test_result"] = None
        st["test_done"] = None
        st["test_kind"] = "dimprog"
        st["prog"] = {"step": None, "phase": "start", "until": None}
        self._prog_stop = asyncio.Event()
        self._log_add("Dimm-Testprogramm angefordert – startet …")
        self._task = self.hass.async_create_background_task(self._dimprog(), "ems_esp_manager dim program")
        return True

    def async_stop_program(self) -> bool:
        ev = getattr(self, "_prog_stop", None)
        if ev is None or not (self._task and not self._task.done()):
            return False
        ev.set()
        self._log_add("Abbruch angefordert – stelle zurück …")
        return True

    def _stopped(self) -> bool:
        ev = getattr(self, "_prog_stop", None)
        return bool(ev and ev.is_set())

    async def _p_wait(self, secs: float) -> None:
        t = time.time()
        while time.time() - t < secs and not self._stopped():
            await asyncio.sleep(1)

    async def _p_sample(self, L, t0: float, tag: str, n: int) -> dict[str, Any] | None:
        try:
            vals = await self.read()
        except Exception as err:  # noqa: BLE001
            L(f"  {tag} +{int(time.time() - t0):>4} s  Lesen fehlgeschlagen: {err_text(err)}")
            return None
        g = lambda k: (vals.get(k) or {}).get("v")  # noqa: E731
        smp = {"t": time.time() - t0, "p": _f(g("hpcurrpower")), "spd": _f(g("hpcompspd")),
               "on": _bool(g("hpcompon")), "act": g("hpactivity"), "in": g(f"hpin{n}")}
        L(f"  {tag} +{int(smp['t']):>4} s  P={g('hpcurrpower')} W  Drehzahl={g('hpcompspd')} %  "
          f"Kompressor={g('hpcompon')}  {g('hpactivity')}  Eingang {n}={g(f'hpin{n}')}")
        return smp

    async def _p_observe(self, L, secs: float, tag: str, n: int, until=None) -> list[dict]:
        """Alle ~10 s Werte lesen (das Lesen selbst dauert am ESP32 einige s)."""
        out: list[dict] = []
        t0 = time.time()
        while time.time() - t0 < secs and not self._stopped():
            smp = await self._p_sample(L, t0, tag, n)
            if smp:
                out.append(smp)
                if until and until(smp, out):
                    break
            await self._p_wait(max(0.0, 10 - ((time.time() - t0) % 10)))
        return out

    async def _p_set(self, L, rt, n: int, bit: str, on: bool, label: str) -> bool:
        ok, lines = await self._write_bit_bus(rt, n, bit, on)
        if ok is not True:
            await asyncio.sleep(2)
            ok, lines = await self._write_bit_bus(rt, n, bit, on)
        L(f"  {label} → {'Ja' if on else 'Nein'}: " + ("bestätigt" if ok else "NICHT bestätigt")
          + (f"  [{lines[-1]}]" if lines else ""))
        return ok is True

    @staticmethod
    def _avg(vals: list[float | None]) -> float | None:
        nums = [v for v in vals if v is not None]
        return sum(nums) / len(nums) if nums else None

    async def _dimprog(self) -> None:
        L = self._log_add
        st = self.st
        c = self.dcfg
        n = int(c["input"])
        res: dict[str, str] = {}
        rt = None
        old_level: int | None = None
        orig: dict[str, Any] | None = None
        bus_lines: list[str] = []

        def _bus(line: str) -> None:
            if "(0x04A2)" in line or "HpInput" in line:
                bus_lines.append(line.strip())

        try:
            try:
                await asyncio.wait_for(self._lock.acquire(), 60)
            except TimeoutError:
                L("FEHLER: Schaltvorgang blockiert seit 60 s – abgebrochen.")
                res["gesamt"] = "blockiert"
                return
            try:
                L("=== Dimm-Testprogramm: Wirkung der Eingangsfunktionen auf die Kompressorleistung ===")
                await self._t_header(L, plim=False)
                steps = [s for s in c["steps"] if s in DIM_STEPS]
                if n == 4 and any(s in EVU_STEPS for s in steps):
                    L("Hinweis: Eingang 4 hat keine EVU-Sperrzeiten – diese Schritte werden ausgelassen.")
                    steps = [s for s in steps if s not in EVU_STEPS]
                if "mod" in steps and n not in (1, 4):
                    L("Hinweis: Schritt „Begrenzungswert“ nur an Eingang 1 oder 4 möglich – ausgelassen.")
                    steps.remove("mod")
                L(f"Eingang {n} · Schritte: {', '.join(DIM_STEPS[s]['label'] for s in steps) or 'keine'} · "
                  f"je {c['active_s']} s aktiv, Grundlast {c['base_s']} s, Erholung max. {c['recover_s']} s")
                L("Ablauf je Schritt: warten bis der Kompressor läuft → Grundlast messen → Funktion setzen → Eingang aktivieren "
                  "(invertieren, Kontakt ist offen) → beobachten → Eingang deaktivieren → Funktion zurück → Erholung abwarten.")
                if not steps:
                    res["gesamt"] = "keine Schritte gewählt"
                    return
                rt = self._rt()
                if rt is None or rt.log is None:
                    L("FEHLER: Kein EMS-ESP bzw. keine Log-Verbindung.")
                    res["gesamt"] = "kein EMS-ESP / kein Log"
                    return
                # aktive Dimmung beenden
                if st.get("active"):
                    L("Die Dimmung ist aktiv – sie wird für das Programm beendet.")
                    await self._set(False, "für Dimm-Testprogramm beendet", True)
                    await asyncio.sleep(3)
                old_level = await self._bus_on(rt)
                rt.log.listeners.append(_bus)
                # Ausgangszustand des Eingangs
                vals = await self.read()
                raw = (vals.get(f"hpin{n}opt") or {}).get("v")
                d = decode_opt(raw, n)
                L(f"Eingang {n} vorher: {raw} → {opt_text(d)} · Zustand {(vals.get(f'hpin{n}') or {}).get('v')}")
                if d is None:
                    L(f"FEHLER: hpin{n}opt nicht lesbar – Programm nicht möglich.")
                    res["gesamt"] = "Eingang nicht lesbar"
                    return
                if any(d.get(k) for k in BIT_TEXT if k != "inv"):
                    L(f"FEHLER: Eingang {n} hat schon eine Funktion ({opt_text(d)}). Bitte einen freien, nicht "
                      "verdrahteten Eingang wählen – sonst würde das Programm eine echte Funktion verändern.")
                    res["gesamt"] = "Eingang nicht frei"
                    return
                orig = {"inv": bool(d["inv"]), "mod": d.get("mod")}
                pending = [{"method": "bit", "input": n, "bit": "inv", "on": orig["inv"]}]
                st["restore"] = pending
                await self.mgr.async_save()
                # Leistungsverringerung für die Dauer des Programms
                pr = int(c.get("pr") or 0)
                if pr:
                    pr = max(30, min(60, int(round(pr / 10.0)) * 10))
                    pr0 = _f((vals.get("powerreduction") or {}).get("v"))
                    if pr0 is None:
                        L("Leistungsverringerung (powerreduction) wird nicht geliefert – bleibt unverändert.")
                    elif int(pr0) != pr:
                        orig["pr"] = int(pr0)
                        pending.append({"method": "value", "key": "powerreduction", "value": int(pr0)})
                        st["restore"] = pending
                        await self.mgr.async_save()
                        await self.write("powerreduction", pr)
                        got = await self._wait_value("powerreduction", lambda v: _f(v) is not None and int(_f(v)) == pr, timeout=20)
                        L(f"Leistungsverringerung {int(pr0)} % → {pr} %: "
                          + ("übernommen" if _f(got) is not None and int(_f(got)) == pr else f"NICHT übernommen ({got})"))
                    else:
                        L(f"Leistungsverringerung steht schon auf {pr} %.")
                    st["prog_pr"] = pr
                else:
                    st["prog_pr"] = None
                if orig["inv"]:
                    L(f"Eingang {n} ist invertiert (offen = aktiv) – wird für das Programm auf „nicht invertiert“ gestellt.")
                    if not await self._p_set(L, rt, n, "inv", False, "Eingang invertiert"):
                        res["gesamt"] = "Eingang ließ sich nicht vorbereiten"
                        return
                for i, key in enumerate(steps, 1):
                    if self._stopped():
                        break
                    stp = DIM_STEPS[key]
                    name = stp["label"]
                    st["prog"] = {"step": f"{i}/{len(steps)} {name}", "phase": "warten", "until": None}
                    L(f"--- Schritt {i}/{len(steps)}: {name} ---")
                    res[name] = await self._dim_step(L, rt, n, key, c, bus_lines)
                    await self.mgr.async_save()
            finally:
                try:
                    if rt is not None and orig is not None:
                        st["prog"] = {"step": None, "phase": "zurückstellen", "until": None}
                        L("--- Zurückstellen ---")
                        failed: list[dict] = []   # v1.22.2: nicht bestätigte Bits bleiben zum Nachholen gespeichert
                        for bit in ("inv",) + tuple(b for k in DIM_STEPS for b in DIM_STEPS[k]["bits"] if not (n == 4 and b in EVU_STEPS)):
                            want = orig["inv"] if bit == "inv" else False
                            cur = await self._read_bit_bus(rt, bit_offset(n, bit))
                            if cur is None or bool(cur) != want:
                                if cur is None:
                                    L(f"  {BIT_TEXT[bit]}: Zustand nicht lesbar – wird sicherheitshalber geschrieben")
                                if not await self._p_set(L, rt, n, bit, want, BIT_TEXT[bit]):
                                    failed.append({"method": "bit", "input": n, "bit": bit, "on": want})
                        if n in (1, 4) and orig.get("mod") is not None:
                            vals = await self.read()
                            dd = decode_opt((vals.get(f"hpin{n}opt") or {}).get("v"), n) or {}
                            if dd.get("mod") is not None and dd.get("mod") != orig["mod"]:
                                await self._restore({"method": "mod", "input": n, "value": orig["mod"]})
                                L(f"  Begrenzungswert → {orig['mod']}")
                        if orig.get("pr") is not None:
                            await self.write("powerreduction", orig["pr"])
                            got = await self._wait_value("powerreduction", lambda v: _f(v) is not None and int(_f(v)) == orig["pr"], timeout=20)
                            L(f"  Leistungsverringerung → {orig['pr']} %: {'bestätigt' if _f(got) is not None and int(_f(got)) == orig['pr'] else 'NICHT bestätigt (' + str(got) + ')'}")
                        vals = await self.read()
                        L(f"Eingang {n} nachher: {(vals.get(f'hpin{n}opt') or {}).get('v')} → "
                          f"{opt_text(decode_opt((vals.get(f'hpin{n}opt') or {}).get('v'), n))}")
                        st["restore"] = failed or None
                        if failed:
                            L(f"ACHTUNG: {len(failed)} Einstellung(en) nicht bestätigt – der Manager schreibt sie alle 30 s erneut, bitte am UI 800 prüfen.")
                except Exception as err:  # noqa: BLE001
                    L(f"FEHLER beim Zurückstellen: {err_text(err)} – der Manager versucht es weiter, bitte am UI 800 prüfen.")
                if rt is not None and _bus in rt.log.listeners:
                    rt.log.listeners.remove(_bus)
                if rt is not None and old_level is not None:
                    await self._bus_off(rt, old_level)
                self._lock.release()
        except asyncio.CancelledError:
            L("Programm abgebrochen (Home Assistant wird beendet) – der Eingang wird nach dem Start zurückgestellt.")
            raise
        except Exception as err:  # noqa: BLE001
            L(f"FEHLER: {err_text(err)}")
            res["gesamt"] = f"Fehler: {err_text(err)}"
        finally:
            L("=== Ergebnis ===")
            if self._stopped():
                L("Programm wurde vorzeitig abgebrochen.")
            for k, txt in res.items():
                L(f"{k}: {txt}")
            good = [k for k, v in res.items() if v.startswith("gedimmt")]
            block = [k for k, v in res.items() if v.startswith("gesperrt")]
            if good:
                L(f"Empfehlung: Für die Dimmung „{good[0]}“ verwenden – die Wärmepumpe läuft damit mit reduzierter Leistung weiter.")
            elif block:
                L(f"„{block[0]}“ schaltet den Kompressor ab (Sperre, keine Dimmung). Für Peak-Shaving geht das als harte Abschaltung.")
            elif res:
                L("Keine Variante hat die Leistung sichtbar verändert. Test wiederholen, wenn der Kompressor länger läuft "
                  "(z. B. Heizbetrieb bei Kälte oder Warmwasserladung), oder die aktive Zeit verlängern.")
            L("=== Ende ===")
            st["prog"] = None
            st["test_result"] = res
            st["test_done"] = dt_util.now().isoformat()
            try:
                await self.mgr.async_save()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("saving dim program log failed", exc_info=True)

    async def _dim_step(self, L, rt, n: int, key: str, c: dict, bus_lines: list[str]) -> str:
        st = self.st
        stp = DIM_STEPS[key]
        # 1. Kompressor muss laufen
        if c["wait_run_s"]:
            st["prog"]["phase"] = "wartet auf Kompressor"
            run = await self._p_observe(L, c["wait_run_s"], "warten", n,
                                        until=lambda s, _a: (s["p"] or 0) >= RUN_W)
            if self._stopped():
                return "abgebrochen"
            if not run or (run[-1]["p"] or 0) < RUN_W:
                L(f"  Kompressor lief in {c['wait_run_s']} s nicht an – Schritt ausgelassen.")
                return "ausgelassen – Kompressor lief nicht"
        # 2. Grundlast
        st["prog"]["phase"] = "Grundlast"
        base = await self._p_observe(L, c["base_s"], "vorher", n)
        b = self._avg([s["p"] for s in base])
        if self._stopped():
            return "abgebrochen"
        if b is None or b < RUN_W:
            L(f"  Grundlast zu klein (Ø {b if b is None else round(b)} W) – Schritt ausgelassen.")
            return "ausgelassen – Kompressor lief nicht"
        L(f"  Grundlast Ø {b:.0f} W")
        # 3. Funktion setzen (Eingang noch inaktiv → noch keine Wirkung)
        st["prog"]["phase"] = "Funktion setzen"
        pend = list(st.get("restore") or [])
        for bit in stp["bits"]:
            pend.append({"method": "bit", "input": n, "bit": bit, "on": False})
            st["restore"] = pend
            await self.mgr.async_save()
            if not await self._p_set(L, rt, n, bit, True, BIT_TEXT[bit]):
                return "Funktion nicht übernommen"
        if stp.get("mod"):
            vals = await self.read()
            m0 = (decode_opt((vals.get(f"hpin{n}opt") or {}).get("v"), n) or {}).get("mod") or 0
            pend.append({"method": "mod", "input": n, "value": m0})
            st["restore"] = pend
            await self.mgr.async_save()
            await self.write(f"hpin{n}opt", "x" * len(BITS_4 if n == 4 else BITS_123) + str(int(c["mod"])))
            got = await self._wait_value(f"hpin{n}opt", lambda v: (decode_opt(v, n) or {}).get("mod") == int(c["mod"]), timeout=20)
            L(f"  Begrenzungswert → {c['mod']}: {'übernommen' if (decode_opt(got, n) or {}).get('mod') == int(c['mod']) else 'NICHT übernommen'} ({got})")
        # 4. Eingang aktivieren
        st["prog"]["phase"] = "aktiv"
        st["prog"]["until"] = (dt_util.now() + timedelta(seconds=int(c["active_s"]))).isoformat()
        bus0 = len(bus_lines)
        if not await self._p_set(L, rt, n, "inv", True, "Eingang invertiert = AKTIV"):
            return "Eingang ließ sich nicht aktivieren"
        during = await self._p_observe(L, c["active_s"], "AKTIV", n)
        # 5. deaktivieren, Funktion zurück
        st["prog"]["phase"] = "zurück"
        st["prog"]["until"] = None
        keep: list[dict] = []   # v1.22.2: nicht bestätigtes Zurückstellen nicht vergessen
        if not await self._p_set(L, rt, n, "inv", False, "Eingang invertiert = inaktiv"):
            keep.append({"method": "bit", "input": n, "bit": "inv", "on": False})
        for bit in stp["bits"]:
            if not await self._p_set(L, rt, n, bit, False, BIT_TEXT[bit]):
                keep.append({"method": "bit", "input": n, "bit": bit, "on": False})
        if stp.get("mod"):
            try:
                await self._restore(pend[-1])
                L(f"  Begrenzungswert → {pend[-1]['value']}")
            except Exception as err:  # noqa: BLE001
                keep.append(pend[-1])
                L(f"  Begrenzungswert NICHT zurückgesetzt: {err_text(err)}")
        # zuerst die offenen Bits, danach die ursprünglichen Werte vom Programmstart (die gelten am Ende)
        st["restore"] = keep + [p for p in pend if p.get("bit") == "inv" or p.get("method") == "value"]
        await self.mgr.async_save()
        new = list(dict.fromkeys(bus_lines[bus0:]))
        if new:
            L("  Eingangszustand 0x04A2 während des Schritts:")
            for x in new[:6]:
                L("    BUS " + x)
        # 6. Erholung
        st["prog"]["phase"] = "Erholung"
        rec = await self._p_observe(L, c["recover_s"], "danach", n,
                                    until=lambda s, a: len(a) >= 3 and (s["p"] or 0) >= 0.7 * b)
        back = rec and (rec[-1]["p"] or 0) >= 0.7 * b
        L(f"  Erholung: {'nach ' + str(int(rec[-1]['t'])) + ' s wieder ≥ 70 % der Grundlast' if back else 'Grundlast in ' + str(c['recover_s']) + ' s nicht wieder erreicht'}")
        # Auswertung: späte 2/3 der aktiven Phase (WP regelt langsam)
        if not during:
            return "keine Messwerte während der aktiven Phase"
        late = during[len(during) // 3:] or during
        a = self._avg([s["p"] for s in late]) or 0.0
        first = next((s["t"] for s in during if (s["p"] or 0) < 0.85 * b), None)
        off = all(s["on"] is False or (s["p"] or 0) < 100 for s in late)
        react = f", Reaktion nach ~{int(first)} s" if first is not None else ""
        if off:
            return f"gesperrt – Kompressor aus (Ø {b:.0f} W → {a:.0f} W{react})"
        drop = (b - a) / b
        if drop >= 0.15:
            pr = st.get("prog_pr")
            hint = (f", passt zur Leistungsverringerung {pr} %" if pr and abs(a / b * 100 - pr) <= 15 else "")
            return f"gedimmt – Ø {b:.0f} W → {a:.0f} W (auf {a / b * 100:.0f} %{hint}{react})"
        return f"keine Reaktion – Ø {b:.0f} W → {a:.0f} W ({-drop * 100:+.0f} %)"

    # ------------------------------------------------------------- Anlern-Modus
    @property
    def learning(self) -> bool:
        return bool(self._ltask and not self._ltask.done())

    def _learn_add(self, text: str) -> None:
        log = self.st.setdefault("learn_log", [])
        log.append(f"{dt_util.now().strftime('%H:%M:%S')}  {text}")
        if len(log) > MAX_LEARN_LOG:
            del log[: len(log) - MAX_LEARN_LOG]

    def async_start_learn(self, secs: int = 180, note: str = "") -> bool:
        if self.learning or (self._task and not self._task.done()):
            return False
        secs = min(900, max(30, int(secs or 180)))
        self.st["learn_log"] = []
        self.st["learn_result"] = None
        self.st["learn_done"] = None
        self.st["learn_phase"] = "start"
        self.st["learn_until"] = None
        self._learn_stop = asyncio.Event()
        self._learn_add("Anlernen angefordert – startet …")
        self._ltask = self.hass.async_create_background_task(self._learn(secs, note), "ems_esp_manager input learn")
        return True

    def async_stop_learn(self) -> bool:
        if not self.learning or self._learn_stop is None:
            return False
        self._learn_stop.set()
        return True

    async def _learn_wait(self, secs: float) -> None:
        """Warten, bis die Zeit um ist oder „Anlernen beenden“ gedrückt wurde."""
        t = time.time()
        while time.time() - t < secs and not (self._learn_stop and self._learn_stop.is_set()):
            await asyncio.sleep(1)

    async def _learn_reads(self, rt, L) -> None:
        from .input_learn import READS

        for dev, typ, off, ln in READS:
            req = f"{dev:02X} {typ:X} {off} {ln}"
            try:
                await rt.api.command("system", "read", req)
            except Exception as err:  # noqa: BLE001
                L(f"system/read {req}: {err_text(err)}")
            await asyncio.sleep(1.2)
        await asyncio.sleep(2)

    async def _learn_opts(self) -> dict[int, Any]:
        try:
            vals = await self.read()
        except Exception:  # noqa: BLE001
            return {}
        return {n: (vals.get(f"hpin{n}opt") or {}).get("v") for n in range(1, 5)}

    async def _learn(self, secs: int, note: str) -> None:
        from .input_learn import MEASURE_TYPES, Recorder, hpin_offset_text, hx, opt_diff

        L = self._learn_add
        st = self.st
        rt = None
        rec: Recorder | None = None
        t0 = time.time()
        res: list[str] = []

        def _listen(line: str) -> None:
            if rec is not None:
                rec.feed(time.time() - t0, line)

        old_level = None
        try:
            L("=== Anlernen: Funktion der externen Eingänge ===")
            await self._t_header(L)
            if note:
                L(f"Vorhaben: {note}")
            rt = self._rt()
            if rt is None or rt.log is None:
                L("FEHLER: Kein EMS-ESP bzw. keine Log-Verbindung – Anlernen nicht möglich.")
                res.append("kein EMS-ESP / kein Log")
                return
            me = rt.bus_id if isinstance(getattr(rt, "bus_id", None), int) else 0x0B
            rec = Recorder(me=me & 0x7F)
            rt.log.listeners.append(_listen)
            old_level = rt.log_level
            await rt.api.command("system", "watch", rt.watch_mode if rt.watch_mode in ("on", "raw") else "on")
            if rt.log_level < 5:
                await rt.api.set_log_level(5)
            await asyncio.sleep(0.5)

            # 1. Ausgangszustand
            st["learn_phase"] = "vorher"
            L("--- 1. Ausgangszustand ---")
            opts0 = await self._learn_opts()
            for n in range(1, 5):
                d = decode_opt(opts0.get(n), n)
                L(f"Eingang {n}: {opts0.get(n)} → {opt_text(d)}")
            rec.phase = "vorher"
            await self._learn_reads(rt, L)
            snap = rec.snapshots.get("vorher", {})
            row = [f"{off}={hx(snap.get((0x486, off)))}" for off in range(0, 54) if (0x486, off) in snap]
            L(f"0x486 gelesen ({len(row)} Byte): " + (" ".join(row) if row else "keine Antwort"))
            if len(row) < 54:
                miss = [str(o) for o in range(0, 54) if (0x486, o) not in snap]
                L(f"0x486 ohne Antwort: Offset {', '.join(miss)}")

            # 2. Ruhephase
            st["learn_phase"] = "ruhe"
            L("--- 2. Ruhephase 30 s – bitte am UI 800 noch NICHTS ändern ---")
            rec.phase = "ruhe"
            await self._learn_wait(30)
            L(f"Ruhephase: {sum(1 for e in rec.events if e[1] == 'ruhe')} Telegramme, "
              f"{len(rec.noisy)} Bytes ändern sich ständig (werden ausgeblendet)")
            if self._learn_stop.is_set():
                L("Abgebrochen vor dem Anlernfenster.")
                res.append("abgebrochen")
                return

            # 3. Anlernfenster
            st["learn_phase"] = "fenster"
            st["learn_until"] = (dt_util.now() + timedelta(seconds=secs)).isoformat()
            L(f"--- 3. JETZT am UI 800 die Funktion des Eingangs ändern (Fenster {secs} s) ---")
            L("Service → Externer Eingang → Funktion wählen und bestätigen. Danach „Anlernen beenden“ drücken oder warten.")
            rec.phase = "fenster"
            tw = time.time()
            seen_ch = 0
            while time.time() - tw < secs and not self._learn_stop.is_set():
                await self._learn_wait(5)
                # Live-Anzeige der UI-Schreibzugriffe / Byteänderungen
                for t, src, typ, off, old, new, tel in rec.changes[seen_ch:]:
                    if typ in MEASURE_TYPES:
                        continue
                    name = hpin_offset_text(off) if typ == 0x486 else None
                    L(f"  +{int(t - (tw - t0)):>3} s  Byte geändert: {tel.src_name}(0x{src:02X}) 0x{typ:04X} Offset {off}: "
                      f"{hx(old)} → {hx(new)}" + (f"  ({name})" if name else ""))
                seen_ch = len(rec.changes)
            L(f"Fenster beendet nach {int(time.time() - tw)} s "
              f"({'vorzeitig' if self._learn_stop.is_set() else 'Zeit abgelaufen'}).")
            await asyncio.sleep(3)

            # 4. Endzustand
            st["learn_phase"] = "nachher"
            st["learn_until"] = None
            L("--- 4. Endzustand ---")
            rec.phase = "nachher"
            await self._learn_reads(rt, L)
            opts1 = await self._learn_opts()
            for n in range(1, 5):
                L(f"Eingang {n}: {opts1.get(n)} → {opt_text(decode_opt(opts1.get(n), n))}")

            # Auswertung
            L("--- Schreibzugriffe des UI 800 im Fenster ---")
            writes = rec.ui_writes()
            if not writes:
                L("keine – das UI 800 hat im Fenster nichts an andere Geräte geschrieben.")
            for t, tel, new in writes[:80]:
                extra = ""
                if tel.type == 0x486:
                    names = [hpin_offset_text(tel.offset + i) for i in range(len(tel.data or []))]
                    extra = "  (" + ", ".join(n for n in names if n) + ")" if any(names) else ""
                L(f"  {'NEU ' if new else '    '}{tel.src_name}(0x{tel.src:02X}) → {tel.dst_name}(0x{tel.dst:02X}) "
                  f"{tel.label} Offset {tel.offset}: {' '.join(hx(x) for x in tel.data or [])}{extra}")
            if len(writes) > 80:
                L(f"  … {len(writes) - 80} weitere")
            L("--- Geänderte Einstellungs-Bytes im Fenster (ohne Messwerte) ---")
            sc = rec.setting_changes()
            if not sc:
                L("keine")
            L(f"({len(rec.changes) - len(sc)} Änderungen von Messwerten ausgeblendet)")
            for t, src, typ, off, old, new, tel in sc[:120]:
                name = hpin_offset_text(off) if typ == 0x486 else None
                L(f"  {tel.src_name}(0x{src:02X}) 0x{typ:04X} Offset {off}: {hx(old)} → {hx(new)}" + (f"  ({name})" if name else ""))
            L("--- Vergleich gezielte Lesezugriffe vorher/nachher ---")
            diff = rec.snapshot_diff()
            if not diff:
                L("keine Unterschiede")
            for typ, off, old, new in diff:
                name = hpin_offset_text(off) if typ == 0x486 else None
                L(f"  0x{typ:04X} Offset {off}: {hx(old)} → {hx(new)}" + (f"  ({name})" if name else ""))
            od = opt_diff(opts0, opts1)
            L("--- EMS-ESP-Werte hpinNopt ---")
            for line in od or ["unverändert"]:
                L("  " + line)

            res = rec.findings() + od
            st["learn_found"] = res
            L("=== Ergebnis ===")
            if res:
                for line in res:
                    L("• " + line)
                L("Bitte dieses Protokoll kopieren und weitergeben – damit lässt sich das Schreiben der Funktion einbauen.")
            else:
                L("Nichts gefunden. Entweder wurde am UI 800 nichts geändert, oder das UI 800 speichert die Funktion "
                  "nur bei sich selbst und schickt sie nicht über den Bus.")
        except asyncio.CancelledError:
            L("Anlernen abgebrochen (Home Assistant wird beendet).")
            raise
        except Exception as err:  # noqa: BLE001
            L(f"FEHLER: {err_text(err)}")
            res.append(f"Fehler: {err_text(err)}")
        finally:
            if rt is not None and rt.log is not None and _listen in rt.log.listeners:
                rt.log.listeners.remove(_listen)
            if rt is not None:
                try:
                    if rt.watch_mode == "off":
                        await rt.api.command("system", "watch", "off")
                    if old_level is not None and old_level < 5:
                        await rt.api.set_log_level(old_level)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("restoring log settings failed", exc_info=True)
            if rec is not None:
                L(f"{len(rec.events)} Telegramme mitgeschnitten.")
            L("=== Ende ===")
            st["learn_phase"] = None
            st["learn_until"] = None
            st["learn_result"] = res
            st["learn_done"] = dt_util.now().isoformat()
            try:
                await self.mgr.async_save()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("saving learn log failed", exc_info=True)


def _hm_now() -> str:
    return dt_util.now().strftime("%d.%m. %H:%M")


def get(hass: HomeAssistant) -> PowerLimit | None:
    return hass.data.get(_DATA)


@callback
def async_setup(hass: HomeAssistant, mgr) -> PowerLimit:
    pl = hass.data.get(_DATA)
    if pl is None:
        pl = PowerLimit(hass, mgr)
        hass.data[_DATA] = pl
        pl.start()
    return pl


async def async_release_on_remove(hass: HomeAssistant, mgr, api) -> list[str]:
    """v1.22.2: Integration gelöscht – aktive Dimmung und offene Test-Rücksetzungen an der WP zurücknehmen.

    Läuft nach dem Entladen (der Eintrag ist dann nicht mehr geladen), deshalb mit der API des
    gelöschten Gateways statt über einen geladenen Eintrag.
    """
    pl = PowerLimit(hass, mgr)
    pl._api = lambda: api   # noqa: SLF001
    st, done = pl.st, []
    if st.get("restore"):
        try:
            async with asyncio.timeout(60):
                await pl._restore(st["restore"])   # noqa: SLF001
            pl._log_add(f"Integration entfernt – Testwerte zurückgesetzt: {st['restore']}")   # noqa: SLF001
            done.append("Testwerte zurückgesetzt")
            st["restore"] = None
            st.pop("restore_tries", None)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Integration entfernt: Testwerte nicht zurückgesetzt (%s) – bitte am UI 800 prüfen: %s", err, st["restore"])
    if st.get("active"):
        prev = st.get("prev") or {}
        try:
            async with asyncio.timeout(30):
                await pl._restore(prev)   # noqa: SLF001
            st.update(active=False, prev=None, until=None, why=None, last=f"{_hm_now()}: Dimmung aus (Integration entfernt)")
            pl._log_add(f"Dimmung AUS: {prev or '-'} zurückgeschrieben (Integration entfernt)")   # noqa: SLF001
            done.append("Dimmung beendet")
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Integration entfernt: Dimmung nicht zurückgenommen (%s) – bitte am UI 800 prüfen: %s", err, prev)
    if st.get("prog"):
        st["prog"] = None
    await mgr.async_save()
    return done
