"""Reads the EMS-ESP log (EMS bus traffic) through its server-sent-event stream."""

from __future__ import annotations

import asyncio
import json
import time
import re
import logging
from collections import deque
from collections.abc import Awaitable, Callable

import aiohttp

from homeassistant.util import dt as dt_util

from .api import LEVEL_CHAR, EmsEspApi

_LOGGER = logging.getLogger(__name__)

MAX_LINES = 60            # shown in the dashboard (sensor attribute)
MAX_DOWNLOAD = 20000      # kept in memory for the download (~2 MB)
RECONNECT_SECONDS = 10
STALL_SECONDS = 45         # no line for this long -> check whether the gateway restarted
# reconnect anyway after this long without a single line. A quiet bus (log level
# "warning", no capture) is normal and the stall check below already detects a
# restarted / unreachable gateway – reconnecting every 5 min only made EMS-ESP
# replay its whole log buffer again (log 19.10.2026: 6 replays in one hour)
MAX_SILENT_SECONDS = 1800
SEEN_KEYS = 5000           # EMS-ESP replays its log buffer on every connect -> skip known lines
TX_MODES = {0: "aus (nur lesen)", 1: "EMS", 2: "EMS+", 3: "HT3", 4: "Hardware", 5: "Auto"}

_RE_ME = re.compile(r"\bme\(0x([0-9A-Fa-f]{2})\)")
_RE_UPTIME = re.compile(r"(?:(\d+)\D)?(\d+):(\d+):(\d+)")
_RE_ESP_TIME = re.compile(r"(?:(\d{4})-(\d\d)-(\d\d)T|(\d+)\+)?(\d+):(\d+):(\d+)(?:\.(\d+))?")


def _esp_seconds(value: str) -> float | None:
    """EMS-ESP log time ('2026-10-16T12:44:10.125' or uptime '000+01:02:03.456') as seconds."""
    m = _RE_ESP_TIME.fullmatch(value.strip()) if value else None
    if not m:
        return None
    y, mo, d, days, h, mi, sec, frac = m.groups()
    t = int(h) * 3600 + int(mi) * 60 + int(sec) + (float("0." + frac) if frac else 0.0)
    if y:
        from datetime import date

        t += date(int(y), int(mo), int(d)).toordinal() * 86400
    elif days:
        t += int(days) * 86400
    return t


def _uptime_seconds(value) -> float | None:
    """EMS-ESP uptime as seconds (number or text like '000+01:02:03.456')."""
    if isinstance(value, (int, float)):
        return float(value)
    m = _RE_UPTIME.search(str(value or ""))
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


_RE_DECODED_TELEGRAM = re.compile(r"^\S+\(0x[0-9A-Fa-f]{2}\)\s+[RWB]\s+\S+\(0x[0-9A-Fa-f]{2}\),")


def _is_telegram(text: str) -> bool:
    """Rx/Tx lines of the bus capture (watch on / raw)."""
    t = text.lstrip()
    if t.startswith(("Rx:", "Tx:", "Rx ", "Tx ", "Sent", "Received")):
        return True
    # decoded (EMS-ESP 3.7/3.8): "boiler(0x08) B all(0x00), UBAMonitorFastPlus(0xE4), data: ..."
    #                            "me(0x0B) R thermostat(0x10), ?(0x02C3), length: 12"
    if _RE_DECODED_TELEGRAM.match(t):
        return True
    # older format: "Boiler(0x08) -> All(0x00), UBAMonitorFast(0x18), data: ..."
    return "->" in t and ("data:" in t or "(0x" in t)



def _device_lines(devices: list[dict] | None) -> list[str]:
    """Header lines: manager version + detected EMS devices with software version."""
    from .const import MANAGER_VERSION

    out = [f"# EMS-ESP Manager v{MANAGER_VERSION}"]
    if devices is None:
        return out + ["# Geräte: nicht abrufbar (EMS-ESP /api/system/info)"]
    if not devices:
        return out + ["# Geräte: keine erkannt"]
    out.append("# Geräte (Typ | Bus-ID | Produkt-ID | Software | Marke | Name):")
    for d in devices:
        dev_id = str(d.get("deviceID") or "?")
        if dev_id != "?" and not dev_id.lower().startswith("0x"):
            dev_id = "0x" + dev_id.upper().zfill(2)
        out.append(
            f"#   {d.get('type') or '?'} | {dev_id} | {d.get('productID', '?')} | "
            f"{d.get('version') or '?'} | {d.get('brand') or '-'} | {d.get('name') or ''}"
        )
    return out

class EmsEspLog:
    """Keeps the last log lines of one gateway."""

    def __init__(self, api: EmsEspApi, on_update: Callable[[], None]) -> None:
        self._api = api
        self._on_update = on_update
        self.lines: deque[str] = deque(maxlen=MAX_LINES)
        # full lines for the download: (HA time, EMS-ESP time, level, text)
        self.history: deque[tuple[str, str, str, str]] = deque(maxlen=MAX_DOWNLOAD)
        self.since: str | None = None
        self.listeners: list[Callable[[str], None]] = []   # e.g. switch-program decoder
        self.connected = False
        self.me_id: int | None = None      # EMS-ESP's own bus id, learned from "me(0x..)"
        self.on_connect: Callable[[bool], Awaitable[None]] | None = None
        self.connects = 0
        self.last_rx = 0.0
        self._uptime: float | None = None
        self._seen: deque[tuple[str, str]] = deque(maxlen=SEEN_KEYS)
        self._seen_set: set[tuple[str, str]] = set()
        self._seen_t: dict[str, list[float]] = {}   # text -> EMS-ESP times seen (replay check)
        self._quiet = False        # last disconnect was only a silence timeout
        self._task: asyncio.Task | None = None
        self._notify_handle: asyncio.TimerHandle | None = None
        self.telegrams = 0          # telegram lines in self.history (kept up to date, no re-scan)

    # ------------------------------------------------------------ public
    def start(self, task_factory: Callable[[object, str], asyncio.Task]) -> None:
        self._task = task_factory(self._run(), "ems_esp_manager log")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._notify_handle is not None:
            self._notify_handle.cancel()
            self._notify_handle = None
        self.connected = False

    def clear(self) -> None:
        self.lines.clear()
        self.history.clear()
        self.telegrams = 0
        self.since = None
        self._on_update()

    def _history_add(self, row: tuple[str, str, str, str]) -> None:
        """Append to the download buffer and keep the telegram counter right."""
        if len(self.history) == self.history.maxlen and _is_telegram(self.history[0][3]):
            self.telegrams -= 1
        self.history.append(row)
        if _is_telegram(row[3]):
            self.telegrams += 1

    def note(self, text: str) -> None:
        """Own status line of the manager (level 'M') – shows up in the download."""
        now = dt_util.now()
        ha_time = now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        self._history_add((ha_time, "", "M", f"[Manager] {text}"))
        self.lines.append(f"{now.strftime('%H:%M:%S')} [Manager] {text}")
        _LOGGER.info("EMS-ESP log: %s", text)
        self._on_update()

    def export(self, telegrams_only: bool = False, devices: list[dict] | None = None) -> str:
        """Text file for the download (one line per log entry).

        ``devices``: EMS devices from /api/system/info (type, name, deviceID,
        productID, brand, version) – written into the header, so every log
        shows which units and software versions it comes from.
        """
        rows = [
            f"{ha_time} | {esp_time} | {level} | {text}"
            for ha_time, esp_time, level, text in self.history
            if not telegrams_only or _is_telegram(text)
        ]
        head = [
            "# EMS-ESP Bus-Log – exportiert aus Home Assistant (EMS-ESP Manager)",
            f"# Zeilen: {len(rows)}" + (" (nur Telegramme)" if telegrams_only else "")
            + f", Speicher max. {MAX_DOWNLOAD} Zeilen",
        ]
        head += _device_lines(devices)
        head.append("# HA-Zeit | EMS-ESP-Zeit | Level | Meldung")
        return "\n".join(head + rows) + "\n"

    @property
    def last_line(self) -> str | None:
        return self.lines[-1] if self.lines else None

    # ----------------------------------------------------------- internal
    def _add(self, raw: str) -> bool:
        """Store one log event. True = the gateway announced a restart."""
        try:
            event = json.loads(raw)
        except ValueError:
            return False
        text = str(event.get("m", "")).strip()
        if not text:
            return False
        m = _RE_ME.search(text)
        if m:
            self.me_id = int(m.group(1), 16)
        esp_time = str(event.get("t", ""))
        # EMS-ESP sends its whole log buffer again after every (re)connect
        if esp_time and self._is_replay(esp_time, text):
            return False
        level = LEVEL_CHAR.get(int(event.get("l", 6)), "?")
        now = dt_util.now()
        ha_time = now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        self._history_add((ha_time, esp_time, level, text))
        for listener in self.listeners:
            try:
                listener(text)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("log listener failed", exc_info=True)
        if self.since is None:
            self.since = now.strftime("%d.%m. %H:%M")
        # dashboard: short time (HH:MM:SS.mmm) – EMS-ESP sends a date or an uptime
        if "T" in esp_time:
            short = esp_time.split("T", 1)[1][:12]
        elif "+" in esp_time:
            short = esp_time.split("+", 1)[1][:12]
        else:
            short = esp_time[:12] or ha_time[11:]
        self.lines.append(f"{short} {level} {text}")
        self._schedule_notify()
        return "Restarting" in text or "restarting" in text

    def _is_replay(self, esp_time: str, text: str) -> bool:
        """True if this line was already stored (EMS-ESP replays its buffer on connect).

        The replayed lines carry the same text, but their time stamp is often
        shifted by exactly one second (log 19.10.2026: 14:12:20.122 -> 14:12:19.122),
        so an exact comparison let them through as new lines. Lines that are
        really repeated (e.g. two failed commands 160 ms apart) are kept.
        """
        key = (esp_time, text)
        if key in self._seen_set:
            return True
        t = _esp_seconds(esp_time)
        times = self._seen_t.get(text)
        if t is not None and times and any(abs(abs(t - x) - 1.0) < 0.02 for x in times):
            return True
        if len(self._seen) == self._seen.maxlen:
            old_time, old_text = self._seen[0]
            self._seen_set.discard((old_time, old_text))
            old_t = _esp_seconds(old_time)
            lst = self._seen_t.get(old_text)
            if lst is not None:
                if old_t in lst:
                    lst.remove(old_t)
                if not lst:
                    del self._seen_t[old_text]
        self._seen.append(key)
        self._seen_set.add(key)
        if t is not None:
            self._seen_t.setdefault(text, []).append(t)
        return False

    def _schedule_notify(self) -> None:
        """Update entities at most once per second (the bus can be busy)."""
        if self._notify_handle is not None:
            return
        loop = asyncio.get_running_loop()

        def _fire() -> None:
            self._notify_handle = None
            self._on_update()

        self._notify_handle = loop.call_later(1, _fire)

    async def _run(self) -> None:
        while True:
            reason = ""
            try:
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep retrying
                reason = str(err) or type(err).__name__
                _LOGGER.debug("EMS-ESP log stream stopped: %s", reason)
            if self.connected:
                self.connected = False
                self._quiet = reason.startswith("seit ")
                if not self._quiet:
                    self.note(f"Log-Verbindung getrennt ({reason}) – neuer Versuch in {RECONNECT_SECONDS} s")
            self._on_update()
            await asyncio.sleep(RECONNECT_SECONDS)

    async def _uptime_now(self) -> float | None:
        status = await self._api.get_status()
        return _uptime_seconds(status.get("uptime"))

    async def _restarted(self) -> bool:
        """After a silent period: did the gateway restart (or is it gone)?

        A restarted ESP never closes the old TCP connection, so without this
        check the stream would wait forever (seen 07.10.2026 after an update).
        """
        try:
            now = await self._uptime_now()
        except Exception:  # noqa: BLE001 - not reachable -> reconnect
            return True
        if now is None or self._uptime is None:
            self._uptime = now
            return False
        restarted = now + 5 < self._uptime
        self._uptime = now
        return restarted

    async def _listen(self) -> None:
        headers = {"Accept": "text/event-stream"}
        if self._api.token:
            headers["Authorization"] = f"Bearer {self._api.token}"
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=None)
        async with self._api.session.get(
            self._api.log_url(), headers=headers, timeout=timeout
        ) as resp:
            if resp.status in (401, 403):
                # token expired (e.g. after an EMS-ESP restart or password change):
                # sign in again, otherwise every retry fails the same way
                try:
                    await self._api.sign_in()
                except Exception as err:  # noqa: BLE001
                    raise RuntimeError(f"HTTP {resp.status}, Anmeldung fehlgeschlagen ({err})") from None
                raise RuntimeError(f"HTTP {resp.status} – neu angemeldet")
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            self.connected = True
            self.connects += 1
            try:
                self._uptime = await self._uptime_now()
            except Exception:  # noqa: BLE001
                self._uptime = None
            self._on_update()
            if self.on_connect is not None:
                try:
                    await self.on_connect(self._quiet)
                except Exception:  # noqa: BLE001 - never break the stream
                    _LOGGER.debug("on_connect failed", exc_info=True)
            silent = 0.0
            while True:
                try:
                    raw = await asyncio.wait_for(resp.content.readline(), STALL_SECONDS)
                except TimeoutError:
                    silent += STALL_SECONDS
                    if silent >= MAX_SILENT_SECONDS:
                        raise RuntimeError(f"seit {int(silent)} s keine Daten") from None
                    if await self._restarted():
                        raise RuntimeError("EMS-ESP neu gestartet oder nicht erreichbar") from None
                    continue
                if not raw:
                    raise RuntimeError("Verbindung vom EMS-ESP geschlossen")
                silent = 0.0
                self._quiet = False
                self.last_rx = time.time()
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data:") and self._add(line[5:].strip()):
                    raise RuntimeError("EMS-ESP startet neu")
