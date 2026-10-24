"""Samsung EHS: websocket commands of the panel – status and logs of EHS Sentinel.

Taken over from the separate "Samsung EHS Panel" (v1.1.1); the commands are
now ``ems_esp_manager/samsung/<name>``.
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from ..const import DOMAIN, EHS_DOMAIN, MANAGER_VERSION as VERSION

WS = f"{DOMAIN}/samsung"
_DATA = f"{DOMAIN}_samsung"

LEVELS = {"all": 0, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


def _buffer(hass: HomeAssistant):
    return hass.data.get(_DATA, {}).get("logbuffer")


def _instances(hass: HomeAssistant) -> list[dict[str, Any]]:
    coords = hass.data.get(EHS_DOMAIN, {}) or {}
    out = []
    for entry in hass.config_entries.async_entries(EHS_DOMAIN):
        c = coords.get(entry.entry_id) if isinstance(coords, dict) else None
        stats = {}
        queue = None
        if c is not None:
            try:
                stats = dict(getattr(c, "stats", {}) or {})
            except Exception:  # pylint: disable=broad-except
                stats = {}
            q = getattr(c, "_packet_queue", None)
            try:
                queue = q.qsize() if q is not None else None
            except Exception:  # pylint: disable=broad-except
                queue = None
        out.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "state": str(entry.state.value if hasattr(entry.state, "value") else entry.state),
                "loaded": c is not None,
                "connected": bool(c is not None and getattr(c, "_tcp_reader", None) is not None),
                "running": bool(getattr(c, "running", False)) if c is not None else False,
                "ip": getattr(c, "ip", None) if c is not None else (entry.options.get("ip") or entry.data.get("ip")),
                "port": getattr(c, "port", None) if c is not None else (entry.options.get("port") or entry.data.get("port")),
                "write_mode": getattr(c, "writemode", None) if c is not None else None,
                "polling": getattr(c, "polling", None) if c is not None else None,
                "extended_logging": getattr(c, "extended_logging", None) if c is not None else None,
                "stats": stats,
                "queue": queue,
            }
        )
    return out


@websocket_api.websocket_command({vol.Required("type"): f"{WS}/status"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_status(hass: HomeAssistant, connection, msg) -> None:
    buf = _buffer(hass)
    connection.send_result(
        msg["id"],
        {"version": VERSION, "instances": _instances(hass), "log_level": buf.current_level() if buf else "default"},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS}/logs",
        vol.Optional("since", default=0): int,
        vol.Optional("level", default="all"): vol.In(list(LEVELS)),
        vol.Optional("limit", default=500): vol.All(int, vol.Range(min=1, max=2000)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_logs(hass: HomeAssistant, connection, msg) -> None:
    buf = _buffer(hass)
    if buf is None:
        connection.send_error(msg["id"], "not_ready", "Protokoll-Aufzeichnung nicht aktiv")
        return
    connection.send_result(msg["id"], {"entries": buf.entries(msg["since"], LEVELS[msg["level"]], msg["limit"])})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS}/log_level", vol.Required("level"): vol.In(["default", "info", "debug"])}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_log_level(hass: HomeAssistant, connection, msg) -> None:
    buf = _buffer(hass)
    connection.send_result(msg["id"], {"level": buf.set_level(msg["level"]) if buf else "default"})


@websocket_api.websocket_command({vol.Required("type"): f"{WS}/clear_logs"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_clear(hass: HomeAssistant, connection, msg) -> None:
    buf = _buffer(hass)
    if buf:
        buf.clear()
        buf.add_marker("Protokoll geleert")
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): f"{WS}/diagnostics"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_diagnostics(hass: HomeAssistant, connection, msg) -> None:
    """Diagnose von EHS Sentinel anfordern (Task-Übersicht, Warteschlange, Paketzähler → Protokoll)."""
    if hass.services.has_service(EHS_DOMAIN, "request_diagnostic_logs"):
        await hass.services.async_call(EHS_DOMAIN, "request_diagnostic_logs", {}, blocking=True)
        connection.send_result(msg["id"], {"ok": True})
    else:
        connection.send_error(msg["id"], "not_found", "Dienst ehs_sentinel.request_diagnostic_logs nicht vorhanden")


@websocket_api.websocket_command({vol.Required("type"): f"{WS}/reconnect", vol.Required("entry_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_reconnect(hass: HomeAssistant, connection, msg) -> None:
    """EHS-Sentinel-Integration neu laden → Verbindung neu aufbauen."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != EHS_DOMAIN:
        connection.send_error(msg["id"], "not_found", "EHS-Sentinel-Eintrag nicht gefunden")
        return
    buf = _buffer(hass)
    if buf:
        buf.add_marker(f"Neu verbinden: {entry.title}")
    ok = await hass.config_entries.async_reload(entry.entry_id)
    connection.send_result(msg["id"], {"ok": bool(ok)})


def _packet_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path("www", EHS_DOMAIN, "logs"))


def _read_packets(path: Path, lines: int) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return list(deque(fh, maxlen=lines))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS}/packet_log",
        vol.Optional("file"): str,
        vol.Optional("lines", default=200): vol.All(int, vol.Range(min=10, max=2000)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_packet_log(hass: HomeAssistant, connection, msg) -> None:
    """Paket-Log von EHS Sentinel (www/ehs_sentinel/logs/packet_*.log) – nur bei aktivem erweiterten Logging."""
    folder = _packet_dir(hass)

    def _list() -> list[str]:
        if not folder.is_dir():
            return []
        return sorted((p.name for p in folder.glob("packet_*.log")), reverse=True)

    files = await hass.async_add_executor_job(_list)
    name = msg.get("file") or (files[0] if files else None)
    lines: list[str] = []
    if name and name in files:
        lines = await hass.async_add_executor_job(_read_packets, folder / name, msg["lines"])
    connection.send_result(msg["id"], {"files": files, "file": name, "lines": [l.rstrip("\n") for l in lines]})


def async_register(hass: HomeAssistant) -> None:
    for cmd in (ws_status, ws_logs, ws_log_level, ws_clear, ws_diagnostics, ws_reconnect, ws_packet_log):
        websocket_api.async_register_command(hass, cmd)
