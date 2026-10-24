"""Weekly switch times (Zeitprogramm) of the Bosch UI 800 / BC400 controller.

EMS-ESP does not know this telegram yet (shows "?(0x02C3)"), found in Sascha's
bus log on 07.10.2026:

    gateway(0x48) W thermostat(0x10), ?(0x02C3), data: 03 18 01 54 01 FF ...

0x02C3 = switch program heating circuit 1, 84 bytes = 7 days (Mo..Su) x
6 switch points x 2 bytes: [mode][time in 15-minute steps]
    mode 0x03 = Heizen (comfort), 0x01 = Absenken (eco)
    time 0xFF = switch point not used
    e.g. 03 18 = Heizen ab 06:00, 01 58 = Absenken ab 22:00

0x02FF = hot water (Warmwasser) program, same layout (log 07.10. 15:15):
    mode = hot water level: 0x00 = Aus, 0x01 = Eco+, 0x02 = Eco, 0x03 = Komfort
    (log 07.10. 16:16: display wrote 03 / 01 / 02 for the first point of Monday)
    e.g. 01 14 00 5C = Eco+ ab 05:00, Aus ab 23:00

On the bus the EMS+ type is written as id - 0x100 (0x02C3 -> bytes 01 C3).

The controller hardly answers read requests for this telegram (log 07.10.:
2 of 4 answers at 13:25, 0 of 56 at 13:53). It does however broadcast the
whole program after every change. Therefore:
  * every 0x02C3 telegram seen on the bus (broadcast, answer, write of the
    display) is stored and kept in .storage – the panel shows this copy,
  * an active read is only a short extra attempt,
  * writing sends one telegram per day and waits for the broadcast as
    confirmation.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, ems_entries

_LOGGER = logging.getLogger(__name__)

THERMOSTAT = 0x10
ME_DEFAULT = 0x0B          # EMS-ESP up to 3.8.1; 3.8.2+ default is 0x49 (gateway 2)
_ME = {"id": None}         # learned at runtime (log "me(0x..)" or settings ems_bus_id)
READ_WAIT = 4.0            # 07.10.2026: answers took up to 3.6 s


def current_me(rt=None) -> int:
    """EMS-ESP's own bus id – never hard-coded, it changed with firmware 3.8.2."""
    if rt is not None:
        log = getattr(rt, "log", None)
        if log is not None and getattr(log, "me_id", None):
            _ME["id"] = log.me_id
        elif getattr(rt, "bus_id", None):
            _ME["id"] = rt.bus_id
    return _ME["id"] or ME_DEFAULT
DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MODE_HEAT = 0x03
MODE_ECO = 0x01
POINTS_PER_DAY = 6
DAY_BYTES = POINTS_PER_DAY * 2
TOTAL = 7 * DAY_BYTES
# the controller's own blocks (how the display writes and how it answers reads)
BLOCKS = [(0, 24), (24, 24), (48, 24), (72, 12)]


class Program:
    """One switch program telegram of the controller."""

    def __init__(self, key: str, type_id: int, label: str, modes: dict[str, int], unused: list[int]) -> None:
        self.key = key
        self.type_id = type_id
        self.label = label
        self.modes = modes                      # name -> mode byte
        self.names = {v: k for k, v in modes.items()}
        self.unused = unused                    # mode bytes of unused points as the display writes them


# confirmed from Sascha's logs 07.10.2026 (HK2-4 / program 2 not yet known)
PROGRAMS: dict[str, Program] = {
    "hc1": Program("hc1", 0x02C3, "Heizkreis 1", {"heat": MODE_HEAT, "eco": MODE_ECO},
                   [MODE_HEAT, MODE_ECO, MODE_ECO, MODE_ECO, MODE_HEAT, MODE_HEAT]),
    "dhw": Program("dhw", 0x02FF, "Warmwasser",
                   {"off": 0x00, "ecoplus": 0x01, "eco": 0x02, "comfort": 0x03}, [0x01] * POINTS_PER_DAY),
}
BY_TYPE: dict[int, Program] = {p.type_id: p for p in PROGRAMS.values()}
# kept for compatibility: heating circuit -> telegram type
PROGRAM_TYPES = {1: 0x02C3}
UNUSED_MODES = PROGRAMS["hc1"].unused
_DATA = f"{DOMAIN}_schedule"

_HEX = r"([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})*)"
# from the controller: "thermostat(0x10) B all(0x00), ?(0x02C3), data: 03 18 ... (offset 24)"
_RE_FROM = re.compile(
    r"(?:thermostat|10)\(0x10\)\s+[WB]\s+\S+?\(0x[0-9A-Fa-f]{2}\),\s*.*?\(0x([0-9A-Fa-f]{4})\),\s*data:\s*"
    + _HEX + r"\s*(?:\(offset (\d+)\))?\s*$"
)
# to the controller (display): "gateway(0x48) W thermostat(0x10), ?(0x02C3), data: ..."
_RE_TO = re.compile(
    r"\(0x([0-9A-Fa-f]{2})\)\s+W\s+(?:thermostat|10)\(0x10\),\s*.*?\(0x([0-9A-Fa-f]{4})\),\s*data:\s*"
    + _HEX + r"\s*(?:\(offset (\d+)\))?\s*$"
)
# raw:  Rx: 90 0B FF 18 01 C3 03 18 ... CRC  (src dst FF offset typeH typeL data crc, type = bytes + 0x100;
#       src may carry the bus mask 0x80)
_RE_RAW = re.compile(r"^(?:Rx|Tx):\s*((?:[0-9A-Fa-f]{2}\s*)+)$")


class ScheduleCache:
    """Last known switch program bytes, filled from the log stream, persisted."""

    def __init__(self) -> None:
        self.data: dict[int, bytearray] = {}
        self.known: dict[int, list[bool]] = {}
        self.updated: dict[int, float] = {}   # wall clock (timestamp)
        self.rx: dict[tuple[int, int], float] = {}  # (type, offset) -> last time data arrived
        self.event = asyncio.Event()
        self.on_change: Any = None

    @staticmethod
    def _valid(type_id: int, offset: int, payload: bytes) -> bool:
        """Only plausible switch-point data (protects against misread frames)."""
        prog = BY_TYPE.get(type_id)
        if prog is None or offset % 2 or len(payload) < 2 or len(payload) % 2 or offset + len(payload) > TOTAL:
            return False
        return all(
            payload[i] in prog.names and (payload[i + 1] <= 95 or payload[i + 1] == 0xFF)
            for i in range(0, len(payload), 2)
        )

    def _store(self, type_id: int, offset: int, payload: bytes) -> None:
        if not self._valid(type_id, offset, payload):
            _LOGGER.debug("Ignoring implausible 0x%04X data at %s: %s", type_id, offset, payload.hex(" "))
            return
        buf = self.data.setdefault(type_id, bytearray([0xFF] * TOTAL))
        known = self.known.setdefault(type_id, [False] * TOTAL)
        for i, b in enumerate(payload):
            if 0 <= offset + i < TOTAL:
                buf[offset + i] = b
                known[offset + i] = True
        self.updated[type_id] = time.time()
        self.rx[(type_id, offset)] = time.time()
        self.event.set()
        if self.on_change:
            self.on_change()

    def feed(self, text: str) -> None:
        """Called for every log line."""
        t = text.strip()
        m = _RE_FROM.search(t)
        if m:
            self._feed(int(m.group(1), 16), m.group(2), m.group(3))
            return
        m = _RE_TO.search(t)
        if m:
            # our own writes ("me(0x49) W thermostat") only count once confirmed
            if not t.startswith("me(") and int(m.group(1), 16) != current_me():
                self._feed(int(m.group(2), 16), m.group(3), m.group(4))
            return
        m = _RE_RAW.match(t)
        if not m:
            return
        try:
            b = [int(x, 16) for x in m.group(1).split()]
        except ValueError:
            return
        if len(b) < 8 or b[2] != 0xFF:
            return
        # Sascha's Bosch bus sends every source byte with 0x80 set (HT3-style
        # mask, log 07.10.2026 19:00: "90 00 FF …" = thermostat broadcast,
        # "8B 88 FF …" = EMS-ESP 0x0B reads boiler). Strip the mask from the
        # source; a set bit in the destination still means "read request".
        src, dst = b[0] & 0x7F, b[1]
        if dst & 0x80:  # read request, no data
            return
        type_id = ((b[4] << 8) | b[5]) + 0x100
        if type_id not in BY_TYPE:
            return
        if src == THERMOSTAT or (dst == THERMOSTAT and src != current_me()):
            self._store(type_id, b[3], bytes(b[6:-1]))

    def _feed(self, type_id: int, data: str, offset: str | None) -> None:
        if type_id not in BY_TYPE:
            return
        try:
            payload = bytes(int(x, 16) for x in data.split())
        except ValueError:
            return
        self._store(type_id, int(offset or 0), payload)

    def complete(self, type_id: int) -> bool:
        return type_id in self.known and all(self.known[type_id])

    def day_known(self, type_id: int, day: int) -> bool:
        k = self.known.get(type_id)
        return bool(k) and all(k[day * DAY_BYTES:(day + 1) * DAY_BYTES])

    def day_bytes(self, type_id: int, day: int) -> bytes | None:
        if not self.day_known(type_id, day):
            return None
        return bytes(self.data[type_id][day * DAY_BYTES:(day + 1) * DAY_BYTES])

    # ---------- persistence ----------
    def as_dict(self) -> dict[str, Any]:
        return {
            f"{t:04X}": {
                "data": self.data[t].hex(),
                "known": "".join("1" if k else "0" for k in self.known[t]),
                "updated": self.updated.get(t, 0),
            }
            for t in self.data
        }

    def load_dict(self, d: dict[str, Any]) -> None:
        for key, v in (d or {}).items():
            try:
                t = int(key, 16)
                data = bytearray.fromhex(v["data"])
                known = [c == "1" for c in v["known"]]
            except (ValueError, KeyError, TypeError):
                continue
            if len(data) == TOTAL and len(known) == TOTAL:
                self.data[t], self.known[t] = data, known
                self.updated[t] = float(v.get("updated", 0))


def decode_day(chunk: bytes | bytearray, prog: Program | None = None) -> list[dict[str, Any]]:
    prog = prog or PROGRAMS["hc1"]
    pts = []
    for p in range(POINTS_PER_DAY):
        mode, slot = chunk[p * 2], chunk[p * 2 + 1]
        if slot == 0xFF or slot > 95:
            continue
        pts.append(
            {
                "time": f"{slot // 4:02d}:{(slot % 4) * 15:02d}",
                "mode": prog.names.get(mode, f"0x{mode:02X}"),
            }
        )
    return pts


def decode(buf: bytes | bytearray, prog: Program | None = None) -> list[list[dict[str, Any]]]:
    """84 bytes -> 7 days x list of {time, mode}."""
    return [decode_day(buf[d * DAY_BYTES:(d + 1) * DAY_BYTES], prog) for d in range(7)]


def encode_day(
    points: list[dict[str, Any]], old: bytes | bytearray | None = None, prog: Program | None = None
) -> bytes:
    """List of {time 'HH:MM', mode heat|eco / on|off} -> 12 bytes (validated)."""
    prog = prog or PROGRAMS["hc1"]
    if not 1 <= len(points) <= POINTS_PER_DAY:
        raise ValueError(f"1 bis {POINTS_PER_DAY} Schaltpunkte je Tag")
    slots = []
    for pt in points:
        hh, mm = (int(x) for x in str(pt["time"]).split(":"))
        if not (0 <= hh <= 23 and mm in (0, 15, 30, 45)):
            raise ValueError(f"Ungültige Zeit {pt['time']} (15-Minuten-Schritte)")
        mode = prog.modes.get(pt["mode"])
        if mode is None:
            raise ValueError(f"Ungültige Betriebsart {pt['mode']}")
        slots.append((hh * 4 + mm // 15, mode))
    slots.sort()
    if len({s for s, _ in slots}) != len(slots):
        raise ValueError("Zwei Schaltpunkte mit derselben Uhrzeit")
    out = bytearray()
    for i in range(POINTS_PER_DAY):
        if i < len(slots):
            out += bytes([slots[i][1], slots[i][0]])
        else:  # unused point: keep the controller's mode byte, time FF
            keep = old[i * 2] if old and old[i * 2] in prog.names else prog.unused[i]
            out += bytes([keep, 0xFF])
    return bytes(out)


def _type_bytes(type_id: int) -> list[int]:
    """EMS+ type on the bus = type id - 0x100 (EMS-ESP adds 256 when decoding)."""
    raw = type_id - 0x100
    return [raw >> 8, raw & 0xFF]


def _hex(data: bytes | list[int]) -> str:
    return " ".join(f"{b:02X}" for b in data)


class ScheduleManager:
    """Read/write the switch program through EMS-ESP."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.cache = ScheduleCache()
        self.cache.on_change = self._schedule_save
        self.store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.schedule")
        self._lock = asyncio.Lock()
        self._loaded = False
        self._load_task: asyncio.Task | None = None
        self._read_cmd_ok: bool | None = None

    async def async_load(self) -> None:
        """Load the stored programs once (concurrent callers wait for the same load)."""
        if self._loaded:
            return
        if self._load_task is None:
            self._load_task = self.hass.async_create_task(self._async_do_load(), eager_start=True)
        await asyncio.shield(self._load_task)

    async def _async_do_load(self) -> None:
        try:
            self.cache.load_dict(await self.store.async_load() or {})
        except Exception:  # noqa: BLE001 - a broken file must not stop the manager
            _LOGGER.warning("Stored switch programs unreadable – starting empty", exc_info=True)
        self._loaded = True

    @callback
    def _schedule_save(self) -> None:
        self.store.async_delay_save(self.cache.as_dict, 5)

    def _runtime(self):
        for e in ems_entries(self.hass):
            if e.state is ConfigEntryState.LOADED:
                return e.runtime_data
        raise RuntimeError("Kein EMS-ESP verbunden")

    async def _with_capture(self, coro_fn) -> Any:
        """Make sure telegrams appear in the log while coro_fn runs.

        The watch setting is always (re)sent – it may have been changed in the
        EMS-ESP web UI, so the value known to the integration can be stale.
        Afterwards the setting chosen in Home Assistant is restored.
        """
        rt = self._runtime()
        keep = rt.watch_mode if rt.watch_mode in ("on", "raw") else "on"
        old_level = rt.log_level
        try:
            await rt.api.command("system", "watch", keep)
            if rt.log_level < 5:
                await rt.api.set_log_level(5)
            await asyncio.sleep(0.3)
            return await coro_fn(rt)
        finally:
            try:
                if rt.watch_mode == "off":
                    await rt.api.command("system", "watch", "off")
                if old_level < 5:
                    await rt.api.set_log_level(old_level)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Restoring log settings failed", exc_info=True)

    def import_text(self, text: str, hc: int | str = 1) -> dict[str, Any]:
        """Take the switch programs out of a downloaded bus log (txt)."""
        key = self._prog(hc).key
        before = {p.type_id: self.cache.updated.get(p.type_id, 0) for p in PROGRAMS.values()}
        lines = 0
        for line in text.splitlines():
            if line.startswith("#"):
                continue
            msg = line.split(" | ", 3)[3] if line.count(" | ") >= 3 else line
            # EMS-ESP web log format: "14:33:22.008 N me(0x0B) R ..."
            msg = re.sub(r"^\S+\s+[EWNIDTA]\s+", "", msg) if not msg.startswith(("Rx", "Tx")) else msg
            self.cache.feed(msg)
            lines += 1
        found = [p.key for p in PROGRAMS.values() if self.cache.updated.get(p.type_id, 0) > before[p.type_id]]
        res = self.result(key)
        res["imported"] = bool(found)
        res["found"] = found
        res["programs"] = {k: self.result(k) for k in found}
        res["lines"] = lines
        return res

    @staticmethod
    def _prog(hc: int | str) -> Program:
        key = f"hc{hc}" if isinstance(hc, int) else str(hc)
        prog = PROGRAMS.get(key)
        if prog is None:
            raise ValueError(f"Zeitprogramm „{key}“ ist noch nicht bekannt (bisher nur HK1 und Warmwasser)")
        return prog

    def _type(self, hc: int | str) -> int:
        return self._prog(hc).type_id

    def result(self, hc: int | str, note: str | None = None) -> dict[str, Any]:
        prog = self._prog(hc)
        type_id = prog.type_id
        days = [
            decode_day(self.cache.day_bytes(type_id, d), prog) if self.cache.day_known(type_id, d) else None
            for d in range(7)
        ]
        # day without any switch point: the controller keeps the mode byte of the
        # first point -> whole day in this mode (log 16.10.2026: all "03 FF" = Heizen all week)
        constant = [
            prog.names.get(self.cache.day_bytes(type_id, d)[0]) if days[d] == [] else None
            for d in range(7)
        ]
        if note is None and all(d == [] for d in days):
            mode = constant[0]
            label = {"heat": "Heizen", "eco": "Absenken", "off": "Aus", "ecoplus": "Eco+",
                     "comfort": "Komfort"}.get(mode or "", mode or "?")
            note = f"Im Regler sind für dieses Programm keine Schaltzeiten hinterlegt – ganztägig {label}."
        ts = self.cache.updated.get(type_id)
        return {
            "constant": constant,
            "hc": hc,
            "prog": prog.key,
            "label": prog.label,
            "modes": list(prog.modes),
            "days": days,
            "complete": all(d is not None for d in days),
            "updated": dt_util.as_local(dt_util.utc_from_timestamp(ts)).isoformat(timespec="seconds") if ts else None,
            "note": note,
        }

    async def _request_block(self, rt, type_id: int, off: int, length: int) -> bool:
        """Ask the controller for one block, wait for the answer.

        The controller answers reads in its own 24-byte blocks (offset 0/24/48/72)
        – 12-byte day reads were never answered (07.10. 13:53, 0 of 56), 24-byte
        reads were (13:25, 15:11). First EMS-ESP's own read command (same as
        EMS-ESP uses after a write), then the raw telegram.
        """
        th, tl = _type_bytes(type_id)
        attempts = []
        if self._read_cmd_ok is not False:
            attempts.append(("read", f"{THERMOSTAT:02X} {type_id:X} {off} {length}"))
        else:
            # only without system/read: raw request with EMS-ESP's real bus id
            # (v1.10.2 sent 0B as source although EMS-ESP was 0x49 -> garbage)
            attempts.append(("send", _hex([current_me(rt), THERMOSTAT | 0x80, 0xFF, off, length, th, tl])))
        for cmd, value in attempts:
            t0 = time.time()
            try:
                await rt.api.command("system", cmd, value)
                if cmd == "read":
                    self._read_cmd_ok = True
            except Exception as err:  # noqa: BLE001
                if cmd == "read" and self._read_cmd_ok is None:
                    _LOGGER.debug("system/read not available (%s), using raw telegram", err)
                    self._read_cmd_ok = False
                    return await self._request_block(rt, type_id, off, length)
                raise
            while time.time() - t0 < READ_WAIT:
                if self.cache.rx.get((type_id, off), 0) >= t0:
                    return True
                self.cache.event.clear()
                try:
                    await asyncio.wait_for(self.cache.event.wait(), 0.25)
                except TimeoutError:
                    pass
        return False

    async def read(self, hc: int | str, force: bool = False) -> dict[str, Any]:
        """Stored copy; asks the controller when forced or incomplete."""
        await self.async_load()
        prog = self._prog(hc)
        type_id = prog.type_id
        if self.cache.complete(type_id) and not force:
            return self.result(hc)
        async with self._lock:

            async def _do(rt) -> None:
                t_start = time.time()
                known = self.cache.known.get(type_id, [False] * TOTAL)
                todo = [
                    (o, n) for o, n in BLOCKS
                    if force or not all(known[o:o + n])
                ]
                for _round in range(3):
                    todo = [(o, n) for o, n in todo if self.cache.rx.get((type_id, o), 0) < t_start]
                    if not todo:
                        break
                    for o, n in todo:
                        await self._request_block(rt, type_id, o, n)
                        await asyncio.sleep(0.2)

            await self._with_capture(_do)
        res = self.result(hc)
        if not res["complete"]:
            missing = [DAYS[d] for d, v in enumerate(res["days"]) if v is None]
            res["note"] = (
                "Der Regler hat nicht auf alle Leseanfragen geantwortet (fehlend: "
                + ", ".join(missing)
                + "). Bitte „Vom Regler neu lesen“ – oder die Zeiten für alle Tage neu speichern."
            )
        elif force:
            res["note"] = None
        return res

    async def write_days(self, hc: int | str, days: list[int], points: list[dict[str, Any]]) -> dict[str, Any]:
        """Write the same points to several days, then wait for the broadcast."""
        await self.async_load()
        prog = self._prog(hc)
        type_id = prog.type_id
        if not self.cache.complete(type_id):
            # read the missing blocks from the controller first
            await self.read(hc)
        if not self.cache.complete(type_id):
            missing = [d for d in range(7) if not self.cache.day_known(type_id, d)]
            if not set(missing) <= set(days):
                raise ValueError(
                    "Der Regler hat die übrigen Tage nicht geliefert ("
                    + ", ".join(DAYS[d] for d in missing)
                    + "). Bitte noch einmal speichern oder „Beim Speichern übernehmen für: Alle Tage“ wählen."
                )
        payloads: dict[int, bytes] = {}
        for day in days:
            if not 0 <= day <= 6:
                raise ValueError("Tag 0 (Mo) bis 6 (So)")
            payloads[day] = encode_day(points, self.cache.day_bytes(type_id, day), prog)

        async with self._lock:

            async def _do(rt) -> None:
                # write exactly like the display does: the complete program in
                # 4 telegrams (offset 0/24/48/72, 24/24/24/12 bytes) – single
                # 12-byte day writes were ignored by the controller (07.10.)
                th, tl = _type_bytes(type_id)
                me = current_me(rt)
                full = bytearray(self.cache.data.get(type_id, bytearray([0xFF] * TOTAL)))
                for day, payload in payloads.items():
                    full[day * DAY_BYTES:(day + 1) * DAY_BYTES] = payload
                _LOGGER.info(
                    "Zeitprogramm %s schreiben (%s, Absender 0x%02X): %s",
                    prog.label, ", ".join(DAYS[d] for d in payloads), me, _hex(full),
                )
                for off in (0, 24, 48, 72):
                    chunk = full[off:off + 24]
                    await rt.api.command(
                        "system", "send", _hex([me, THERMOSTAT, 0xFF, off, th, tl, *chunk])
                    )
                    await asyncio.sleep(0.3)
                # the controller broadcasts the changed program – wait for it
                deadline = time.time() + 8
                while time.time() < deadline and not all(
                    self.cache.day_bytes(type_id, d) == p for d, p in payloads.items()
                ):
                    self.cache.event.clear()
                    try:
                        await asyncio.wait_for(self.cache.event.wait(), 0.5)
                    except TimeoutError:
                        pass

            await self._with_capture(_do)
        confirmed = all(self.cache.day_bytes(type_id, d) == p for d, p in payloads.items())
        res = self.result(hc)
        res["confirmed"] = confirmed
        if not confirmed:
            res["note"] = (
                "Gesendet, aber der Regler hat die Änderung (noch) nicht bestätigt. "
                "Bitte am Display prüfen."
            )
        return res


def get_manager(hass: HomeAssistant) -> ScheduleManager:
    if _DATA not in hass.data:
        hass.data[_DATA] = ScheduleManager(hass)
    return hass.data[_DATA]


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/schedule/get",
        vol.Optional("hc", default=1): int,
        vol.Optional("prog"): vol.In(list(PROGRAMS)),
        vol.Optional("force", default=False): bool,
        vol.Optional("max_age"): vol.Coerce(float),  # older panels
    }
)
@websocket_api.async_response
async def ws_schedule_get(hass: HomeAssistant, connection, msg) -> None:
    try:
        res = await get_manager(hass).read(msg.get("prog") or msg["hc"], msg["force"])
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Zeitprogramm lesen: %s", err)
        connection.send_error(msg["id"], "schedule_error", str(err))
        return
    connection.send_result(msg["id"], res)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/schedule/set_days",
        vol.Optional("hc", default=1): int,
        vol.Optional("prog"): vol.In(list(PROGRAMS)),
        vol.Required("days"): [vol.All(int, vol.Range(min=0, max=6))],
        vol.Required("points"): [
            {vol.Required("time"): str, vol.Required("mode"): vol.In(["heat", "eco", "off", "ecoplus", "comfort"])}
        ],
    }
)
@websocket_api.async_response
async def ws_schedule_set_days(hass: HomeAssistant, connection, msg) -> None:
    try:
        res = await get_manager(hass).write_days(msg.get("prog") or msg["hc"], sorted(set(msg["days"])), msg["points"])
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Zeitprogramm schreiben: %s", err)
        connection.send_error(msg["id"], "schedule_error", str(err))
        return
    connection.send_result(msg["id"], res)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/schedule/import",
        vol.Required("content"): str,
        vol.Optional("hc", default=1): int,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_schedule_import(hass: HomeAssistant, connection, msg) -> None:
    mgr = get_manager(hass)
    try:
        await mgr.async_load()
        res = mgr.import_text(msg["content"][:5_000_000], msg["hc"])
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "schedule_error", str(err))
        return
    connection.send_result(msg["id"], res)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/schedule/info"})
@websocket_api.async_response
async def ws_schedule_info(hass: HomeAssistant, connection, msg) -> None:
    mgr = get_manager(hass)
    await mgr.async_load()
    res = mgr.result(1)
    res["programs"] = {k: mgr.result(k) for k in PROGRAMS}
    connection.send_result(msg["id"], res)


def async_setup_schedule(hass: HomeAssistant) -> ScheduleManager:
    mgr = get_manager(hass)
    if not hass.data.get(f"{_DATA}_ws"):
        websocket_api.async_register_command(hass, ws_schedule_get)
        websocket_api.async_register_command(hass, ws_schedule_set_days)
        websocket_api.async_register_command(hass, ws_schedule_import)
        websocket_api.async_register_command(hass, ws_schedule_info)
        hass.data[f"{_DATA}_ws"] = True
        hass.async_create_task(mgr.async_load())
    return mgr
