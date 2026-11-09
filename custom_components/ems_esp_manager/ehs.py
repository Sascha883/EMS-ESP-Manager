"""Samsung EHS heat pump in the EMS-ESP Manager (data from "EHS Sentinel").

Chosen at the first setup ("Samsung EHS"). There is no gateway to talk to:
the HACS integration EHS Sentinel delivers the entities, the manager adds

- the panel (same file as the Bosch EHS, Samsung colours and menus) at
  /samsung-ehs in the sidebar (old address /samsung-hmi still works),
- the settings dashboard "EMS-EHS" (like "EMS-ESP" for Bosch): EHS Sentinel status,
  all settings (FSV), values, mapping and log,
- connection status, log recording and packet log of EHS Sentinel
  (websocket commands ems_esp_manager/samsung/*),
- the shared functions of the manager (Heizstab, kiosk link, installer info).

Replaces the separate integration "Samsung EHS Panel" (samsung_ehs_panel).
Everything Samsung lives in this one file (v1.13.3: before in the folder samsung/).
"""

from __future__ import annotations

from collections import deque
import logging
from pathlib import Path
import re
import threading
import time
import traceback
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, persistent_notification, websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_ADMIN_ONLY,
    CONF_SHOW_IN_SIDEBAR,
    DOMAIN,
    EHS_DOMAIN,
    MANAGER_VERSION as VERSION,
    SAMSUNG_SIDEBAR_URL,
    VENDOR_SAMSUNG,
    entry_vendor,
)
from .wpa import inject as wpa_inject

_LOGGER = logging.getLogger(__name__)
WS = f"{DOMAIN}/samsung"
_DATA = f"{DOMAIN}_samsung"


# =====================================================================
# Setup
# =====================================================================

OLD_DOMAIN = "samsung_ehs_panel"
_NOTE_OLD = f"{DOMAIN}_samsung_old_panel"
_NOTE_EHS = f"{DOMAIN}_samsung_no_ehs"


async def async_setup_samsung(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Samsung EHS part (one entry)."""
    from .panel import async_setup_panel

    from .buslog import async_log_flag

    data = hass.data.setdefault(_DATA, {})
    if "logbuffer" not in data:
        data["logbuffer"] = EhsLogBuffer()
        data["logbuffer"].enabled = await async_log_flag(hass, "ehs")
    data["logbuffer"].attach()
    if not data.get("ws_registered"):
        _ws_register(hass)
        data["ws_registered"] = True

    try:
        await async_setup_panel(hass, dict(entry.options), VENDOR_SAMSUNG)
    except Exception:  # noqa: BLE001 - the panel must never break the integration
        _LOGGER.exception("Samsung EHS panel could not be set up")

    await async_setup_hub(hass, entry)
    _notify(hass)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_samsung(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove the Samsung sidebar entry and stop the log recording."""
    from .panel import async_remove_panel

    buf = hass.data.get(_DATA, {}).get("logbuffer")
    if buf:
        buf.detach()
    async_remove_panel(hass, VENDOR_SAMSUNG)
    async_release_hub(hass, entry)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from .panel import async_setup_panel

    await async_setup_panel(hass, dict(entry.options), VENDOR_SAMSUNG)
    await async_refresh_ehs_page(hass)


def _notify(hass: HomeAssistant) -> None:
    """Hints: EHS Sentinel missing / old separate panel still installed."""
    if hass.config_entries.async_entries(EHS_DOMAIN):
        persistent_notification.async_dismiss(hass, _NOTE_EHS)
    else:
        persistent_notification.async_create(
            hass,
            "Für Samsung EHS braucht der EMS-ESP Manager die HACS-Integration "
            "**EHS Sentinel** (Verbindung zum RS485-Adapter der Wärmepumpe). "
            "Bitte über HACS installieren und einrichten – das Panel „Samsung EHS“ "
            "zeigt die Werte danach automatisch.",
            title="EMS-ESP Manager – Samsung EHS",
            notification_id=_NOTE_EHS,
        )
    old = [
        e
        for e in hass.config_entries.async_entries(OLD_DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if old:
        persistent_notification.async_create(
            hass,
            "Das Samsung-Panel ist jetzt im EMS-ESP Manager enthalten (Seitenleiste "
            "„Samsung EHS“). Die separate Integration **Samsung EHS Panel** wird nicht "
            "mehr gebraucht: unter Einstellungen → Geräte & Dienste löschen und in HACS "
            "entfernen. Eigene Zuordnungen aus dem alten Panel übernimmt das neue beim "
            "ersten Öffnen als Administrator im selben Browser.",
            title="EMS-ESP Manager – Samsung EHS",
            notification_id=_NOTE_OLD,
        )
    else:
        persistent_notification.async_dismiss(hass, _NOTE_OLD)


# =====================================================================
# Log-Puffer für EHS Sentinel
# =====================================================================
EHS_LOGGER = "custom_components.ehs_sentinel"


class EhsLogBuffer(logging.Handler):
    """Hält die letzten Meldungen von EHS Sentinel im Speicher (wie der EMS-ESP Manager)."""

    def __init__(self, size: int = 2000) -> None:
        super().__init__(level=logging.NOTSET)
        self._buf: deque[dict] = deque(maxlen=size)
        self._next_id = 1
        self._lock_buf = threading.Lock()
        self._orig_level: int | None = None
        self.enabled = False  # Aufzeichnung im Panel ein-/ausschaltbar – Standard aus, Schalter wird gespeichert

    # ---- logging.Handler ----
    def emit(self, record: logging.LogRecord) -> None:
        if not self.enabled:
            return
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
            name = record.name.replace(EHS_LOGGER, "ehs_sentinel")
            with self._lock_buf:
                self._buf.append(
                    {"id": self._next_id, "ts": record.created, "level": record.levelname, "name": name, "msg": msg[:4000]}
                )
                self._next_id += 1
        except Exception:  # pylint: disable=broad-except
            self.handleError(record)

    # ---- Abfrage ----
    def entries(self, since: int = 0, min_level: int = 0, limit: int = 500) -> list[dict]:
        with self._lock_buf:
            items = [e for e in self._buf if e["id"] > since and logging.getLevelNamesMapping().get(e["level"], 0) >= min_level]
        return items[-limit:]

    def clear(self) -> None:
        with self._lock_buf:
            self._buf.clear()

    # ---- Ein-/Aushängen, Protokollstufe ----
    def attach(self) -> None:
        logger = logging.getLogger(EHS_LOGGER)
        if self not in logger.handlers:
            logger.addHandler(self)
        if self.enabled:
            self.add_marker("Protokoll-Aufzeichnung gestartet")

    def detach(self) -> None:
        self.set_level("default")
        logging.getLogger(EHS_LOGGER).removeHandler(self)

    def set_level(self, level: str) -> str:
        logger = logging.getLogger(EHS_LOGGER)
        if self._orig_level is None:
            self._orig_level = logger.level
        if level == "debug":
            logger.setLevel(logging.DEBUG)
        elif level == "info":
            logger.setLevel(logging.INFO)
        else:
            logger.setLevel(self._orig_level)
            self._orig_level = None
            level = "default"
        self.add_marker(f"Protokollstufe: {level}")
        return level

    def current_level(self) -> str:
        if self._orig_level is None:
            return "default"
        lvl = logging.getLogger(EHS_LOGGER).level
        return "debug" if lvl <= logging.DEBUG else "info"

    def add_marker(self, text: str) -> None:
        with self._lock_buf:
            self._buf.append({"id": self._next_id, "ts": time.time(), "level": "INFO", "name": "panel", "msg": f"— {text} —"})
            self._next_id += 1


# =====================================================================
# Websocket-Befehle (Verbindung, Protokoll, Paket-Log)
# =====================================================================

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
    from homeassistant.const import __version__ as ha_version
    from homeassistant.loader import async_get_integration

    from .panel import get_manager

    buf = _buffer(hass)
    try:
        ehs_version = str((await async_get_integration(hass, EHS_DOMAIN)).version or "")
    except Exception:  # noqa: BLE001 – EHS Sentinel nicht installiert
        ehs_version = ""
    mgr = get_manager(hass)
    opts = mgr.opts(VENDOR_SAMSUNG) if mgr else {}
    connection.send_result(
        msg["id"],
        {
            "version": VERSION,
            "ehs_version": ehs_version,
            "ha_version": ha_version,
            "installer": {
                "name": opts.get("installer_name", ""),
                "addr": opts.get("installer_addr", ""),
                "tel": opts.get("installer_tel", ""),
            },
            "instances": _instances(hass),
            "vendors": list(mgr.vendors) if mgr else [],
            "log_level": buf.current_level() if buf else "default",
            "log_enabled": bool(buf.enabled) if buf else False,
        },
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


@websocket_api.websocket_command({vol.Required("type"): f"{WS}/log_enable", vol.Required("enabled"): bool})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_log_enable(hass: HomeAssistant, connection, msg) -> None:
    from .buslog import async_set_log_flag

    await async_set_log_flag(hass, "ehs", msg["enabled"])
    buf = _buffer(hass)
    if buf:
        if msg["enabled"]:
            buf.enabled = True
            buf.add_marker("Aufzeichnung eingeschaltet")
        else:
            buf.add_marker("Aufzeichnung ausgeschaltet")
            buf.enabled = False
    connection.send_result(msg["id"], {"enabled": bool(buf and buf.enabled)})


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


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/bosch/dashboard"})
@callback
def ws_bosch_dashboard(hass: HomeAssistant, connection, msg) -> None:
    """Inhalt des bisherigen Dashboards „EMS-ESP“ – Reiter „Bosch“ in EMS-EHS (v1.22.0)."""
    from .dashboard_builder import build_dashboard

    try:
        connection.send_result(msg["id"], build_dashboard(hass))
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("Bosch-Ansicht konnte nicht erstellt werden")
        connection.send_error(msg["id"], "bosch_dashboard", str(err))


def _ws_register(hass: HomeAssistant) -> None:
    for cmd in (ws_bosch_dashboard, ws_status, ws_logs, ws_log_level, ws_log_enable, ws_clear, ws_diagnostics, ws_reconnect, ws_packet_log):
        websocket_api.async_register_command(hass, cmd)


# =====================================================================
# EMS-EHS – eigene Seite in der Seitenleiste (Übersicht, Einstellungen,
# Messwerte, Protokoll). Design wie die Info-Seite des TA Managers.
# Ab v1.13.6 ein eigenes Panel statt eines Lovelace-Dashboards: kein
# Zwischenspeicher im Browser, keine iframes im Dashboard.
# =====================================================================
EHS_DASHBOARD_URL = "ems-ehs"
EHS_DASHBOARD_TITLE = "EMS-EHS"
EHS_DASHBOARD_ICON = "mdi:heat-pump-outline"
URL_EHS_APP = f"/api/{DOMAIN}/ehs"
# Brötje (ESP-Bus-Adapter): gleiche Seite im Modus „bsb“
BSB_DASHBOARD_URL = "ems-bsb"
BSB_DASHBOARD_TITLE = "Brötje EHS"   # Vorgabe; tatsächlich „<Hersteller> EHS“
BSB_DASHBOARD_ICON = "mdi:water-boiler"
URL_BSB_APP = f"/api/{DOMAIN}/bsb_app"
# „EMS-BSB“: Bedienfeld im Bosch-Stil (Startseite mit Kacheln) für den Brötje-Regler
URL_BSB_DASH_PANEL = f"/api/{DOMAIN}/panel?vendor=bsb"
_DATA_BSB_DASH = f"{DOMAIN}_bsb_dashboard"
_DATA_DASH = f"{DOMAIN}_ehs_dashboard"

FSV_GROUPS = {
    "1": "FSV 10xx – Grenzwerte",
    "2": "FSV 20xx – Heizkurve",
    "3": "FSV 30xx – Warmwasser",
    "4": "FSV 40xx – Heizung & Zuheizer",
    "5": "FSV 50xx – PV, Smart Grid & Sonstiges",
}
VALUE_GROUPS = (   # (Titel, Muster auf den Namen nach „ehssentinel_“)
    ("Status & Störungen", r"error|status|opmode|operation|valve|power$|outing|silence|defrost(step)?$|backupheater|booster"),
    ("Temperaturen", r"temp|tw\d|evain|suction|discharge"),
    ("Verdichter & Kältekreis", r"comp|press|eev|fan|4way|overheat|hz$"),
    ("Energie & Arbeitszahl", r"watt|cop|output|generated|consumed|energy|accum"),
    ("Hydraulik", r"flow|pump|pwm|water"),
    ("Laufzeiten & Zähler", r"minutes|counter|starts|hours"),
)

EHS_LABELS: dict[str, str] = {
    "infsv3021": "WP max. Temperatur (3021)",
    "infsv3022": "WP Stopp-Diff. (3022)",
    "infsv3023": "WP Start-Diff. (3023)",
    "power": "Wärmepumpe",
    "indooropmode": "Betriebsart",
    "controlsilence": "Flüstermodus",
    "indooroutgoing": "Abwesenheit (Outing)",
    "powerzone2": "Zone 2",
    "intempwateroutlettargetzone2f": "Zone 2 Vorlauf-Soll",
    "outdoorcomp1orderhz": "Frequenz-Vorgabe",
    "outcompfreqratecontrol": "Leistungsbegrenzung (FSV 5051)",
    "outdoorfanrpm1": "Lüfter Außengerät",
    "ehssentineldefrostcounter": "Abtauvorgänge",
    "outdoordefroststep": "Abtauung",
    "inboosterheater": "Booster-Heizstab",
    "infsv1031": "Vorlauf max. (1031)",
    "infsv1032": "Vorlauf min. (1032)",
    "infsv1041": "Raum max. (1041)",
    "infsv1042": "Raum min. (1042)",
    "infsv1051": "Speicher max. (1051)",
    "infsv1052": "Speicher min. (1052)",
    "infsv1011": "Vorlauf max. (1011)",
    "infsv1012": "Vorlauf min. (1012)",
    "infsv1021": "Raum max. (1021)",
    "infsv1022": "Raum min. (1022)",
    "infsv2011": "Außentemp. kalt (2011)",
    "infsv2012": "Außentemp. warm (2012)",
    "infsv2021": "Vorlauf bei kalt WL1 (2021)",
    "infsv2022": "Vorlauf bei warm WL1 (2022)",
    "infsv2031": "Vorlauf bei kalt WL2 (2031)",
    "infsv2032": "Vorlauf bei warm WL2 (2032)",
    "infsv2041": "Heizkurven-Typ (2041)",
    "infsv2051": "Außentemp. max (2051)",
    "infsv2052": "Außentemp. min (2052)",
    "infsv2061": "Vorlauf WL1 max (2061)",
    "infsv2062": "Vorlauf WL1 min (2062)",
    "infsv2071": "Vorlauf WL2 max (2071)",
    "infsv2072": "Vorlauf WL2 min (2072)",
    "infsv2081": "Kühlkurven-Typ (2081)",
    "infsv2091": "Ext. Thermostat WL1 (2091)",
    "infsv2092": "Ext. Thermostat WL2 (2092)",
    "infsv3011": "WW-Anwendung (3011)",
    "infsv3024": "Min. Heizlaufzeit (3024)",
    "infsv3025": "Max. WW-Laufzeit (3025)",
    "infsv3026": "Max. Heizlaufzeit (3026)",
    "infsv3032": "Verzögerung (3032)",
    "infsv3041": "Desinfektion (3041)",
    "infsv3042": "Intervall (3042)",
    "infsv3043": "Startzeit (3043)",
    "infsv3044": "Temperatur (3044)",
    "infsv3045": "Dauer (3045)",
    "infsv3046": "Max. Zeit (3046)",
    "infsv3051": "Zeitbegrenzung (3051)",
    "infsv3052": "Dauer (3052)",
    "infsv3061": "Solar / Thermostat (3061)",
    "infsv3071": "3-Wege-Ventil Richtung (3071)",
    "infsv3081": "BUH Stufe 1 Leistung (3081)",
    "infsv3082": "BUH Stufe 2 Leistung (3082)",
    "infsv3083": "BSH Leistung (3083)",
    "infsv4011": "Vorrang Heizen/WW (4011)",
    "infsv4012": "Außentemp. für Vorrang (4012)",
    "infsv4013": "Heizgrenze (4013)",
    "infsv4021": "Anwendung (4021)",
    "infsv4022": "BUH/BSH Vorrang (4022)",
    "infsv4023": "Kaltwetter-Kompensation (4023)",
    "infsv4024": "Schwelltemperatur (4024)",
    "infsv4025": "Abtau-Backup Temp. (4025)",
    "infsv4031": "Anwendung (4031)",
    "infsv4032": "Kesselvorrang (4032)",
    "infsv4033": "Schwelle (4033)",
    "infsv4041": "Mischer (4041)",
    "infsv4042": "ΔT Heizen (4042)",
    "infsv4043": "ΔT Kühlen (4043)",
    "infsv4044": "Regelfaktor (4044)",
    "infsv4045": "Regelfaktor (4045)",
    "infsv4046": "Laufzeit (4046)",
    "infsv4051": "Inverter-Pumpe (4051)",
    "infsv4052": "Pumpe ΔT (4052)",
    "infsv4053": "Regelfaktor (4053)",
    "infsv4061": "Zonenregelung (4061)",
    "infsv5013": "Vorlauf Heizen (5013)",
    "infsv5014": "Raum Heizen (5014)",
    "infsv5017": "Auto Heizen WL1 (5017)",
    "infsv5018": "Auto Heizen WL2 (5018)",
    "infsv5019": "Speicher (5019)",
    "infsv5011": "Vorlauf Kühlen (5011)",
    "infsv5012": "Raum Kühlen (5012)",
    "infsv5022": "Sparbetrieb (5022)",
    "infsv5021": "Spartemperatur (5021)",
    "infsv5023": "Thermo-on Temp. (5023)",
    "infsv5041": "Leistungsspitzen-Steuerung (5041)",
    "infsv5042": "Zwangsabschaltung (5042)",
    "infsv5043": "Eingangsspannung (5043)",
    "infsv5051": "Frequenzverhältnis (5051)",
    "infsv5061": "WW/Heizen-Verhältnis (5061)",
    "infsv5081": "PV-Steuerung (5081)",
    "infsv5083": "Anhebung Heizen (5083)",
    "infsv5082": "Absenkung Kühlen (5082)",
    "infsv5091": "Smart Grid (5091)",
    "infsv5092": "Anhebung Heizen (5092)",
    "infsv5093": "Anhebung WW (5093)",
    "infsv5094": "WW-Modus (5094)",
    "indoorwaterouttemp": "Vorlauf Innengerät",
    "indoorwaterintemp": "Rücklauf Innengerät",
    "outdoortw2temp": "TW2 Vorlauf Außengerät",
    "outdoortw1temp": "TW1 Rücklauf Außengerät",
    "inflowsensorcalc": "Durchfluss",
    "inwaterpumppwmvalue": "Heizungspumpe",
    "dhwvalve": "3-Wege-Ventil",
    "indoordhwcurrenttemp": "Speichertemperatur",
    "outdooroperationstatus": "Status Außengerät",
    "outdoorcomp1runhz": "Verdichterfrequenz",
    "outdoorcomp1targethz": "Zielfrequenz",
    "outdoordischargetemp1": "Heißgas (Discharge)",
    "outdoorsuction1temp": "Sauggas",
    "outdoorsuctionoverheat": "Sauggas-Überhitzung",
    "outdoorhighpress": "Hochdruck",
    "outdoorhighpresstemp": "Hochdruck Sättigungstemp.",
    "outdoorlowpress": "Niederdruck",
    "outdoorlowpresstemp": "Niederdruck Sättigungstemp.",
    "outdoorevain": "Verdampfereintritt",
    "outdoormaineev1": "Expansionsventil (EEV)",
    "outdoor4wayvalve": "4-Wege-Ventil (Abtauen)",
    "outdoorcontrolwattmeterallunit": "Elektr. Leistung",
    "ehssentinelheatoutput": "Heizleistung",
    "ehssentinelcop": "COP aktuell",
    "inbackupheater": "Backup-Heizer",
    "outerrorcode": "Fehlercode Außengerät",
    "errorcode2": "Fehlercode 2",
    "errorcode3": "Fehlercode 3",
    "errorcode4": "Fehlercode 4",
    "errorcode5": "Fehlercode 5",
    "ehssentineldailyconsumedpower": "Strom verbraucht heute",
    "ehssentineldailygeneratedpower": "Wärme erzeugt heute",
    "ehssentineldailycop": "Arbeitszahl heute",
    "ehssentineldailycopheatmode": "Arbeitszahl Heizen heute",
    "ehssentineldailycopdhwmode": "Arbeitszahl Warmwasser heute",
    "ehssentineldailyminutesactive": "Laufzeit heute",
    "outdoorcontrolwattmeterallunitaccum": "Strom Außengerät",
    "intotalgeneratedpower": "Wärme erzeugt gesamt",
    "ehssentineltotalcop": "Arbeitszahl gesamt",
    "ehssentineltotalconsumedpowerheatmode": "Strom Heizen",
    "ehssentineltotalgeneratedpowerheatmode": "Wärme Heizen",
    "ehssentineltotalcopheatmode": "Arbeitszahl Heizen",
    "ehssentineltotalminutesactiveheatmode": "Laufzeit Heizen",
    "ehssentineltotalconsumedpowerdhwmode": "Strom Warmwasser",
    "ehssentineltotalgeneratedpowerdhwmode": "Wärme Warmwasser",
    "ehssentineltotalcopdhwmode": "Arbeitszahl Warmwasser",
    "ehssentineltotalminutesactivedhwmode": "Laufzeit Warmwasser"
}


def _obj(entity_id: str) -> str:
    return entity_id.split(".", 1)[1].split("ehssentinel_", 1)[-1]


def _fsv_key(obj: str) -> str:
    m = re.match(r"infsv(\d)(\d+)", obj)
    return f"{m.group(1)}{m.group(2)}" if m else obj


def ehs_groups(hass: HomeAssistant) -> dict[str, list[dict[str, Any]]]:
    """Alle Entitäten von EHS Sentinel, gruppiert für die Seiten Einstellungen und Messwerte."""
    ent_reg = er.async_get(hass)
    ents = sorted(
        (e for e in ent_reg.entities.values() if e.platform == EHS_DOMAIN and not e.disabled_by),
        key=lambda e: e.entity_id,
    )

    def item(e) -> dict[str, Any]:
        o = _obj(e.entity_id)
        return {"id": e.entity_id, "label": EHS_LABELS.get(o) or e.name or e.original_name or o, "key": o}

    settings: dict[str, list] = {"Betrieb": []}
    for k in sorted(FSV_GROUPS):
        settings[FSV_GROUPS[k]] = []
    values: dict[str, list] = {t: [] for t, _ in VALUE_GROUPS}
    values["FSV (nur lesen)"] = []
    rest: list = []
    for e in ents:
        o = _obj(e.entity_id)
        fsv = o.startswith("infsv") and o[5:6] in FSV_GROUPS
        if e.domain in ("number", "select", "switch", "button"):
            settings[FSV_GROUPS[o[5]] if fsv else "Betrieb"].append(item(e))
        elif e.domain in ("sensor", "binary_sensor"):
            if fsv:
                values["FSV (nur lesen)"].append(item(e))
                continue
            for title, pat in VALUE_GROUPS:
                if re.search(pat, o):
                    values[title].append(item(e))
                    break
            else:
                rest.append(item(e))
    values["Sonstiges"] = rest
    for g in settings.values():
        g.sort(key=lambda i: _fsv_key(i["key"]))
    return {
        "settings": [{"title": t, "items": i} for t, i in settings.items() if i],
        "values": [{"title": t, "items": i} for t, i in values.items() if i],
        "count": len(ents),
    }


@websocket_api.websocket_command({vol.Required("type"): f"{WS}/entities"})
@websocket_api.async_response
async def ws_entities(hass: HomeAssistant, connection, msg) -> None:
    connection.send_result(msg["id"], ehs_groups(hass))


async def async_register_ehs_dashboard(
    hass: HomeAssistant, show_in_sidebar: bool = True, admin_only: bool = False
) -> None:
    """Seitenleiste „EMS-EHS“ (eigenes Panel) anlegen bzw. aktualisieren."""
    try:
        if not hass.data.get(f"{_DATA_DASH}_view"):
            hass.http.register_view(EhsAppView())
            websocket_api.async_register_command(hass, ws_entities)
            hass.data[f"{_DATA_DASH}_view"] = True
        # Lovelace-Dashboard der v1.13.2–1.13.5 unter demselben Pfad entfernen
        try:
            from homeassistant.components.lovelace.const import LOVELACE_DATA

            lovelace = hass.data.get(LOVELACE_DATA)
            if lovelace is not None and type(lovelace.dashboards.get(EHS_DASHBOARD_URL)).__name__ == "EhsDashboard":
                lovelace.dashboards.pop(EHS_DASHBOARD_URL, None)
        except Exception:  # noqa: BLE001
            pass
        frontend.async_register_built_in_panel(
            hass,
            "iframe",
            sidebar_title=EHS_DASHBOARD_TITLE if show_in_sidebar else None,
            sidebar_icon=EHS_DASHBOARD_ICON if show_in_sidebar else None,
            frontend_url_path=EHS_DASHBOARD_URL,
            config={"url": URL_EHS_APP},
            require_admin=admin_only,
            update=True,
        )
        hass.data[_DATA_DASH] = True
    except Exception:  # noqa: BLE001 - die Oberfläche darf die Integration nie stören
        _LOGGER.exception("Seite „EMS-EHS“ konnte nicht angelegt werden")


_DATA_HUB = f"{DOMAIN}_ehs_hub"


async def async_setup_hub(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """„EMS-EHS“ mit „System & Panel“ – ab v1.20.3 bei jedem Hersteller.

    Egal ob nur Bosch, Samsung, Heizstab oder ESP-Bus-Adapter eingerichtet ist:
    die Seite (und damit „System & Panel“) ist immer da. Die Reiter „Samsung“,
    „Heizstab“ und „<Hersteller> EHS“ erscheinen nur, wenn das Gerät eingerichtet ist.
    """
    try:
        users: set[str] = hass.data.setdefault(_DATA_HUB, set())
        first = not users
        users.add(entry.entry_id)
        data = hass.data.setdefault(_DATA, {})
        if not data.get("ws_registered"):
            _ws_register(hass)
            data["ws_registered"] = True
        await async_refresh_ehs_page(hass)
    except Exception:  # noqa: BLE001 - die Oberfläche darf die Integration nie stören
        _LOGGER.exception("Seite „EMS-EHS“ (System & Panel) konnte nicht angelegt werden")


def ehs_page_options(hass: HomeAssistant) -> tuple[bool, bool]:
    """(in Seitenleiste, nur Admin) der Seite „EMS-EHS“.

    Ab v1.22.0 gemeinsam für alle Geräte: Samsung, sonst Bosch, sonst Heizstab
    bestimmt (das Einstellungs-Menü schreibt den Wert in alle drei). Der
    ESP-Bus-Adapter hat mit „Brötje EHS“ eine eigene Seite."""
    from .const import DOMAIN as _D, VENDOR_BOSCH, VENDOR_HEATER

    entries = hass.config_entries.async_entries(_D)
    for v in (VENDOR_SAMSUNG, VENDOR_BOSCH, VENDOR_HEATER):
        for e in entries:
            if entry_vendor(e) == v:
                return bool(e.options.get(CONF_SHOW_IN_SIDEBAR, True)), bool(e.options.get(CONF_ADMIN_ONLY, False))
    return True, False


async def async_refresh_ehs_page(hass: HomeAssistant) -> None:
    """Seite „EMS-EHS“ mit den gemeinsamen Einstellungen (neu) anlegen."""
    if not hass.data.get(_DATA_HUB):
        return
    show, admin = ehs_page_options(hass)
    await async_register_ehs_dashboard(hass, show_in_sidebar=show, admin_only=admin)


@callback
def async_release_hub(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Seite „EMS-EHS“ erst entfernen, wenn kein Gerät des Managers mehr geladen ist."""
    users: set[str] = hass.data.get(_DATA_HUB, set())
    users.discard(entry.entry_id)
    if not users:
        async_remove_ehs_dashboard(hass)


@callback
def async_remove_ehs_dashboard(hass: HomeAssistant) -> None:
    if not hass.data.pop(_DATA_DASH, False):
        return
    try:
        frontend.async_remove_panel(hass, EHS_DASHBOARD_URL)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Seite „EMS-EHS“ entfernen fehlgeschlagen", exc_info=True)


# SM-Haustechnik-Logo wie beim TA Manager (runder dunkler Hintergrund, Haus, SM, HAUSTECHNIK)
# – fest eingebettet, die Seite braucht keine weitere Datei.
_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="50" fill="#262d38"/><g fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M30 44V76H70V44" stroke="#fff" stroke-width="3.2"/><path d="M21 46L50 21L79 46" stroke="#e1252f" stroke-width="5.5"/><path d="M76 13.5v3M82.6 16.4l-2.1 2.1M85.5 23h-3M82.6 29.6l-2.1-2.1" stroke="#f5b324" stroke-width="1.8"/></g><circle cx="76" cy="23" r="4.6" fill="#f5b324"/><rect x="36" y="50" width="28" height="20" rx="2.5" fill="#fff"/><text x="50" y="65.6" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-weight="900" font-size="14.5" fill="#262d38">SM</text><text x="50" y="86" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-weight="700" font-size="6.4" letter-spacing=".5" fill="#fff">HAUSTECHNIK</text></svg>'
)


def _logo_data_uri() -> str:
    """SM-Logo als data-URI (SVG, base64)."""
    import base64

    return "data:image/svg+xml;base64," + base64.b64encode(_LOGO_SVG.encode()).decode()


# Seitenleisten-Adressen und Namen je Hersteller (für „Panel öffnen“ / „Installierte Geräte“)
# v1.24.0: aus dem Hersteller-Register
from .vendors import panel_paths as _vpaths, vendor_names as _vnames  # noqa: E402

_PANEL_PATHS_JS = _vpaths()
_VENDOR_NAMES_JS = _vnames()


class EhsAppView(HomeAssistantView):
    """GET /api/ems_esp_manager/ehs – nur die Seite; Daten über die HA-Verbindung des Frontends."""

    url = URL_EHS_APP
    name = f"api:{DOMAIN}:ehs"
    requires_auth = False

    async def get(self, request):
        from aiohttp import web

        hass: HomeAssistant = request.app["hass"]
        logo = await hass.async_add_executor_job(_logo_data_uri)
        html = (wpa_inject(EHS_APP_PAGE).replace("__LOGO__", logo).replace("__PANEL_PATH__", "/" + SAMSUNG_SIDEBAR_URL)
                .replace("__PANEL_PATHS__", _PANEL_PATHS_JS).replace("__VENDOR_NAMES__", _VENDOR_NAMES_JS)
                .replace("__MODE__", "samsung"))
        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )


class BsbAppView(HomeAssistantView):
    """GET /api/ems_esp_manager/bsb_app – Dashboard „EMS-BSB“ (Brötje/Siemens BSB) im Aufbau von EMS-EHS."""

    url = URL_BSB_APP
    name = f"api:{DOMAIN}:bsb_app"
    requires_auth = False

    async def get(self, request):
        from aiohttp import web

        hass: HomeAssistant = request.app["hass"]
        logo = await hass.async_add_executor_job(_logo_data_uri)
        html = (wpa_inject(EHS_APP_PAGE).replace("__LOGO__", logo).replace("__PANEL_PATH__", "/esp-bus-adapter").replace("__PANEL_PATHS__", _PANEL_PATHS_JS).replace("__VENDOR_NAMES__", _VENDOR_NAMES_JS)
                .replace("__MODE__", "bsb").replace("<title>EMS-EHS</title>", "<title>EMS-BSB</title>"))
        return web.Response(
            text=html, content_type="text/html", charset="utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )


def bsb_groups(hass: HomeAssistant) -> dict[str, Any]:
    """Entitäten des ESPHome-ESP-Bus-Adapters, gruppiert für Einstellungen und Messwerte."""
    from .bsb import get as _get_bsb

    ad = _get_bsb(hass)
    ents = ad.adapter_entities() if ad is not None else []
    order = [("Heizkreis 1", r"hk1|heizkreis 1|q2\b|\bhk 1"), ("Heizkreis 2", r"hk2|heizkreis 2|q6"),
             ("Trinkwasser", r"trinkwasser|tww|warmwasser|q3"), ("Solar", r"solar|kollektor"),
             ("Kessel & Brenner", r"kessel|brenner|modulation|abgas|wasserdruck"),
             ("Außen & Raum", r"au(ß|ss)en|raum"), ("Regler", r"ger(ä|ae)te|status|software|regler"),
             ("Adapter", r"wlan|laufzeit|ip|verbindung|neustart|led|mitschnitt|firmware")]
    settings: dict[str, list] = {t: [] for t, _ in order}
    values: dict[str, list] = {t: [] for t, _ in order}
    settings["Sonstiges"] = []
    values["Sonstiges"] = []
    for e in ents:
        label = re.sub(r"^(Heizung Br(ö|oe)tje|BSB[- ]Heizung)\s*", "", e["name"]) or e["name"]
        it = {"id": e["entity_id"], "label": label, "key": e["entity_id"].split(".", 1)[1]}
        target = settings if e["domain"] in ("number", "select", "switch", "button") else values
        low = (label + " " + e["entity_id"]).lower()
        for title, pat in order:
            if re.search(pat, low):
                target[title].append(it)
                break
        else:
            target["Sonstiges"].append(it)
    return {
        "settings": [{"title": t, "items": i} for t, i in settings.items() if i],
        "values": [{"title": t, "items": i} for t, i in values.items() if i],
        "count": len(ents),
    }


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/bsb/entities"})
@websocket_api.async_response
async def ws_bsb_entities(hass: HomeAssistant, connection, msg) -> None:
    connection.send_result(msg["id"], bsb_groups(hass))


async def async_register_bsb_dashboard(hass: HomeAssistant, show_in_sidebar: bool = True, admin_only: bool = False) -> None:
    """Seitenleiste „<Hersteller> EHS“ (z. B. „Brötje EHS“, früher „EMS-BSB“) anlegen bzw. aktualisieren.

    Der Titel folgt dem Hersteller, der unter EMS-Regelung → ESP-Bus-Adapter gewählt ist."""
    from .bsb import dashboard_title

    hass.data[f"{_DATA_BSB_DASH}_opts"] = (show_in_sidebar, admin_only)
    title = dashboard_title(hass)
    try:
        if not hass.data.get(f"{_DATA_BSB_DASH}_view"):
            hass.http.register_view(BsbAppView())
            websocket_api.async_register_command(hass, ws_bsb_entities)
            hass.data[f"{_DATA_BSB_DASH}_view"] = True
        frontend.async_register_built_in_panel(
            hass, "iframe",
            sidebar_title=title if show_in_sidebar else None,
            sidebar_icon=BSB_DASHBOARD_ICON if show_in_sidebar else None,
            frontend_url_path=BSB_DASHBOARD_URL, config={"url": URL_BSB_DASH_PANEL},
            require_admin=admin_only, update=True,
        )
        hass.data[_DATA_BSB_DASH] = True
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Seite „EMS-BSB“ konnte nicht angelegt werden")


async def async_refresh_bsb_dashboard(hass: HomeAssistant) -> None:
    """Titel nach Herstellerwechsel neu setzen (nur wenn das Dashboard angelegt ist)."""
    if not hass.data.get(_DATA_BSB_DASH):
        return
    show, admin = hass.data.get(f"{_DATA_BSB_DASH}_opts", (True, False))
    await async_register_bsb_dashboard(hass, show_in_sidebar=show, admin_only=admin)


@callback
def async_remove_bsb_dashboard(hass: HomeAssistant) -> None:
    if not hass.data.pop(_DATA_BSB_DASH, False):
        return
    try:
        frontend.async_remove_panel(hass, BSB_DASHBOARD_URL)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Seite „EMS-BSB“ entfernen fehlgeschlagen", exc_info=True)


EHS_APP_PAGE = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EMS-EHS</title>
<style>
:root{--bg:#000;--card:#111317;--line:#262a30;--sep:#22262b;--txt:#f2f4f7;--k:#d5d9de;--dim:#9aa1ab;--lbl:#868d97;
  --acc:#2196f3;--sam:#3a95e6;--ok:#4caf50;--warn:#f2b632;--err:#e2453a;--in:#0b0d10}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.4 Roboto,"Segoe UI",Arial,sans-serif;-webkit-font-smoothing:antialiased}
button,select,input{font:inherit;color:inherit}
/* Kopf */
.top{position:sticky;top:0;z-index:5;background:var(--bg);padding:4px 22px 0}
.hd{display:flex;align-items:center;gap:14px;max-width:1500px;margin:0 auto}
.hd .lg{width:44px;height:44px;border-radius:50%;background:#262d38 center/100% no-repeat;flex:none}
.hd .tt{font-size:22px;font-weight:700;letter-spacing:.2px}.hd .tt b{color:var(--sam)}
.hd .st{color:var(--dim);font-size:13px}
.hd .sp{flex:1}
.tabs{display:flex;gap:4px;max-width:1500px;margin:12px auto 0;overflow-x:auto;scrollbar-width:none}
.tabs a{padding:10px 16px 12px;color:var(--dim);text-decoration:none;font-size:15px;font-weight:500;white-space:nowrap;border-bottom:2px solid transparent}
.tabs a.on{color:var(--txt);border-bottom-color:var(--sam)}
.vtabs{margin:6px 0 18px;border-bottom:1px solid var(--line);max-width:none}.vtabs a{font-size:14px;padding:9px 14px 11px}
.rule{height:3px;background:linear-gradient(90deg,#3a95e6,#1f6fc0);max-width:1500px;margin:0 auto}
/* Inhalt */
.wrap{max-width:1500px;margin:0 auto;padding:18px 22px 30px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start}
.grid.g3{grid-template-columns:1fr 1fr 1fr}@media (max-width:1250px){.grid.g3{grid-template-columns:1fr 1fr}}
.fr{width:100%;height:calc(100vh - 150px);min-height:420px;border:1px solid var(--line);border-radius:12px;background:#000;display:block}
.col{display:flex;flex-direction:column;gap:18px;min-width:0}
.mas{columns:2 460px;column-gap:18px}.mas .card{break-inside:avoid;margin-bottom:18px}
@media (max-width:860px){.grid,.grid.g3{grid-template-columns:1fr}.wrap,.top{padding-left:12px;padding-right:12px}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px 6px}
.card.pad{padding-bottom:16px}
.cap{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--lbl);font-weight:500;margin:2px 0 4px}
.row{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:11px 0;border-top:1px solid var(--sep);min-height:48px}
.cap+.row,.cap+div>.row:first-child{border-top:0}
.row .k{color:var(--k);font-size:16px;min-width:0}
.row .k small{display:block;color:var(--lbl);font-size:11px;margin-top:1px;word-break:break-all}
.row .v{font-weight:700;font-size:16px;text-align:right;white-space:nowrap}
.row .v small{font-weight:400;color:var(--dim);font-size:13px;margin-left:6px}
.row .v.d{color:var(--dim);font-weight:400}
.pill{display:inline-block;font-size:13px;font-weight:600;padding:3px 10px;border-radius:12px;background:#183b23;color:#7fd99a}
.pill.no{background:#3d1a1a;color:#f19a9a}.pill.dm{background:#3a3000;color:#ffd866}
.brand{display:flex;align-items:center;justify-content:center;gap:18px;padding:8px 0 16px}
.logo{width:100px;height:100px;border-radius:50%;background:#262d38 center/100% no-repeat;flex:none}
.bt{font-size:clamp(20px,2.1vw,27px);font-weight:700;line-height:1.05;white-space:nowrap}.bt b{color:var(--sam)}
.bs{color:var(--dim);font-size:14px;margin-top:4px}
.ins{display:flex;align-items:center;gap:16px;padding:6px 0 10px}
.ins .logo{width:60px;height:60px}
.ins .t2{font-size:20px;font-weight:700;margin-top:2px}
.line{display:flex;gap:12px;color:var(--k);font-size:16px;padding:5px 2px}
.line svg{width:18px;height:18px;flex:none;margin-top:2px;opacity:.75}.line.tel{font-weight:700;color:var(--txt)}
.btns{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0 12px}
.btns.b3{grid-template-columns:1fr 1fr 1fr}@media (max-width:640px){.btns.b3{grid-template-columns:1fr}}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:42px;padding:0 16px;border-radius:9px;border:1px solid var(--line);background:#0c0e11;
  color:var(--txt);font-size:15px;font-weight:600;cursor:pointer;text-decoration:none;white-space:nowrap}
.btn svg{width:17px;height:17px;flex:none}.btn.pri{background:var(--acc);border-color:var(--acc);color:#fff}
.btn.sm{height:34px;padding:0 12px;font-size:13px}.btn:disabled{opacity:.45;cursor:default}.btn:active{filter:brightness(1.15)}
.note{color:var(--dim);font-size:13px;padding:10px 0 12px;border-top:1px solid var(--sep)}
.note.top0{border-top:0}
.msg{font-size:13px;min-height:1em;padding:2px 0 10px}.msg.err{color:var(--err)}.msg.ok{color:var(--ok)}
code{font-size:12px;word-break:break-all;user-select:all;color:#cfe3f7}
/* Bedienelemente */
.ctl{display:flex;align-items:center;gap:8px;flex:none}
.num{display:flex;align-items:center;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--in)}
.num button{width:34px;height:34px;border:0;background:transparent;color:var(--k);font-size:18px;cursor:pointer}
.num button:active{background:#1b1f25}
.num input{width:70px;height:34px;border:0;background:transparent;text-align:center;font-weight:700;font-size:15px;-moz-appearance:textfield;outline:none}
.num input::-webkit-outer-spin-button,.num input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
.u{color:var(--dim);font-size:13px;min-width:26px}
select{height:36px;border:1px solid var(--line);border-radius:8px;background:var(--in);padding:0 30px 0 10px;font-weight:600;max-width:230px;
  appearance:none;background-image:linear-gradient(45deg,transparent 50%,#9aa1ab 50%),linear-gradient(135deg,#9aa1ab 50%,transparent 50%);
  background-position:calc(100% - 15px) 15px,calc(100% - 10px) 15px;background-size:5px 5px;background-repeat:no-repeat}
.tog{width:46px;height:26px;border-radius:13px;background:#3a3f46;position:relative;cursor:pointer;border:0;flex:none}
.tog::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;transition:left .2s}
.tog.on{background:var(--sam)}.tog.on::after{left:23px}
.busy{opacity:.5;pointer-events:none}
.row.pend .num{border-color:var(--sam)}
.flash{animation:fl 1.2s}@keyframes fl{0%{background:#17324a}100%{background:transparent}}
.tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.tools .sp{flex:1}
.q{height:40px;border-radius:9px;border:1px solid var(--line);background:var(--card);padding:0 14px;min-width:240px;outline:none}
.q:focus{border-color:var(--sam)}
.chip{height:34px;padding:0 14px;border-radius:17px;border:1px solid var(--line);background:var(--card);cursor:pointer;font-size:13px;font-weight:600;color:var(--k)}
.chip.on{background:var(--sam);border-color:var(--sam);color:#fff}
/* Protokoll */
.logs{font-size:13px}
.lr{display:flex;gap:12px;padding:7px 0;border-top:1px solid var(--sep);line-height:1.35}
.lr:first-child{border-top:0}
.lr .lt{color:var(--dim);flex:none;font-variant-numeric:tabular-nums}
.lr .ll{flex:none;width:58px;font-weight:700;font-size:11px;padding-top:1px}
.lr .lm{min-width:0;word-break:break-word;white-space:pre-wrap;user-select:text}
.lr .ln{display:block;color:var(--lbl);font-size:11px}
.lr.WARNING .ll{color:var(--warn)}.lr.ERROR .ll,.lr.CRITICAL .ll{color:var(--err)}.lr.ERROR .lm{color:#f3b0aa}.lr.INFO .ll{color:#7fb8ec}.lr.DEBUG .ll,.lr.DEBUG .lm{color:var(--dim)}
.pkt{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;padding:3px 0;border-top:1px solid var(--sep);word-break:break-all;user-select:text}
.empty{color:var(--dim);padding:30px 0;text-align:center}
/* Heizstab */
.htop{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:4px 0 14px}
.hid{display:flex;align-items:center;gap:14px;flex:1;min-width:220px}.hid .t2{font-size:22px;font-weight:700}.hid .ts{color:var(--dim);font-size:13px}
.hico{width:52px;height:52px;border-radius:50%;background:#262d38;display:flex;align-items:center;justify-content:center;color:var(--dim);flex:none}
.hico svg{width:28px;height:28px}.hico.on{background:#3a1d12;color:#ff8a3d}.hico.ok{background:#14301d;color:#7fd99a}
.hpills{display:flex;gap:6px;flex-wrap:wrap}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--in);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:0}
.tile .tl{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--lbl)}
.tile .tv{font-size:20px;font-weight:700;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .ts{font-size:12px;color:var(--dim);margin-top:2px}.tile.hot .tv{color:#ff8a3d}
.subnav{display:flex;gap:8px;margin:18px 0;flex-wrap:wrap}.subnav .chip{height:40px;padding:0 18px;font-size:14px}
.subnav.top{margin:0 0 18px}a.chip{display:inline-flex;align-items:center;text-decoration:none;white-space:nowrap}
/* ---- Handy (iPhone/Android): Zeilen umbrechen statt quetschen ---- */
.row .k small{word-break:normal;overflow-wrap:anywhere}
@media (max-width:640px){
  body{padding-bottom:env(safe-area-inset-bottom)}
  .wrap{padding:12px 10px 28px}.card{padding:12px 14px 4px;border-radius:14px}.card.pad{padding-bottom:12px}
  .grid,.grid.g3{gap:12px}.col{gap:12px}.mas{columns:1}.mas .card{margin-bottom:12px}
  .row{flex-wrap:wrap;row-gap:8px;min-height:52px}
  .row .k{flex:1 1 120px;min-width:0;font-size:15px}.num button{width:38px}
  .row>.ctl,.row>.tog,.row>.v{margin-left:auto}
  .row>select{flex:1 1 100%;max-width:none;width:100%;height:44px}
  .row .q{width:100%!important;flex:1 1 auto;height:44px!important}
  .row>.ctl:has(.q){flex:1 1 100%}.row>.ctl:has(.q) .btn{flex:none}
  .row>input.q{flex:1 1 100%}
  .num input{width:64px}
  .htop{gap:10px;padding-bottom:10px}.hid{min-width:0;flex:1 1 100%}.hpills{flex:1 1 100%}.hid .t2{font-size:20px}.hico{width:44px;height:44px}
  .hbt,.hbt .btn{width:100%}
  .tiles{grid-template-columns:1fr 1fr;gap:8px}.tile{padding:9px 11px}.tile .tv{font-size:18px}
  .subnav{position:sticky;top:48px;z-index:4;background:var(--bg);margin:12px -10px;padding:8px 10px;flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}
  .subnav::-webkit-scrollbar{display:none}.subnav .chip{flex:none;height:42px}
  .subnav.top{margin:0 -10px 12px}
  .tools{gap:8px}.tools .q{min-width:0;flex:1 1 100%}
  .lr{flex-wrap:wrap;gap:4px 10px}.lr .lm{flex:1 1 100%}
  .btns{grid-template-columns:1fr 1fr}
}
@media (max-width:380px){ .btns{grid-template-columns:1fr} .tiles{grid-template-columns:1fr 1fr} .row .k{flex-basis:100%} .row>.ctl{margin-left:0} }

/* ---- Handy / Tablet (iPhone, Android) ---- */
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{overscroll-behavior-y:contain;-webkit-tap-highlight-color:transparent}
.btns>*{min-width:0}.btn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media (max-width:440px){.btns{grid-template-columns:1fr}}
.tabs{-webkit-overflow-scrolling:touch;scroll-snap-type:x proximity}.tabs a{scroll-snap-align:start}
@media (pointer:coarse){
  .tabs a{padding:13px 16px 14px}
  .btn,.btn.sm{height:44px;font-size:15px}
  .chip{height:40px;font-size:14px}
  .num button{width:42px;height:42px}.num input{height:42px;font-size:16px}
  .tog{width:52px;height:32px;border-radius:16px}.tog::after{width:26px;height:26px}
  .tog.on::after{left:23px}
  input,select,textarea{font-size:16px}
  .cap{font-size:12px}
}
/* ---- Reiter „<Hersteller> EHS“ (ESP-Bus-Adapter) – Aufbau wie die Adapter-Oberfläche ---- */
.bnav .chip{display:inline-flex;align-items:center;gap:8px}.bnav .chip svg{width:18px;height:18px;flex:none}
.pills{display:flex;gap:6px;flex-wrap:wrap;padding:0 0 12px}
.hint{display:flex;gap:12px;align-items:center;padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:var(--card);margin-bottom:10px}
.hint.err{border-color:#5a2222;background:#1c1012}.hint.warn{border-color:#4a3c10;background:#19160b}.hint.info{border-color:#1d3a5a;background:#0e1824}.hint.okm{border-color:#1f4a2c;background:#0d1a12}
.hint .hi{flex:none;font-size:18px;line-height:1.2}.hint .ht{flex:1;min-width:0;font-size:14px;color:var(--k)}.hint .ht small{display:block;color:var(--dim);font-size:12px;margin-top:2px}.hint .btn{flex:none}
.tiles.t6{grid-template-columns:repeat(6,1fr)}@media (max-width:1100px){.tiles.t6{grid-template-columns:repeat(3,1fr)}}
.tile .tl{display:flex;align-items:center;gap:4px;min-height:20px}.tile .tl .ibx{margin:-4px -6px -4px auto}
.tile.bad .tv{color:#f19a9a}.tile.good .tv{color:#7fd99a}.tile.lnk{cursor:pointer}.tile.lnk:active{filter:brightness(1.2)}
.ibx{width:24px;height:24px;border-radius:50%;border:0;background:transparent;color:var(--sam);display:inline-flex;align-items:center;justify-content:center;cursor:pointer;padding:0;flex:none;vertical-align:middle}
.ibx svg{width:18px;height:18px}.ibx:active{background:#13263a}
.cap.capi{display:flex;align-items:center;gap:6px;min-height:24px}.cap .n{text-transform:none;letter-spacing:0;opacity:.7}
.row .k:has(.kt){display:flex;flex-wrap:wrap;align-items:center;column-gap:6px}.row .k:has(.kt) small{flex-basis:100%}
.seg{display:flex;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:4px;gap:4px;margin-bottom:12px}
.seg button{flex:1;height:38px;border:0;border-radius:9px;background:transparent;color:var(--k);font-weight:600;font-size:14px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.seg button.on{background:var(--sam);color:#fff}
.q.bq{width:100%;min-width:0;height:42px}
.accs{gap:10px}
.acc{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.acc>.ah{display:flex;align-items:center;gap:12px;width:100%;padding:14px 16px;border:0;background:transparent;cursor:pointer;text-align:left;min-height:62px;color:var(--txt)}
.acc>.ah .ai{width:36px;height:36px;border-radius:10px;background:#1a2230;color:var(--sam);display:flex;align-items:center;justify-content:center;flex:none}
.acc>.ah .ai svg{width:19px;height:19px}
.acc>.ah .at{flex:1;min-width:0}.acc>.ah b{display:block;font-size:16px}.acc>.ah small{display:block;color:var(--dim);font-size:12px;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.acc>.ah .cv{display:flex;width:20px;height:20px;color:var(--dim);transition:transform .2s;flex:none}.acc>.ah .cv svg{width:20px;height:20px}.acc.open>.ah .cv{transform:rotate(180deg)}
.acc>.ab{display:none;padding:0 16px 8px;border-top:1px solid var(--sep)}.acc.open>.ab{display:block}
.acc.fix>.ah{cursor:default}
.acc .ainfo{display:flex;justify-content:flex-end;margin:6px -4px -8px 0}
.dlg{position:fixed;inset:0;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;z-index:30;padding:16px}
.dlg .card{width:min(520px,100%);max-height:86vh;overflow-y:auto;padding:18px 20px 6px}
.dlg h3{margin:0 0 8px;font-size:18px;display:flex;align-items:center;gap:8px}.dlg h3 svg{width:22px;height:22px;color:var(--sam);flex:none}
.dlg .dtx p{margin:0 0 10px;line-height:1.55;color:var(--k)}.dlg .dtx ul{margin:0 0 10px;padding-left:20px;line-height:1.55;color:var(--k)}
.ok{color:#7fd99a}.bad{color:#f19a9a}
@media (max-width:640px){
  .tiles.t6{grid-template-columns:1fr 1fr}
  .bnav{gap:4px}.bnav .chip{flex:1 1 0;min-width:0;flex-direction:column;justify-content:center;height:auto!important;min-height:54px;padding:6px 2px;font-size:11px;gap:3px;border-radius:12px}
  .bnav .chip svg{width:21px;height:21px}
  .row .k:has(.kt){flex-basis:100px}.num input{width:52px}
  .acc>.ab{padding:0 14px 6px}
  .dlg{align-items:flex-end;padding:0}.dlg .card{width:100%;border-radius:18px 18px 0 0;max-height:80vh;padding-bottom:calc(8px + env(safe-area-inset-bottom))}
  .hint{flex-wrap:wrap}.hint .ht{flex:1 1 calc(100% - 40px)}.hint .btn{margin-left:30px}
  .seg button{font-size:13px}
}
/* Hinweis laufende Aufzeichnungen (v1.22.4) */
.lgn{background:#2a2108;border:1px solid #6b5313;border-radius:12px;padding:12px 16px;margin-bottom:18px}
.lgn .lt{font-weight:600;color:var(--warn);display:flex;align-items:center;gap:8px}.lgn .lt svg{width:18px;height:18px;flex:none}
.lgn .ls{color:var(--k);font-size:13px;margin:4px 0 6px}
.lgn .lr2{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid #4a3a10;flex-wrap:wrap}
.lgn .lr2 .lb{display:flex;gap:6px;flex-wrap:wrap}
/* Bosch-Reiter (v1.22.0) */
.bif{width:100%;border:1px solid var(--sep);border-radius:10px;background:transparent;display:block;margin:8px 0 12px}
.bcap{margin-top:16px}.card>.cap:first-child{margin-top:2px}
.bmd{font-size:14px;color:var(--k);padding:8px 0 10px;border-top:1px solid var(--sep);line-height:1.5;overflow-x:auto}
.bmd p{margin:4px 0 8px}.bmd a{color:var(--acc)}.bmd code{font-family:Consolas,monospace;font-size:12px;color:var(--dim)}
.bmd .bmh{font-weight:600;color:var(--txt);margin:4px 0 6px}.bmd .bq{border-left:3px solid var(--warn);padding:4px 10px;margin:6px 0;color:var(--k)}
.bmt{border-collapse:collapse;width:100%;font-size:13px}.bmt th,.bmt td{text-align:left;padding:6px 8px;border-top:1px solid var(--sep)}.bmt th{color:var(--lbl);font-weight:500}
.hvs{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}.hvs .chip.on{border-color:var(--sam);color:var(--txt)}
/* Bosch → System (v1.22.8): Kacheln + Ausklappmenü */
.tiles.s4{grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:14px}@media (max-width:800px){.tiles.s4{grid-template-columns:1fr 1fr}}
.tile .ts{font-size:12px;color:var(--dim);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .ti{display:inline-flex;width:16px;height:16px;color:var(--sam);margin-right:4px}.tile .ti svg{width:16px;height:16px}
.tile.lnk:hover{border-color:var(--sam)}
.wab{padding-top:4px}.wab .card{background:transparent;border:0;border-top:1px solid var(--sep);border-radius:0;padding:12px 0 4px;margin:0}.wab>.card:first-child,.wab .col>.card:first-child{border-top:0}
/*__WPA_CSS__*/
</style></head><body>
<div class="top">
  <nav class="tabs" id="tabs"></nav>
</div>
<div class="rule"></div>
<div class="wrap" id="main"><div class="empty">Lade …</div></div>
<input id="file" type="file" accept=".html,.htm,text/html" hidden>
<input id="fwfile" type="file" accept=".bin,application/octet-stream" hidden>
<script>
const I={
  open:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  up:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 16V4M7 9l5-5 5 5M4 20h16"/></svg>',
  pin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s-7-6.1-7-11a7 7 0 0 1 14 0c0 4.9-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>',
  tel:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/></svg>',
  nav:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 11 21 3l-8 18-2-8z"/></svg>',
  re:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/></svg>',
  log:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5"/></svg>',
  heat:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M4 12h16M4 17h16" /><path d="M7 4c-1 1-1 2 0 3M12 4c-1 1-1 2 0 3M17 4c-1 1-1 2 0 3" opacity=".7"/><path d="M20 7v10"/></svg>',
  plug:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 3v5M15 3v5M7 8h10v4a5 5 0 0 1-10 0zM12 17v4"/></svg>',
  dl:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 4v12M7 11l5 5 5-5M4 20h16"/></svg>',
  copy:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 9h11v11H9zM5 15H4V4h11v1"/></svg>',
};
// Reiter wie im Bosch-Dashboard „EMS-ESP“: System & Firmware | Einstellungen | Messwerte | Zuordnung | Bus-Log | MQTT | Uhrzeit | Web-Oberfläche
// Modus: „samsung“ = Dashboard EMS-EHS, „bsb“ = Dashboard EMS-BSB (Brötje / Siemens-BSB)
const BSBMODE='__MODE__'==='bsb';
if(BSBMODE) document.title='EMS-BSB';   // Tabellenansicht zum Dashboard „<Hersteller> EHS“
const TABS_ALL=BSBMODE
  ? [['bsb','ESP-Bus-Adapter'],['heizstab','Heizstab']]   // v1.19: Einstellungen/Messwerte/Info & Log stecken im Reiter (Werte · Logs)
  : [['system','System & Panel'],['bosch','Bosch'],['sstart','Übersicht'],['einstellungen','Werte'],['messwerte','Messwerte'],['ssystem','System'],['sbus','Bus'],['verbindung','Logs'],['heizstab','Heizstab'],['bsb','ESP-Bus-Adapter']];
// Hersteller-Seite: Einstellungen, Messwerte, Zuordnung, Info & Log liegen als Unterseiten unter „Samsung“ bzw. „Brötje“
// (wie Heizstab und ESP-Bus-Adapter). Die Adressen #einstellungen, #messwerte … bleiben gültig.
const VGROUP={key:'hersteller', get label(){ return BSBMODE?((typeof BS!=='undefined'&&BS&&BS.brand)||'Brötje'):'Samsung'; },
  subs:BSBMODE?[]
              :[['sstart','Übersicht','home'],['einstellungen','Werte','list'],['ssystem','System','gear'],['sbus','Bus','bus'],['verbindung','Logs','log']]};
// „Messwerte“ ist die zweite Ansicht von „Werte“ (Umschalter oben)
const inVG=t=>(t==='messwerte'&&!BSBMODE)||VGROUP.subs.some(x=>x[0]===t);
let VSUB='sstart'; try{ const v=localStorage.getItem('emsehs_vsub_'+(BSBMODE?'bsb':'sam')); if(v&&inVG(v)) VSUB=v; }catch(e){}
const TOPS=()=>{ const out=[]; let done=false;
  for(const t of TABS_()){ if(inVG(t[0])){ if(!done&&(BSBMODE||HAS('samsung'))) out.push([VGROUP.key,VGROUP.label]); done=true; continue; } out.push(t); }
  const i=out.findIndex(t=>t[0]===VGROUP.key); const at=out[1]&&out[1][0]==='bosch'?2:1; if(i>at){ const [g]=out.splice(i,1); out.splice(at,0,g); }
  return out; };
// Unterseiten-Auswahl als Chips mit Symbol (gleicher Aufbau wie „<Hersteller> EHS“)
let SWV='einstellungen'; try{ SWV=localStorage.getItem('emsehs_swv')==='messwerte'?'messwerte':'einstellungen'; }catch(e){}
function vgNav(){ return `<nav class="subnav top bnav">${VGROUP.subs.map(([k,l,ic])=>{ const w=k==='einstellungen'; const on=w?(TAB==='einstellungen'||TAB==='messwerte'):TAB===k;
  return `<a href="#${w?SWV:k}" class="chip ${on?'on':''}">${BI[ic]||''}<span>${l}</span></a>`; }).join('')}</nav>`; }
const ALIAS=BSBMODE?{uebersicht:'bsb',system:'bsb',einstellungen:'bsb',messwerte:'bsb',verbindung:'bsb',protokoll:'bsb',log:'bsb',heizstablog:'heizstab'}
  :{uebersicht:'system',protokoll:'log',heizstablog:'heizstab',webui:'ssystem',hersteller:'sstart'};
const webUrl=()=>{ const x=ST&&ST.instances&&ST.instances.find(i=>i.ip); return x?'http://'+x.ip:null; };
const TABS_=()=>TABS_ALL.filter(t=>(t[0]!=='bosch'||(VEN()&&VEN().includes('bosch')))&&(t[0]!=='webui'||webUrl())&&(!t[0].startsWith('heizstab')||HT)&&(t[0]!=='bsb'||BSBON||BSBMODE));
// ESP-Bus-Adapter: Reiter, sobald das Gerät „ESP-Bus-Adapter“ eingerichtet ist (Seite = Panel EMS-Regelung → ESP-Bus-Adapter)
let BSBON=false;
async function bsbLoad(){ await bsLoad(null, TAB==='bsb'); }
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const nf=(v,d)=>v==null||isNaN(v)?'–':Number(v).toLocaleString('de-DE',{minimumFractionDigits:d||0,maximumFractionDigits:d||0});
function H(){ for(const w of [window.parent, window.parent&&window.parent.parent, window.top]){ try{ const ha=w&&w!==window&&w.document.querySelector('home-assistant'); if(ha&&ha.hass) return ha.hass; }catch(e){} } return null; }
const ws=m=>H().connection.sendMessagePromise(m);
const call=(d,s,data)=>H().callService(d,s,data);
// Zusatzangabe direkt hinter der Bezeichnung (z. B. „Warmwasser Soll 52 °C“, „Arbeitszahl gesamt 2,46“)
const rowL=(k,sub,v)=>`<div class="row"><span class="k">${esc(k)}${sub?` <small style="display:inline;margin-left:6px;font-size:13px;color:var(--dim)">${esc(sub)}</small>`:''}</span><span class="v">${v}</span></div>`;
const row=(k,v,sub)=>`<div class="row"><span class="k">${esc(k)}</span><span class="v">${v}${sub?`<small>${esc(sub)}</small>`:''}</span></div>`;
let ST=null, INFO=null, GR=null, TAB='system', LOG={level:'all',pause:false,data:null,pkt:false}, Q='', SHOW_ID=false;

/* ---------- Werte / Übersetzung ---------- */
const TR={OP_STOP:'Stopp',OP_SAFETY:'Sicherheitsabschaltung',OP_NORMAL:'Normalbetrieb',OP_BALANCE:'Ausgleich',OP_RECOVERY:'Ölrückführung',OP_DEICE:'Abtauen',
  OP_COMPDOWN:'Verdichter herunter',OP_PROHIBIT:'Gesperrt',OP_TEST:'Test',OP_NONSTOP_DEICE:'Abtauen (non-stop)',OP_SPLIT_DEICE:'Abtauen (geteilt)',
  OFF:'Aus',ON:'Ein','On Alternate':'Ein (wechselnd)',Auto:'Auto',Heat:'Heizen',Cool:'Kühlen',Dry:'Trocknen',Fan:'Lüften','Hot water':'Warmwasser',
  Eco:'Eco',Standard:'Standard',Power:'Power',Force:'Forced (Extra-WW)',ROOM:'Heizung',TANK:'Warmwasser','STEP 1':'Stufe 1','STEP 2':'Stufe 2',
  Floor:'Fußboden (WL1)',FCU:'Gebläsekonvektor (WL2)',No:'Nein',Yes:'Ja',DHW:'Warmwasser',Heating:'Heizen',on:'Ein',off:'Aus',
  Sunday:'Sonntag',Monday:'Montag',Tuesday:'Dienstag',Wednesday:'Mittwoch',Thursday:'Donnerstag',Friday:'Freitag',Saturday:'Samstag',Everyday:'Täglich',
  'No defrost operation':'Kein Abtauen','Defrost operation end stage':'Abtauen Ende',unknown:'–',unavailable:'n. v.'};
const tr=v=>v==null?'–':(TR[v]??String(v).replace(/^Defrost stage (\d+)$/,'Abtau-Stufe $1').replace(/^OP_/,''));
const S=id=>{ const h=H(); return h&&h.states[id]; };
let ENT_IDX=null, ENT_SRC=null;
function ent(k){ const h=H(); if(!h) return null;
  if(ENT_SRC!==h.states){ ENT_SRC=h.states; ENT_IDX={};
    for(const id in h.states){ const o=id.split('.')[1]||''; const i=o.indexOf('ehssentinel_'); if(i<0) continue;
      if(i>0 && o[i-1]!=='_') continue; const key=o.slice(i+12); (ENT_IDX[key]=ENT_IDX[key]||[]).push(id); } }
  for(const id of (ENT_IDX[k]||[])){ const s=h.states[id]; if(s && s.state!=='unavailable'&&s.state!=='unknown') return s; } return null; }
function num(k){ const s=ent(k); if(!s) return null; const v=parseFloat(s.state); return isNaN(v)?null:{v,u:(s.attributes||{}).unit_of_measurement||''}; }
function fmtState(s){
  if(!s) return '–'; const v=s.state, u=(s.attributes||{}).unit_of_measurement||'';
  if(v==='unavailable'||v==='unknown') return '<span class="v d">'+tr(v)+'</span>';
  const n=parseFloat(v); if(!isNaN(n) && /^-?\d*\.?\d+(e[-+]?\d+)?$/i.test(v)){
    // ESP-Bus-Adapter (EMS-BSB): Temperaturen immer mit 1 Nachkommastelle
    const d=(/^(°C|K)$/.test(u) && (BSBMODE || (typeof AE==='function' && AE().some(x=>x.entity_id===s.entity_id)))) ? 1 : Math.abs(n%1)>0.001?(Math.abs(n)<10?2:1):0; return nf(n,d)+(u?' '+esc(u):''); }
  return esc(tr(v));
}

/* ---------- Kopf & Tabs ---------- */
function head(){
  $('#tabs').innerHTML=TOPS().map(([k,l])=>{ const isG=k===VGROUP.key; const on=isG?inVG(TAB):TAB===k;
    // Reiter des Adapters trägt den gewählten Hersteller (z. B. „Brötje EHS“, „Elco EHS“)
    if(k==='bsb' && BS && BS.brand_label) l=esc(BS.brand_label);
    return `<a href="#${isG?VSUB:k==='bosch'?'bosch/'+BV:k}" class="${on?'on':''}" ${k==='bsb'&&!BSBMODE?'title="ESP-Bus-Adapter"':''}>${l}</a>`; }).join('');
}
// v1.20.3: „System & Panel“ ist immer da – welche Hersteller eingerichtet sind, meldet der Manager
const PPATHS=__PANEL_PATHS__;
const VEN=()=>(INFO&&INFO.vendors)||(ST&&ST.vendors)||null;
const HAS=v=>{ const l=VEN(); return !l||l.includes(v); };   // vor dem ersten Laden: alles zeigen
const PV=()=>{ const l=VEN()||[]; return ['samsung','bosch','bsb','heater'].find(v=>l.includes(v))||'samsung'; };
const VNAME=__VENDOR_NAMES__;   // v1.24.0: aus dem Hersteller-Register
const vName=v=>v==='bsb'&&typeof BS!=='undefined'&&BS&&BS.brand_label?BS.brand_label:VNAME[v]||v;
const openPanel=p=>{ p=p||PPATHS[PV()]||'__PANEL_PATH__'; try{ window.parent.history.pushState(null,'',p); window.parent.dispatchEvent(new CustomEvent('location-changed')); }catch(e){ window.open(p,'_top'); } };
function route(){ let h=(location.hash||'').slice(1);
  try{ const j=localStorage.getItem('emsehs_tab'); if(j){ localStorage.removeItem('emsehs_tab'); if(!h){ h=j; history.replaceState(null,'','#'+j); } } }catch(e){}
  if(!BSBMODE && h==='zuordnung'){ BS_OPEN['s-map']=1; h='ssystem'; }
  if(!BSBMODE && h==='webui'){ BS_OPEN['s-web']=1; }
  if(h==='heizstablog'){ HSUB='log'; }
  if(!BSBMODE && /^bosch(\/|$)/.test(h)){ const sv=h.split('/')[1]; if(sv){ BV=sv; try{ localStorage.setItem('emsehs_bview',BV); }catch(e){} } h='bosch'; }
  h=ALIAS[h]||h; if(h==='log'){ h='verbindung'; } if(h===VGROUP.key) h=VSUB;
  TAB=TABS_ALL.some(t=>t[0]===h)?h:(BSBMODE?'bsb':'system');
  if(TAB==='einstellungen'||TAB==='messwerte'){ SWV=TAB; try{ localStorage.setItem('emsehs_swv',SWV); }catch(e){} }
  if(inVG(TAB)){ VSUB=TAB; try{ localStorage.setItem('emsehs_vsub_'+(BSBMODE?'bsb':'sam'),VSUB); }catch(e){} }
  head(); render(true); if(TAB==='verbindung' && !BSBMODE && !LOG.data) loadLog();
  if(BSBMODE && TAB==='verbindung') loadBLog().then(()=>{ if(TAB==='verbindung') bLogRefresh(); });
  if(TAB==='bosch'){ bLoad(!BD).then(ch=>{ if(ch && TAB==='bosch') render(true); }); }
  if(TAB==='system'){ const was=JSON.stringify(LOGS&&LOGS.active); logsLoad().then(()=>{ if(TAB==='system' && JSON.stringify(LOGS&&LOGS.active)!==was) render(); }); }
  if(TAB==='heizstab'){ HT_MSG=''; if(!HTD || Date.now()-HTD_T>60000) htdLoad().then(()=>{ if(TAB==='heizstab') render(); });
    loadHLog().then(()=>{ if(TAB==='heizstab') hLogRefresh(); });
  }
  if(TAB==='bsb'){ BS_MSG=''; bsLoad().then(()=>{ if(TAB==='bsb') render(); }); if(BSUB==='log') loadBLog().then(()=>{ if(TAB==='bsb') bLogRefresh(); }); } }
window.addEventListener('hashchange',route);

/* =================== Hinweis: laufende Log-Aufzeichnungen (v1.22.4) =================== */
let LOGS=null;
async function logsLoad(off){ try{ LOGS=await ws(off?{type:'ems_esp_manager/panel/logs',off}:{type:'ems_esp_manager/panel/logs'}); }catch(e){ if(off) alert('Nicht ausgeschaltet: '+(e&&e.message||e)); } }
function logsHint(){
  const a=(LOGS&&LOGS.active)||[]; if(!a.length) return '';
  const adm=LOGS.is_admin;
  return `<div class="lgn"><div class="lt">${BI.log||''} ${a.length===1?'Eine Log-Aufzeichnung läuft':a.length+' Log-Aufzeichnungen laufen'}</div>
    <div class="ls">Aufzeichnungen belasten Speicher und Prozessor von Home Assistant. Nur zur Fehlersuche einschalten – nach einem Neustart von Home Assistant sind sie automatisch wieder aus.</div>
    ${a.map(x=>`<div class="lr2"><span>${esc(x.label)}</span><span class="lb"><button class="btn sm" data-lgo="${esc(x.key)}" data-h="${esc(x.hash)}">${I.open} Ansehen</button>${adm?`<button class="btn sm" data-loff="${esc(x.key)}">Ausschalten</button>`:''}</span></div>`).join('')}
    ${adm&&a.length>1?`<div class="lr2"><span></span><span class="lb"><button class="btn sm pri" data-loff="all">Alle ausschalten</button></span></div>`:''}</div>`;
}
async function logsClick(b){
  if(b.dataset.loff){ b.disabled=true; await logsLoad(b.dataset.loff); if(typeof ST!=='undefined'&&ST&&(b.dataset.loff==='ehs'||b.dataset.loff==='all')) ST.log_enabled=false;
    if(b.dataset.loff==='heater'||b.dataset.loff==='all'){ try{ await htLoad(); }catch(e){} }
    if(b.dataset.loff==='bsb'||b.dataset.loff==='all'){ try{ await bsLoad(null,true); }catch(e){} } render(); return true; }
  if(b.dataset.lgo){ const k=b.dataset.lgo;
    if(k==='ehs'){ ESUB='log'; try{localStorage.setItem('emsehs_esub','log');}catch(e){} }
    if(k==='bsb'){ BSUB='log'; try{localStorage.setItem('emsehs_bsub','log');}catch(e){} }
    location.hash=b.dataset.h; window.scrollTo(0,0); return true; }
  return false;
}
/* =================== System & Panel (wie „System & Firmware“ bei Bosch) =================== */
function vOverview(){
  const i=INFO||{}, d=ST||{}, x=(d.instances||[])[0], ins=(ST&&ST.installer)||{};
  const st=ent('outdooroperationstatus'), ot=num('outdoorouttemp'), sam=HAS('samsung'), vl=VEN()||[];
  const sub=vl.length?vl.map(v=>v==='samsung'?'Samsung EHS · EHS Sentinel':v==='bosch'?'Bosch / Buderus · EMS-ESP':esc(vName(v))).join(' · '):'Samsung EHS · EHS Sentinel';
  const devs=vl.map(v=>`<div class="row"><span class="k">${esc(vName(v))}</span><span class="v"><button class="btn sm" data-act="open" data-path="${esc(PPATHS[v]||'')}">${I.open} Öffnen</button></span></div>`).join('')
    +(vl.includes('bosch')?`<div class="row"><span class="k">Bosch-Einstellungen (EMS-ESP)</span><span class="v"><button class="btn sm" data-hash="bosch/system">${I.open} Öffnen</button></span></div>`:'');
  const L=[
    bAccFix('p-ver',BI.book,'Version & Panel','Manager '+esc(d.version||'–')+' · Panel '+esc(i.version||'–'),
      `<div class="brand" style="margin-top:12px"><div class="logo" style="background-image:url('__LOGO__')"></div><div><div class="bt">EMS-<b>EHS</b>-Manager</div><div class="bs">${sub}</div></div></div>
      <div id="o_ver">${verRows()}</div>
      <div class="btns"><button class="btn pri" data-act="open">${I.open} Panel öffnen</button><button class="btn" data-act="upload" ${i.is_admin?'':'disabled'}>${I.up} Neue Version hochladen</button></div>
      <div id="ask"></div><div class="msg" id="msg"></div>`),
    !sam?'':bAcc('p-ehs',BI.link,'EHS Sentinel',!x?'nicht eingerichtet':(x.connected?'verbunden':'getrennt')+(x.ip?' · '+esc(x.ip):''),`<div id="o_ehs">${ehsRows()}</div>`),
    !sam?'':bAcc('p-wp',BI.wp,'Wärmepumpe',esc(st?tr(st.state):'keine Werte')+(ot?' · außen '+nf(ot.v,1)+' °C':''),`<div id="o_wp">${wpRows()}</div>`),
    bAcc('p-kiosk',BI.link,'Zugriff von außen (Kiosk)',!i.is_admin?'nur für Administratoren':i.kiosk_url?'bereit':'Token nicht hinterlegt',kioskHtml()),
    bAcc('p-ins',BI.tel,'Ihr Fachbetrieb',esc(ins.name||'nicht eingetragen'),`<div style="padding-top:12px">${insHtml()}</div>`),
    devs?bAcc('p-dev',BI.dev,'Installierte Geräte',vl.map(vName).map(esc).join(' · '),`<div style="padding-top:8px">${devs}</div>`):''];
  return logsHint()+`<div class="grid"><div class="col accs">${L[0]}</div><div class="col accs">${L[1]}${L[2]}${L[5]}${L[3]}${L[4]}</div></div>`;
}
/* =================== Samsung: Übersicht · Werte · System · Bus · Logs =================== */
function sPills(){ const x=((ST&&ST.instances)||[])[0];
  const yn=(l,v)=>v==null?'':v?`<span class="pill">${l} ein</span>`:`<span class="pill dm">${l} aus</span>`;
  return `<div class="pills">${!x?'<span class="pill no">EHS Sentinel fehlt</span>':!x.loaded?'<span class="pill dm">Nicht geladen</span>':x.connected?'<span class="pill">RS485-Adapter</span>':'<span class="pill no">Adapter getrennt</span>'}
    ${x?yn('Schreibmodus',x.write_mode)+yn('Polling',x.polling):''}${ST&&ST.log_enabled?'<span class="pill dm">Log an</span>':''}</div>`; }
function sHints(){
  const d=ST||{}, xs=d.instances||[], out=[]; const add=(lvl,t,todo,btn)=>out.push({lvl,t,todo,btn});
  const go=(h,l)=>`<button class="btn sm" data-hash="${h}">${l||'Öffnen'}</button>`;
  if(d.err) add('err',d.err,'');
  else if(!xs.length) add('err','EHS Sentinel ist nicht eingerichtet.','HACS-Integration „EHS Sentinel“ mit RS485-Adapter installieren und einrichten.');
  xs.forEach(x=>{
    if(!x.loaded) add('err',`${x.title}: ${STAT_TXT[x.state]||x.state||'nicht geladen'}.`,'Unter Einstellungen → Geräte & Dienste → EHS Sentinel prüfen und neu laden.',go('ssystem'));
    else if(!x.connected) add('err',`Keine Verbindung zum RS485-Adapter ${x.ip||''}:${x.port||''}.`,'Adapter mit Strom und Netzwerk prüfen, dann „Neu verbinden“.',`<button class="btn sm" data-re="${esc(x.entry_id)}">Neu verbinden</button>`);
    if(x.loaded && x.write_mode===false) add('info','Schreibmodus ist aus – Einstellungen der Wärmepumpe können nicht geändert werden.','In EHS Sentinel unter „Konfigurieren“ den Steuermodus einschalten.');
    if(x.queue>20) add('warn',`Warteschlange mit ${x.queue} Paketen – die Verbindung kommt nicht hinterher.`,'Adapter und WLAN-Empfang prüfen.',go('sbus'));
  });
  if(!out.length) return ''; const ico={err:'⛔',warn:'⚠️',info:'ℹ️'};
  return out.map(x=>`<div class="hint ${x.lvl}"><span class="hi">${ico[x.lvl]}</span><span class="ht">${esc(x.t)}${x.todo?`<small>${esc(x.todo)}</small>`:''}</span>${x.btn||''}</div>`).join(''); }
function sTiles(){ const t=k=>{ const n=num(k); return n?nf(n.v,1)+' °C':'–'; };
  const so=num('indoordhwsettemp'), hz=num('outdoorcomp1runhz'), el=num('outdoorcontrolwattmeterallunit'), cop=num('ehssentinelcop'), jaz=num('ehssentineltotalcop');
  const st=ent('outdooroperationstatus'), run=hz&&hz.v>0;
  return `<div class="tiles t6">
    ${btile('Außen',t('outdoorouttemp'),'Außengerät')}
    ${btile('Vorlauf',t('indoorwaterouttemp'),num('indoorwaterintemp')?'Rücklauf '+t('indoorwaterintemp'):'')}
    ${btile('Warmwasser',t('indoordhwcurrenttemp'),so?'Soll '+nf(so.v,0)+' °C':'')}
    ${btile('Verdichter',hz?nf(hz.v,0)+' Hz':'–',st?esc(tr(st.state)):'',run?'hot':'')}
    ${btile('Elektrisch',el?nf(el.v,2)+' '+esc(el.u||'kW'):'–','Leistungsaufnahme')}
    ${btile('Arbeitszahl',cop?nf(cop.v,1):'–',jaz?'gesamt '+nf(jaz.v,2):'')}
  </div>`; }
function sStart(){
  if(!ST) return '<div class="empty">Lade …</div>';
  return sPills()+sHints()+`<div id="s_tiles">${sTiles()}</div><div style="height:18px"></div>
    <div class="grid"><div class="col"><div class="card"><div class="cap">Wärmepumpe</div><div id="o_wp">${wpRows()}</div></div></div>
    <div class="col"><div class="card"><div class="cap">EHS Sentinel</div><div id="o_ehs">${ehsRows()}</div></div></div></div>`; }
function sWerte(kind){
  return `<div class="seg"><button data-hash="einstellungen" class="${kind==='settings'?'on':''}">Einstellungen</button><button data-hash="messwerte" class="${kind==='values'?'on':''}">Messwerte</button></div>`+vGroups(kind); }
// v1.24.1: Samsung „System“ wie Bosch: Kacheln oben + Ausklappmenü untereinander (WP-Auswertung als Abschnitt statt Umschalter)
function sSystem(){ return sSystemBody(); }
function sSysTiles(){
  const d=ST||{}, t=(k,ic,l,v,sub,c)=>`<div class="tile lnk ${c||''}" data-btile="${k}"><div class="tl"><span class="ti">${BI[ic]||''}</span>${esc(l)}</div><div class="tv">${v}</div><div class="ts">${sub}</div></div>`;
  waVendor('samsung'); const P=WPR_PER[WPA.per], c=waC()[P.k]||{}; if(!c.res&&!c.loading&&!c.err) waLoad(P.k);
  const r=c.res, kr=r?r.find.filter(x=>x.lv===2).length:0, out=[];
  out.push(t('s-wpa','wp','WP-Auswertung',r?'AZ '+waN(r.K.az,2):c.loading?'lädt …':'–',
    r?`${nf(r.K.starts)} Starts · ${esc(P.l)}${kr?' · '+kr+' kritisch':''}`:c.err?'keine Werte':esc(P.l),r?(kr?'bad':'good'):''));
  const xs=d.instances||[], x=xs[0], con=xs.length&&xs.every(i=>i.connected);
  out.push(t('s-conn0','link','Verbindung',!x?'–':con?'verbunden':!x.loaded?'nicht geladen':'getrennt',esc(x&&x.ip?x.ip:'EHS Sentinel'),!x?'':con?'good':'bad'));
  out.push(t('s-map','dev','Zuordnung','Panel ↔ HA',nf((INFO||{}).mapped)+' eigene Zuordnungen'));
  out.push(t('s-ver','book','Versionen','Manager '+esc(d.version||'–'),'EHS Sentinel '+esc(d.ehs_version||'–')));
  return `<div class="tiles s4">${out.join('')}</div>`; }
function sSystemBody(){
  if(!ST) return '<div class="empty">Lade …</div>';
  const d=ST||{}, u=webUrl(), yn=v=>v==null?'–':v?'Ein':'Aus';
  const inst=(d.instances||[]).map((x,i)=>bAcc('s-conn'+i,BI.link,(d.instances.length>1?x.title:'Verbindung'),(!x.loaded?'nicht geladen':x.connected?'verbunden':'getrennt')+(x.ip?' · '+esc(x.ip):''),
      row('Adresse (RS485-Adapter)',esc((x.ip||'–')+':'+(x.port||'–')))+row('Integrationsstatus',esc(STAT_TXT[x.state]||x.state))
      +row('Schreibmodus (Steuermodus)',yn(x.write_mode))+row('Polling',yn(x.polling))+row('Erweitertes Logging',yn(x.extended_logging),'nötig für das Paket-Log')
      +`<div class="btns"><button class="btn" data-re="${esc(x.entry_id)}">${I.re} Neu verbinden</button><a class="btn" href="/config/integrations/integration/ehs_sentinel" target="_top">${I.open} EHS Sentinel</a></div>`
      +`<div class="note">Schreibmodus, Polling und erweitertes Logging stellst du in EHS Sentinel unter „Konfigurieren“ ein.</div>`));
  if(!inst.length) inst.push(bAcc('s-conn0',BI.link,'Verbindung','nicht eingerichtet','<div class="note top0" style="padding-top:12px">EHS Sentinel ist nicht eingerichtet (HACS-Integration „EHS Sentinel“ mit RS485-Adapter).</div>'));
  const L=[bAcc('s-wpa',BI.wp,'WP-Auswertung','Laufzeit, Taktung, Arbeitszahl · PDF-Bericht',BS_OPEN['s-wpa']?`<div class="wab">${vWpa()}</div>`:' '),...inst,
    bAcc('s-map',BI.dev,'Zuordnung',nf((INFO||{}).mapped)+' eigene Zuordnungen',`<div style="padding:12px 0 8px">${vMap()}</div>`),
    bAcc('s-web',BI.chip,'Web-Oberfläche des Adapters',u?esc(u.replace('http://','')):'keine Adresse bekannt',
      u?`<div class="note">Weboberfläche des RS485-Adapters (z. B. Elfin EW11) – nur im Heimnetz erreichbar.</div><div class="btns"><a class="btn pri" href="${esc(u)}" target="_blank" rel="noopener">${I.open} Im neuen Fenster öffnen</a></div>`
       :'<div class="note top0" style="padding-top:12px">Keine Adresse des RS485-Adapters bekannt.</div>'),
    bAcc('s-ver',BI.book,'Versionen','EHS Sentinel '+esc(d.ehs_version||'–')+' · Manager '+esc(d.version||'–'),verRows())];
  return `<div id="ssys-tiles">${sSysTiles()}</div><div class="accs">${L.join('')}</div>`; }
function sBus(){
  if(!ST) return '<div class="empty">Lade …</div>';
  const xs=(ST&&ST.instances)||[];
  const cards=xs.map(x=>{ const s=x.stats||{}; return `<div class="card"><div class="cap">${esc(x.title)}</div>`+row('Gelesen',nf(s.packets_read))+row('Verarbeitet',nf(s.packets_processed))+row('Angefordert',nf(s.packets_requested))
      +row('Fremde Pakete (nicht Innen/Außen)',nf(s.packets_processed_not_indoor_outdoor))+row('Warteschlange',nf(x.queue))+row('Paket-Log',x.extended_logging?'<span class="pill">an</span>':'<span class="pill dm">aus</span>','erweitertes Logging in EHS Sentinel')
      +`<div class="btns"><button class="btn" data-act="gopkt">${I.log} Paket-Log anzeigen</button><button class="btn" data-act="diag">${I.re} Diagnose</button></div></div>`; }).join('');
  return eTop()+`<div style="height:18px"></div><div class="grid"><div class="col">${cards||'<div class="card pad"><div class="note top0">EHS Sentinel ist nicht eingerichtet.</div></div>'}</div><div class="col"></div></div>`; }
function ehsRows(){
  const d=ST; if(!d) return '<div class="note top0">Lade …</div>';
  if(d.err) return `<div class="note top0">${esc(d.err)}</div>`;
  if(!d.instances.length) return '<div class="note top0">EHS Sentinel ist nicht eingerichtet (HACS-Integration „EHS Sentinel“ mit RS485-Adapter).</div>';
  return d.instances.map((x,i)=>{ const s=x.stats||{}, yn=v=>v==null?'–':v?'Ein':'Aus';
    const pill=!x.loaded?'<span class="pill dm">Nicht geladen</span>':x.connected?'<span class="pill">Verbunden</span>':'<span class="pill no">Getrennt</span>';
    return (d.instances.length>1?`<div class="cap" style="margin-top:${i?14:0}px">${esc(x.title)}</div>`:'')
      +row('Verbindung (RS485-Adapter)',pill)+row('Adresse',esc((x.ip||'–')+':'+(x.port||'–')))+row('Schreibmodus',yn(x.write_mode))
      +row('Pakete gelesen',nf(s.packets_read),s.packets_processed!=null?'verarbeitet '+nf(s.packets_processed):'')+row('Warteschlange',nf(x.queue))
      +`<div class="btns"><button class="btn" data-re="${esc(x.entry_id)}">${I.re} Neu verbinden</button><a class="btn" href="#verbindung">${I.log} EHS-Log</a></div>`;
  }).join('');
}
function wpRows(){
  const t=k=>{ const n=num(k); return n?nf(n.v,1)+' °C':null; }; const r=[];
  const st=ent('outdooroperationstatus'); if(st) r.push(row('Status Außengerät',esc(tr(st.state))));
  const md=ent('indooropmode'), pw=ent('power'); if(md||pw) r.push(row('Betriebsart',esc(pw&&pw.state==='OFF'?'Aus':tr(md?md.state:pw.state))));
  [['Außentemperatur','outdoorouttemp'],['Vorlauf','indoorwaterouttemp'],['Rücklauf','indoorwaterintemp']].forEach(([l,k])=>{ const v=t(k); if(v) r.push(row(l,v)); });
  const ww=t('indoordhwcurrenttemp'); if(ww){ const so=num('indoordhwsettemp');
    r.push(rowL('Warmwasser',so?'Soll '+nf(so.v,0)+' °C':'',ww)); }
  const hz=num('outdoorcomp1runhz'); if(hz) r.push(row('Verdichter',nf(hz.v,0)+' Hz'));
  const el=num('outdoorcontrolwattmeterallunit'); if(el) r.push(row('Elektrische Leistung',nf(el.v,2)+' '+esc(el.u||'kW')));
  const cop=num('ehssentinelcop'), jaz=num('ehssentineltotalcop'); if(cop||jaz) r.push(rowL('Arbeitszahl',jaz?'gesamt '+nf(jaz.v,2):'',cop?nf(cop.v,1):'–'));
  return r.join('') || '<div class="note top0">Keine Werte von EHS Sentinel gefunden.</div>';
}
function insHtml(){
  const i=(ST&&ST.installer)||{};
  if(!i.name&&!i.addr&&!i.tel) return '<div class="cap">Ihr Fachbetrieb</div><div class="note top0">Name, Adresse und Telefon unter Einstellungen → Geräte &amp; Dienste → EMS-ESP Manager → Konfigurieren eintragen.</div>';
  const addr=String(i.addr||'').split(/,\s*|\n/).filter(Boolean), tel=String(i.tel||'').trim();
  const route='https://www.google.com/maps/dir/?api=1&destination='+encodeURIComponent(i.addr||i.name);
  return `<div class="ins"><div class="logo" style="background-image:url('__LOGO__')"></div><div><div class="cap" style="margin:0">Ihr Fachbetrieb</div><div class="t2">${esc(i.name||'')}</div></div></div>
    ${addr.length?`<div class="line">${I.pin}<span>${addr.map(esc).join('<br>')}</span></div>`:''}${tel?`<div class="line tel">${I.tel}<span>${esc(tel)}</span></div>`:''}
    <div class="btns" style="margin-bottom:0">${tel?`<a class="btn pri" href="tel:${esc(tel.replace(/[^\d+]/g,''))}" target="_top">${I.tel} Anrufen</a>`:'<span></span>'}${i.addr?`<a class="btn" href="${esc(route)}" target="_blank" rel="noopener">${I.nav} Route</a>`:''}</div>`;
}
const dt=s=>s?new Date(s).toLocaleString('de-DE',{dateStyle:'short',timeStyle:'short'}):'';
function verRows(){
  const i=INFO||{}, d=ST||{}; const src=i.source==='uploaded'?'hochgeladen'+(i.uploaded&&i.uploaded.date?' '+dt(i.uploaded.date):''):'';
  return row('Manager',esc(d.version||'–'))+row('Panel',esc(i.version||'–'),i.version?src:'')+(HAS('samsung')?row('EHS Sentinel',esc(d.ehs_version||'–')):'')
    +row('Home Assistant',esc(d.ha_version||(H()&&H().config&&H().config.version)||'–'))+row('Eigene Zuordnungen',nf(i.mapped));
}
function kioskHtml(){
  const i=INFO||{};
  if(!i.is_admin) return row('Status','nur für Administratoren');
  if(i.kiosk_url) return row('Kiosk-Adresse','<span class="pill">Bereit</span>')+`<div class="note">Auf dem Wandtablet öffnen (enthält einen geheimen Schlüssel):<br><code>${esc(i.kiosk_url)}</code></div>`;
  return row('Token','<span class="pill dm">nicht hinterlegt</span>')+'<div class="note">Token in HA unter Profil → Sicherheit → Langlebige Zugriffstoken erstellen und unter EMS-ESP Manager → Konfigurieren einfügen. Danach erscheint hier die Adresse.</div>';
}

/* =================== Einstellungen / Messwerte =================== */
function match(it){ if(!Q) return true; const q=Q.toLowerCase(); return it.label.toLowerCase().includes(q)||it.id.includes(q)||it.key.includes(q); }
function tools(ph){
  return `<div class="tools"><input class="q" id="q" placeholder="${ph}" value="${esc(Q)}"><span class="sp"></span>
    <button class="chip ${SHOW_ID?'on':''}" data-act="ids">Entitäts-IDs</button></div>`;
}
function ctlHtml(it){
  const s=S(it.id), dom=it.id.split('.')[0];
  if(!s) return '<span class="v d">n. v.</span>';
  if(s.state==='unavailable') return '<span class="v d">n. v.</span>';
  const a=s.attributes||{};
  if(dom==='switch') return `<button class="tog ${s.state==='on'?'on':''}" data-sw="${esc(it.id)}" aria-label="umschalten"></button>`;
  if(dom==='select') return `<select data-sel="${esc(it.id)}">${(a.options||[]).map(o=>`<option value="${esc(o)}" ${o===s.state?'selected':''}>${esc(tr(o))}</option>`).join('')}</select>`;
  if(dom==='button') return `<button class="btn sm" data-btn="${esc(it.id)}">Auslösen</button>`;
  if(dom==='number'){ const step=+a.step||1, v=parseFloat(s.state);
    return `<span class="ctl"><span class="num"><button data-step="-1" data-n="${esc(it.id)}">−</button><input type="number" data-num="${esc(it.id)}" value="${isNaN(v)?'':v}" step="${esc(step)}" min="${esc(a.min??'')}" max="${esc(a.max??'')}"><button data-step="1" data-n="${esc(it.id)}">+</button></span><span class="u">${esc(a.unit_of_measurement||'')}</span></span>`; }
  return `<span class="v">${fmtState(s)}</span>`;
}
function itemRow(it,ctl){
  return `<div class="row" data-row="${esc(it.id)}"><span class="k">${esc(it.label)}${SHOW_ID?`<small>${esc(it.id)}</small>`:''}</span>${ctl?ctlHtml(it):`<span class="v" data-val="${esc(it.id)}">${fmtState(S(it.id))}</span>`}</div>`;
}
function vGroups(kind){
  if(!GR) return '<div class="empty">Lade …</div>';
  if(!GR.count) return BSBMODE ? '<div class="card pad"><div class="note top0">Keine Werte des ESP-Bus-Adapters gefunden. Unter „ESP-Bus-Adapter → Adapter“ den ESPHome-Adapter wählen (bei BSB-LAN stehen alle Werte unter „ESP-Bus-Adapter“).</div></div>'
    : '<div class="card pad"><div class="note top0">Keine Entitäten von EHS Sentinel gefunden. Bitte die HACS-Integration „EHS Sentinel“ installieren und einrichten.</div></div>';
  const ctl=kind==='settings', groups=GR[kind]||[];
  const hint = ctl && BSBMODE ? `<div class="card pad" style="margin-bottom:18px"><div class="note top0" style="padding:0">Änderungen gehen sofort über den ESP-Bus-Adapter an den Regler. Weitere Werte (alle Parameter, Favoriten) unter „ESP-Bus-Adapter“.</div></div>` : ctl ? `<div class="card pad" style="margin-bottom:18px"><div class="note top0" style="padding:0">Änderungen werden sofort an die Wärmepumpe geschickt. Schreiben geht nur mit aktivem <b>Steuermodus</b> in EHS Sentinel – sonst stehen die FSV-Werte unter „Messwerte“. FSV-Werte nur mit Fachkenntnis ändern.</div></div>` : '';
  const cards=groups.map(g=>{ const its=g.items.filter(match); if(!its.length) return '';
    return `<div class="card"><div class="cap">${esc(g.title)} <span style="opacity:.6">· ${its.length}</span></div><div>${its.map(it=>itemRow(it,ctl)).join('')}</div></div>`; }).join('');
  return tools(ctl?'Einstellung suchen …':'Messwert suchen …')+hint+(cards?`<div class="mas">${cards}</div>`:'<div class="empty">Nichts gefunden.</div>');
}
async function setVal(el, fn){ const r=el.closest('.row'); r&&r.classList.add('busy');
  try{ await fn(); r&&r.classList.remove('busy'); r&&(r.classList.remove('flash'),void r.offsetWidth,r.classList.add('flash')); }
  catch(e){ r&&r.classList.remove('busy'); alert('Nicht übernommen: '+(e&&e.message||e)); render(); } }
function updateLive(){
  document.querySelectorAll('[data-val]').forEach(el=>{ const h=fmtState(S(el.dataset.val)); if(el._h!==h){ el._h=h; el.innerHTML=h; } });
  document.querySelectorAll('[data-num]').forEach(el=>{ if(document.activeElement===el || (el._dirty && Date.now()-el._dirty<10000)) return; const s=S(el.dataset.num); if(s){ const v=parseFloat(s.state); if(!isNaN(v)&&+el.value!==v) el.value=v; } });
  document.querySelectorAll('[data-sel]').forEach(el=>{ if(document.activeElement===el || (el._dirty && Date.now()-el._dirty<10000)) return; const s=S(el.dataset.sel); if(s&&el.value!==s.state&&[...el.options].some(o=>o.value===s.state)) el.value=s.state; });
  document.querySelectorAll('[data-sw]').forEach(el=>{ if(el._dirty && Date.now()-el._dirty<10000) return; const s=S(el.dataset.sw); if(s) el.classList.toggle('on',s.state==='on'); });
  { const w=$('#o_wp'); if(w) w.innerHTML=wpRows(); const t=$('#s_tiles'); if(t) t.innerHTML=sTiles(); }
}

/* =================== Zuordnung (wie bei Bosch: Panel-Seite „Zuordnung“) =================== */
function vMap(){ return `<iframe class="fr" src="/api/ems_esp_manager/panel?vendor=samsung&page=map" title="Zuordnung"></iframe>`; }
/* =================== Verbindung (bei Bosch: MQTT) =================== */
function vConn(){
  const d=ST; if(!d) return '<div class="empty">Lade …</div>';
  if(d.err) return `<div class="card pad"><div class="note top0">${esc(d.err)}</div></div>`;
  const lvl=d.log_level||'default';
  const inst=(d.instances||[]).map(x=>{ const s=x.stats||{}, yn=v=>v==null?'–':v?'Ein':'Aus';
    const pill=!x.loaded?'<span class="pill dm">Nicht geladen</span>':x.connected?'<span class="pill">Verbunden</span>':'<span class="pill no">Getrennt</span>';
    const stat={loaded:'Geladen',setup_error:'Einrichtungsfehler',setup_retry:'Einrichtung wird wiederholt',not_loaded:'Nicht geladen',failed_unload:'Entladen fehlgeschlagen',setup_in_progress:'Wird eingerichtet'}[x.state]||x.state;
    return `<div class="col"><div class="card"><div class="cap">${esc(x.title)}</div>`
      +row('Verbindung (RS485-Adapter)',pill)+row('Adresse',esc((x.ip||'–')+':'+(x.port||'–')))+row('Integrationsstatus',esc(stat))
      +row('Schreibmodus (Steuermodus)',yn(x.write_mode))+row('Polling',yn(x.polling))+row('Erweitertes Logging',yn(x.extended_logging))
      +`<div class="btns"><button class="btn" data-re="${esc(x.entry_id)}">${I.re} Neu verbinden</button><a class="btn" href="/config/integrations/integration/ehs_sentinel" target="_top">${I.open} EHS Sentinel</a></div></div>`
      +`<div class="card"><div class="cap">Pakete</div>`+row('Gelesen',nf(s.packets_read))+row('Verarbeitet',nf(s.packets_processed))+row('Angefordert',nf(s.packets_requested))
      +row('Fremde Pakete (nicht Innen/Außen)',nf(s.packets_processed_not_indoor_outdoor))+row('Warteschlange',nf(x.queue))+'</div></div>';
  }).join('') || '<div class="card pad"><div class="note top0">EHS Sentinel ist nicht eingerichtet (HACS-Integration „EHS Sentinel“ mit RS485-Adapter).</div></div>';
  return `<div class="grid">${inst}<div class="col"><div class="card"><div class="cap">Protokoll</div>
      <div class="row"><span class="k">Protokollstufe</span><select data-act="lvl">${Object.entries(LV).map(([k,l])=>`<option value="${k}" ${k===lvl?'selected':''}>${l}</option>`).join('')}</select></div>
      <div class="btns"><button class="btn" data-act="diag">Diagnose anfordern</button><a class="btn" href="#verbindung">${I.log} EHS-Log</a></div>
      <div class="note">Schreibmodus, Polling und erweitertes Logging stellst du in EHS Sentinel unter „Konfigurieren“ ein. Das Paket-Log entsteht nur mit erweitertem Logging.</div></div></div></div>`;
}
/* =================== Web-Oberfläche des RS485-Adapters =================== */
function vWeb(){ const u=webUrl(); if(!u) return '<div class="empty">Keine Adresse des RS485-Adapters bekannt.</div>';
  return `<div class="tools"><span class="note top0" style="padding:0">Weboberfläche des RS485-Adapters (z. B. Elfin EW11) unter <b>${esc(u)}</b>. Bleibt das Feld leer (Browser blockiert http in https), im neuen Fenster öffnen.</span><span class="sp"></span><a class="btn sm" href="${esc(u)}" target="_blank" rel="noopener">${I.open} Öffnen</a></div>
    <iframe class="fr" src="${esc(u)}" title="Web-Oberfläche"></iframe>`; }
/* =================== Protokoll =================== */
const LV={default:'Standard (HA)',info:'Info',debug:'Debug'};
function eLogRows(){
  if(LOG.err) return `<div class="note top0">${esc(LOG.err)}</div>`;
  if(!LOG.data) return '<div class="empty">Lade …</div>';
  if(LOG.pkt){ const d=LOG.data, ls=(d.lines||[]).slice().reverse();
    return !(d.files||[]).length ? '<div class="note top0">Kein Paket-Log vorhanden. In EHS Sentinel unter „Konfigurieren“ das erweiterte Logging aktivieren – die Datei liegt dann unter www/ehs_sentinel/logs/.</div>'
      : `<div class="cap">${esc(d.file||'')}</div>`+(ls.map(l=>`<div class="pkt">${esc(l)}</div>`).join('')||'<div class="note top0">Datei ist leer.</div>'); }
  const es=(LOG.data.entries||[]).slice().reverse();
  if(!es.length) return `<div class="empty">${ST&&ST.log_enabled===false?'Aufzeichnung ist aus. Bei Bedarf oben einschalten.':'Keine Meldungen in dieser Auswahl.'}</div>`;
  return `<div class="logs">${es.map(e=>`<div class="lr ${esc(e.level)}"><span class="lt">${new Date(e.ts*1000).toLocaleTimeString('de-DE')}</span><span class="ll">${esc(e.level)}</span><span class="lm">${esc(e.msg)}<span class="ln">${esc(e.name)}</span></span></div>`).join('')}</div>`;
}
function eLogText(){
  const x=((ST&&ST.instances)||[])[0]||{};
  const head=[`EHS-Log – EMS-ESP Manager ${(ST&&ST.version)||''} · EHS Sentinel ${(ST&&ST.ehs_version)||''}`,`Exportiert: ${new Date().toLocaleString('de-DE')}`,
    `Adapter: ${(x.ip||'–')+':'+(x.port||'–')} · ${LOG.pkt?'Paket-Log':'Filter: '+({all:'Alle',info:'Info+',warning:'Warnungen',error:'Fehler'})[LOG.level]}`,''];
  const body=LOG.pkt?((LOG.data&&LOG.data.lines)||[]):((LOG.data&&LOG.data.entries)||[]).map(e=>`${new Date(e.ts*1000).toLocaleString('de-DE')}\t${e.level}\t${e.name}\t${e.msg}`);
  return head.concat(body).join('\r\n');
}
function vLog(){
  const on=!(ST&&ST.log_enabled===false), lvl=(ST&&ST.log_level)||'default', x=((ST&&ST.instances)||[])[0], s=(x&&x.stats)||{};
  const n=LOG.pkt?((LOG.data&&LOG.data.lines)||[]).length:((LOG.data&&LOG.data.entries)||[]).length;
  const f=(k,l)=>`<button class="chip ${!LOG.pkt&&LOG.level===k?'on':''}" data-lv="${k}">${l}</button>`;
  const info=x?`Adapter ${esc((x.ip||'–')+':'+(x.port||'–'))}: ${x.connected?'✓ verbunden':'✗ getrennt'} · Pakete ${nf(s.packets_read)} gelesen / ${nf(s.packets_processed)} verarbeitet · Warteschlange ${nf(x.queue)}${x.extended_logging?'':' · Paket-Log aus (erweitertes Logging)'}`:'';
  return `<div class="card"><div class="cap">EHS-Log</div>
    ${hRow('Aufzeichnung',`<button class="tog ${on?'on':''}" data-act="elog" aria-label="umschalten"></button>`,on?`läuft – ${n} Einträge (max. 2000, bis zum Neustart)`:'aus – nur bei Bedarf einschalten')}
    ${hRow('Protokollstufe',`<select data-act="lvl">${Object.entries(LV).map(([k,l])=>`<option value="${k}" ${k===lvl?'selected':''}>${l}</option>`).join('')}</select>`,'„Debug“ nur zur Fehlersuche')}
    <div class="tools" style="margin:12px 0 8px">${f('all','Alle')}${f('info','Info+')}${f('warning','Warnungen')}${f('error','Fehler')}<button class="chip ${LOG.pkt?'on':''}" data-act="pkt">Paket-Log</button></div>
    <div class="tools" style="margin:0 0 12px"><button class="btn sm" data-act="diag">${I.re} Diagnose</button>
      <button class="btn sm" data-act="pause">${LOG.pause?'▶ Weiter':'❚❚ Pause'}</button>
      <button class="btn sm" data-act="edl" ${n?'':'disabled'}>${I.dl} Herunterladen</button>
      <button class="btn sm" data-act="copy" ${n?'':'disabled'}>${I.copy} Kopieren</button><button class="btn sm" data-act="clear" ${n?'':'disabled'}>Leeren</button></div>
    ${info?`<div class="note" style="padding-top:8px">${info}</div>`:''}
    <div id="elogbody" style="max-height:480px;overflow:auto;border-top:1px solid var(--sep);padding-top:6px">${eLogRows()}</div>
    <div class="note">Zeichnet bei eingeschalteter Aufzeichnung alle Meldungen von EHS Sentinel auf (max. 2000, bis zum Neustart). Standard: aus – der Schalter wird gespeichert. „Diagnose“ schreibt den aktuellen Zustand ins Log. Das Paket-Log entsteht nur mit erweitertem Logging in EHS Sentinel.</div></div>`;
}
let LSEQ=0, LBUSY=false;
async function loadLog(full){
  const my=++LSEQ; LBUSY=true;
  try{ const r = LOG.pkt ? await ws({type:'ems_esp_manager/samsung/packet_log',lines:400}) : await ws({type:'ems_esp_manager/samsung/logs',level:LOG.level,limit:800});
       if(my!==LSEQ) return; LOG.data=r; LOG.err=null; }
  catch(e){ if(my!==LSEQ) return; LOG.err = e&&e.code==='unauthorized'?'Nur für Administratoren.':(e&&e.message)||String(e); }
  finally{ if(my===LSEQ) LBUSY=false; }
  if(TAB!=='verbindung') return;
  const b=$('#elogbody'); if(b && !full){ const t=b.scrollTop; b.innerHTML=eLogRows(); b.scrollTop=t; } else render();
}

/* =================== Heizstab (novolto) – eine Seite: Status, Hinweise, Regelung, Geräte-Einstellungen, Log =================== */
let HT=null, HTD=null, HTD_T=0, HLOG={cat:'all',pause:false,data:null,err:null}, HT_MSG='', MQ_EDIT=false;
const HT_STATE={disabled:'Aus',initializing:'Startet',heating:'Heizt',max_temperature_reached:'Temperatur erreicht',error:'Störung'};
const HT_ERR={mqtt_disconnected:'MQTT-Verbindung war unterbrochen',sensor_error:'Temperaturfühler gestört',overtemperature:'Übertemperatur'};
const HD_NS={mqtt:'MQTT-Verbindung (im Gerät)',wifi:'WLAN (im Gerät)',heating:'Heizen (im Gerät)'};
const HD_LBL={'mqtt.broker_uri':'Broker-Adresse','mqtt.base_topic':'Base-Topic','mqtt.client_id':'Client-ID','mqtt.username':'Benutzer','mqtt.password':'Passwort',
  'mqtt.ha_discovery':'HA-Discovery','mqtt.heat_msmt_ivl':'Messwerte senden alle','mqtt.device_temp_ivl':'Platinentemperatur senden alle','mqtt.legacy_info_ivl':'Legacy-Info senden alle',
  'wifi.ssid':'WLAN-Name (SSID)','wifi.pw':'WLAN-Passwort','heating.mode':'Betriebsart','heating.target_temp':'Zieltemperatur','heating.temp_hyst':'Hysterese',
  'heating.temp_hyst_mode':'Hysterese-Lage','heating.target_power':'Leistung','heating.require_mqtt':'Nur mit MQTT heizen'};
const HD_HINT={'mqtt.broker_uri':'mqtt://IP:1883 = Home Assistant · mqtts://mqtt.novolto.de:8883 = Cloud','mqtt.base_topic':'{serial} = Seriennummer (Standard)',
  'mqtt.ha_discovery':'leer lassen – der Manager legt den Heizstab selbst an','mqtt.device_temp_ivl':'0 = aus','mqtt.legacy_info_ivl':'0 = aus (nur für ältere Integrationen)',
  'wifi.ssid':'Änderung trennt die WLAN-Verbindung','heating.require_mqtt':'Sicherheit: heizt nur bei MQTT-Verbindung (empfohlen)',
  'heating.mode':'wird bei aktiver Regelung von Home Assistant gesetzt','heating.target_power':'wird bei aktiver Regelung von Home Assistant gesetzt'};
const HD_U={'mqtt.heat_msmt_ivl':'s','mqtt.device_temp_ivl':'s','mqtt.legacy_info_ivl':'s','heating.target_temp':'°C','heating.temp_hyst':'K','heating.target_power':'W'};
const HD_EN={off:'Aus',manual:'Ein (feste Leistung)',symmetric:'symmetrisch',below_target:'unterhalb des Ziels'};
const HLOG_CAT={all:'Alle',send:'Gesendet',recv:'Empfangen',conn:'Verbindung',web:'Web-API',ctrl:'Regelung'};
const W=v=>v==null?'–':nf(v,0)+' W';
async function htLoad(){ try{ HT=await ws({type:'ems_esp_manager/panel/heater'}); }catch(e){ HT=null; } }
async function htdLoad(set){
  try{ const r=await ws(Object.assign({type:'ems_esp_manager/panel/heater_dev'},set?{set}:{})); HTD=r; HTD_T=Date.now();
    if(set){ HT_MSG=r.saved?'ok:Im Heizstab gespeichert':'err:'+(r.error||'Nicht gespeichert'); if(r.saved) MQ_EDIT=false; } }
  catch(e){ HTD={error:e&&e.code==='unauthorized'?'Geräte-Einstellungen nur für Administratoren.':(e&&e.message)||String(e)}; }
}
const typing=()=>{ const a=document.activeElement; return !!(a && a.closest && a.closest('#main') && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName) && a.type!=='number'); };
function safeRender(){ if(typing()) hLogRefresh(); else render(); }
async function htSave(patch){ try{ HT=await ws({type:'ems_esp_manager/panel/heater',config:patch}); HT_MSG='ok:Gespeichert'; }catch(e){ HT_MSG='err:Nicht gespeichert: '+((e&&e.message)||e); }
  if('log_enabled' in patch) await loadHLog(); render(); }
const hc=()=>(HT&&HT.config)||{};
/* Zahlenfelder: +/− ändern nur den Zwischenstand (HPEND). Erst 0,8 s nach dem letzten Klick wird gespeichert.
   Bis dahin zeigt jedes Neuzeichnen den Zwischenstand – kein Zurückspringen durch die 4-s-Aktualisierung. */
const HPEND={}, HTIMER={}; let H_TOUCH=0;
const hKey=el=>el.dataset.hcn!=null?'c:'+el.dataset.hcn:el.dataset.hdn!=null?'d:'+el.dataset.hdn:null;
function hClamp(el,v,snap){ const st=+el.step||1, lo=el.min!==''?+el.min:null, hi=el.max!==''?+el.max:null;
  if(isNaN(v)) v=lo??0; const base=lo??0; if(snap!==false) v=base+Math.round((v-base)/st)*st; else v=Math.round(v);
  if(lo!=null) v=Math.max(lo,v); if(hi!=null) v=Math.min(hi,v); return +v.toFixed(4); }
function hSetPend(el,v,delay,snap){ const k=hKey(el); if(!k) return; v=hClamp(el,v,snap); el.value=v; HPEND[k]=v; H_TOUCH=Date.now();
  const r=el.closest('.row'); r&&r.classList.add('pend');
  clearTimeout(HTIMER[k]); HTIMER[k]=setTimeout(()=>hCommit(k),delay??800); }
async function hCommit(k){
  if(!(k in HPEND)) return; const v=HPEND[k]; let ok=true;
  try{ if(k.startsWith('c:')){ HT=await ws({type:'ems_esp_manager/panel/heater',config:{[k.slice(2)]:v}}); HT_MSG='ok:Gespeichert'; }
       else { const [ns,key]=k.slice(2).split('.'); await htdLoad({[ns]:{[key]:v}}); ok=!!(HTD&&HTD.saved); } }
  catch(e){ ok=false; HT_MSG='err:Nicht gespeichert: '+((e&&e.message)||e); }
  if(HPEND[k]===v) delete HPEND[k];        // inzwischen weiter geklickt? Dann bleibt der neue Zwischenstand
  if(!Object.keys(HPEND).length) safeRender();
}
const hBusy=()=>Object.keys(HPEND).length>0 || Date.now()-H_TOUCH<2500;
function hNum(attr,key,v,min,max,step,u){
  const k=(attr==='data-hcn'?'c:':'d:')+key; if(k in HPEND) v=HPEND[k];
  return `<span class="ctl"><span class="num"><button data-hstep="-1" aria-label="weniger">−</button><input type="number" inputmode="decimal" ${attr}="${esc(key)}" value="${esc(v??'')}" step="${esc(step||1)}" min="${esc(min??'')}" max="${esc(max??'')}"><button data-hstep="1" aria-label="mehr">+</button></span><span class="u">${esc(u||'')}</span></span>`;
}
function hSel(attr,key,cur,opts){ return `<select ${attr}="${esc(key)}">${opts.map(([v,l])=>`<option value="${esc(v)}" ${String(v)===String(cur)?'selected':''}>${esc(l)}</option>`).join('')}</select>`; }
const hRow=(k,ctl,sub)=>`<div class="row"><span class="k">${esc(k)}${sub?`<small>${esc(sub)}</small>`:''}</span>${ctl}</div>`;
const hIn=(id,v,w,extra)=>`<input class="q" style="min-width:0;width:${w||170}px;height:36px" id="${id}" value="${esc(v||'')}" ${extra||''}>`;
const hTog=(attr,key,on)=>`<button class="tog ${on?'on':''}" ${attr}="${esc(key)}" aria-label="umschalten"></button>`;
const EOPT=new Map(); let EOPT_SRC=null;
function entOpts(f,none){ const h=H(); const st=(h&&h.states)||{};
  if(EOPT_SRC!==st){ EOPT_SRC=st; EOPT.clear(); }
  let o=EOPT.get(f); if(!o){ o=Object.keys(st).filter(f).sort().map(id=>[id,((st[id].attributes||{}).friendly_name||id)]); EOPT.set(f,o); }
  return none?[['',none],...o]:o; }
const fW=id=>id.startsWith('sensor.')&&/^(k?w)$/i.test(((S(id).attributes||{}).unit_of_measurement)||'');
const fPct=id=>id.startsWith('sensor.')&&((S(id).attributes||{}).unit_of_measurement)==='%';
const fRel=id=>/^(binary_sensor|switch|input_boolean)\./.test(id);
function hInfo(){
  const h=HT||{}, c=hc(), lv=h.live||{}, web=h.web||{}, ck=h.check||{};
  const reach=!!(h.mqtt && c.topic && lv.meas && lv.online!==false && (lv.age_meas==null||lv.age_meas<=60));
  const bu=HTD&&HTD.settings?((HTD.settings.mqtt||{}).broker_uri||{}).value||'':(ck.broker_uri||'');
  const webBad=!!(web.host && (ck.web===false || (HTD && !HTD.settings && HTD.error && !/Administrator/.test(HTD.error))));
  return {reach, bu, cloud:/novolto\.de/.test(bu), webBad, webOk:!!(web.host && (ck.web || (HTD && HTD.settings)))};
}
function htStatusTxt(){ const h=HT; if(!h) return '–'; const c=h.config, lv=h.live||{}, s=lv.status||{}, m=lv.meas||{};
  if(!h.mqtt) return 'MQTT fehlt'; if(!c.topic) return 'kein Gerät gewählt'; if(lv.online===false||!lv.meas) return 'keine Daten';
  if(s.state==='heating') return 'Heizt mit '+W(m.power);
  if(s.state==='error') return 'Störung';
  return (HT_STATE[s.state]||s.state||'–'); }
/* ---- Hinweise: nur was gerade wichtig ist, mit dem nächsten Schritt ---- */
function hHints(){
  const h=HT, c=hc(), lv=h.live||{}, s=lv.status||{}, web=h.web||{}, x=hInfo(), out=[];
  const add=(lvl,t,todo)=>out.push({lvl,t,todo});
  if(!h.mqtt) add('err','In Home Assistant ist MQTT nicht eingerichtet.','Mosquitto-Broker und die MQTT-Integration einrichten.');
  else if(!c.topic) add('err','Kein Heizstab gewählt.','Unter „System → Gerät finden“ das Topic (Seriennummer) wählen.');
  else if(x.cloud) add('warn','Der Heizstab sendet an die novolto-Cloud – Home Assistant bekommt keine Daten.','Unter „System → Verbindung zu Home Assistant“ auf Home Assistant umstellen.');
  else if(!x.reach) add('err','Vom Heizstab kommen keine Messwerte.','Unter „System → Verbindung zu Home Assistant“ Broker, Benutzer und Passwort prüfen, dann „Verbindung prüfen“. Details unter „Logs“.');
  if(s.state==='error' && s.error) add('warn','Heizstab meldet: '+(HT_ERR[s.error]||s.error)+'.', s.error==='mqtt_disconnected'?'Verschwindet, sobald der Heizstab wieder Befehle bekommt.':'');
  if(x.webBad && x.reach){ /* Hinweis steht direkt in der Karte „Einstellungen im Gerät“ */ }
  else if(x.webBad) add('warn','Weboberfläche unter '+web.host+' antwortet nicht und vom Heizstab kommen keine MQTT-Daten.','IP im Router prüfen (unter „System → Gerät finden“ eintragen) oder den Heizstab neu starten bzw. den AP-Modus nutzen.');
  else if(!web.host) add('info','Keine Web-Adresse gespeichert – die Einstellungen im Gerät brauchen sie.','Unter „System → Gerät finden“ die IP eintragen oder im Heimnetz suchen.');
  if(x.reach && !c.enabled) add('info','Die Regelung ist aus – Home Assistant steuert den Heizstab nicht.','Unter „Regelung“ einschalten.');
  if(c.enabled && (c.control||'pv')==='pv' && !c.surplus_entity) add('warn','PV-Überschuss gewählt, aber kein Überschuss-Sensor.','Unter „Regelung“ einen Sensor mit W/kW wählen.');
  return out;
}
function vHints(){
  const hs=hHints(); if(!hs.length) return '';
  const ico={err:'⛔',warn:'⚠️',info:'ℹ️'};
  return '<div style="height:12px"></div>'+hs.map(x=>`<div class="hint ${x.lvl}"><span class="hi">${ico[x.lvl]}</span><span class="ht">${esc(x.t)}${x.todo?`<small>${esc(x.todo)}</small>`:''}</span></div>`).join('');
}
/* ---- Aufbau: oben Heizstab (Status), darunter Auswahl Regelung | MQTT-Einstellungen | Info & Log ---- */
let HSUB='start';
try{ HSUB=localStorage.getItem('emsehs_hsub')||'start'; }catch(e){}
HSUB=({mqtt:'system',info:'log'})[HSUB]||HSUB;
const HSUBS=[['start','Übersicht','home'],['regelung','Regelung','sl'],['system','System','gear'],['log','Logs','log']];
const tile=(l,v,sub,cls)=>`<div class="tile ${cls||''}"><div class="tl">${esc(l)}</div><div class="tv">${v}</div>${sub?`<div class="ts">${esc(sub)}</div>`:''}</div>`;
function hTop(){
  const h=HT, c=hc(), lv=h.live||{}, s=lv.status||{}, m=lv.meas||{}, st=h.state||{}, web=h.web||{}, en=st.energy||{}, x=hInfo();
  const msg=HT_MSG?`<div class="msg ${HT_MSG.startsWith('ok:')?'ok':'err'}" style="padding:10px 0 0">${esc(HT_MSG.slice(HT_MSG.indexOf(':')+1))}</div>`:'';
  const on=s.state==='heating';
  return `<div class="card pad"><div class="htop">
      <div class="hid"><div class="hico ${on?'on':''}">${I.heat}</div><div><div class="t2">Heizstab</div><div class="ts">${esc((lv.info&&lv.info.serial)||c.topic||'kein Gerät')}${web.host?' · '+esc(web.host):''}</div></div></div>
      <div class="hpills">${x.reach?'<span class="pill">MQTT · Daten</span>':'<span class="pill no">MQTT · keine Daten</span>'}
        ${HTD&&HTD.via==='mqtt'?'<span class="pill">Einstellungen · über MQTT</span>':!web.host?'<span class="pill dm">Web · keine Adresse</span>':x.webBad?(x.reach?'<span class="pill dm">Web · nur AP-Modus</span>':'<span class="pill no">Web · antwortet nicht</span>'):x.webOk?'<span class="pill">Web · erreichbar</span>':'<span class="pill dm">Web · ungeprüft</span>'}
        ${c.enabled?'<span class="pill">Regelung an</span>':'<span class="pill dm">Regelung aus</span>'}</div>
      <div class="hbt"><button class="btn sm" data-act="hcheck">${I.re} Verbindung prüfen</button></div></div>
    <div class="tiles">
      ${tile('Status',on?'Heizstab Ein':'Heizstab Aus','',on?'hot':'')}
      ${tile('Leistung',lv.meas?W(m.power):'–',s.target_power!=null&&s.mode==='manual'?'Soll '+W(s.target_power):'')}
      ${tile('Wasser',m.water_temp==null?'–':nf(m.water_temp,1)+' °C',s.target_temp!=null?'max. '+nf(s.target_temp,0)+' °C':'')}
      ${tile('Netzspannung',m.voltage==null?'–':nf(m.voltage,0)+' V','')}
      ${tile('Energie heute',nf((en.wh||0)/1000,2)+' kWh',st.energy_yesterday!=null?'gestern '+nf(st.energy_yesterday/1000,2)+' kWh':'')}
    </div>${msg}</div>`;
}
function hSubnav(){ return `<nav class="subnav top bnav">${HSUBS.map(([k,l,ic])=>`<button class="chip ${HSUB===k?'on':''}" data-hsub="${k}">${BI[ic]}<span>${l}</span></button>`).join('')}</nav>`; }
function hCardRegelung(){
  const h=HT, c=hc(), mx=h.dev_max||3000, ctl=c.control||'pv', st=h.state||{};
  const how={pv:`Alle ${c.interval??10} s: Überschuss + aktuelle Heizstab-Leistung − Reserve, in ${c.step??50}-W-Schritten. Ohne gültigen Überschusswert (älter als 5 min) wird ausgeschaltet.`,
    manual:'Der Heizstab heizt mit fester Leistung, bis die Maximaltemperatur erreicht ist.',off:'Home Assistant hält den Heizstab aus.'}[ctl];
  return `<div class="card"><div class="cap">Regelung durch Home Assistant</div>
      ${hRow('Heizstab-Regelung',hTog('data-hct','enabled',c.enabled),c.enabled?'aktiv':'aus – Heizstab wird nicht gesteuert')}
      ${hRow('Betriebsart',hSel('data-hc','control',ctl,[['pv','PV-Überschuss'],['manual','Feste Leistung'],['off','Aus']]))}
      ${ctl==='manual'?hRow('Leistung',hNum('data-hcn','manual_power',c.manual_power,0,mx,50,'W')):''}
      ${ctl==='pv'?hRow('Überschuss-Sensor',hSel('data-hc','surplus_entity',c.surplus_entity||'',entOpts(fW,'– bitte wählen –')),'Netz- oder Einspeiseleistung in W/kW')
        +hRow('Vorzeichen',hSel('data-hc','surplus_invert',c.surplus_invert?'1':'0',[['0','Einspeisung positiv'],['1','Einspeisung negativ']]),'bei Netzzählern meist „negativ“')
        +hRow('Reserve',hNum('data-hcn','reserve',c.reserve,0,2000,50,'W'),'bleibt als Einspeisung übrig')+hRow('Mindestleistung',hNum('data-hcn','min_power',c.min_power,0,mx,50,'W'),'darunter aus')
        +hRow('Höchstleistung',hNum('data-hcn','max_power',Math.min(c.max_power??mx,mx),100,mx,50,'W'))+hRow('Abschaltverzögerung',hNum('data-hcn','off_delay',c.off_delay,0,600,10,'s'),'Mindestleistung so lange halten')
        +hRow('Regelintervall',hNum('data-hcn','interval',c.interval,5,120,5,'s')):''}
      <div class="note">${esc(how)}${c.enabled&&st.check?`<br><b>Jetzt:</b> ${esc(st.check)}`:''}${st.last?`<br><b>Zuletzt gesendet:</b> ${esc(st.last)}`:''}</div></div>`;
}
function hCardSpeicher(){
  const c=hc(), ctl=c.control||'pv';
  return `<div class="card"><div class="cap">Speicher &amp; Freigabe</div>
      ${hRow('Maximaltemperatur',hNum('data-hcn','target_temp',c.target_temp,20,80,1,'°C'),'regelt der Heizstab selbst')}
      ${hRow('Hysterese',hNum('data-hcn','temp_hyst',c.temp_hyst,4,10,1,'K'),'Schaltabstand um die Maximaltemperatur')}
      ${ctl!=='off'?hRow('Freigabe-Signal',hSel('data-hc','release_entity',c.release_entity||'',entOpts(fRel,'immer frei')),'muss „an“ sein')
        +hRow('Akku-Ladestand',hSel('data-hc','soc_entity',c.soc_entity||'',entOpts(fPct,'nicht prüfen')))+(c.soc_entity?hRow('Mindest-Ladestand',hNum('data-hcn','soc_min',c.soc_min,0,100,5,'%')):''):''}
    </div>`;
}
function hCardGeraet(){
  const h=HT, c=hc(), web=h.web||{};
  const devs=(h.devices||[]).map(d=>[d.topic,(d.serial||d.topic)+(d.fw?' · FW '+d.fw:'')]);
  if(c.topic && !devs.some(d=>d[0]===c.topic)) devs.push([c.topic,c.topic]);
  return `<div class="card"><div class="cap">Gerät finden</div>
      ${hRow('Web-Adresse',`<span class="ctl">${hIn('hhost',web.host,150,'placeholder="192.168.0.x"')}<button class="btn sm" data-act="hhost">Prüfen</button></span>`,'IP des Heizstabs (Router) – für Geräte-Einstellungen')}
      ${hRow('MQTT-Topic',hSel('data-hc','topic',c.topic||'',[['','– kein Gerät –'],...devs]),'Seriennummer – für Messwerte und Regelung')}
      <div class="btns"><button class="btn" data-act="hscan">Im Heimnetz suchen</button><button class="btn" data-act="htopic">Topic eingeben</button></div>
      ${web.scan?`<div class="note">${web.scan.running?'⏳ Suche läuft …':esc((web.scan.log||[]).slice(-1)[0]||'Suche beendet')}</div>`:''}
    </div>`;
}
function hDevMissing(title){
  const web=(HT&&HT.web)||{};
  const box=t=>`<div class="card pad"><div class="cap">${esc(title)}</div>${t}</div>`;
  if(!web.host) return box('<div class="note top0">Diese Werte werden direkt aus dem Heizstab gelesen. Dafür unter „System → Gerät finden“ seine IP eintragen.</div>');
  if(!HTD) return box('<div class="note top0">Lese Einstellungen aus dem Heizstab …</div>');
  if(!HTD.settings){
    const det=HTD.error_detail?`<details style="margin-top:8px"><summary style="cursor:pointer;color:var(--lbl);font-size:12px">Technische Meldung</summary><code>${esc(HTD.error_detail)}</code></details>`:'';
    if(hInfo().reach) return box(`<div class="row" style="justify-content:flex-start;gap:12px;border-top:0"><span style="flex:none">ℹ️</span><span class="k"><b>Im MQTT-Betrieb gesperrt</b><small style="word-break:normal">Regelung und Messwerte laufen normal über MQTT – nur die Weboberfläche (${esc(web.host)}) ist abgeschaltet.</small></span></div>
      <div class="note" style="padding-top:8px"><b>Einstellungen ändern – so geht’s:</b>
        <ol style="margin:6px 0 0;padding-left:20px;line-height:1.5">
          <li>Heizstab kurz vom Strom trennen und wieder einstecken. Danach ist die Weboberfläche eine Weile erreichbar → <b>„Erneut versuchen“</b>.</li>
          <li>Oder AP-Modus: Taste an der Unterseite drücken, Handy/PC mit dem WLAN „NOVOLTO-…“ verbinden und <b>http://192.168.2.1/expert</b> öffnen.</li>
        </ol>${det}</div>
      <div class="btns"><button class="btn pri" data-act="hdload">${I.re} Erneut versuchen</button><a class="btn" href="http://192.168.2.1/expert" target="_blank" rel="noopener">${I.open} AP-Seite öffnen</a></div>`);
    return box(`<div class="row" style="justify-content:flex-start;gap:12px;border-top:0"><span style="flex:none">⚠️</span><span class="k"><b>${esc(HTD.error||'Nicht lesbar')}</b><small style="word-break:normal">Vom Heizstab kommen auch keine MQTT-Daten.</small></span></div>
      <div class="note" style="padding-top:8px">IP im Router prüfen und unter „System → Gerät finden“ eintragen – oder den Heizstab neu starten bzw. den AP-Modus nutzen (WLAN „NOVOLTO-…“ → http://192.168.2.1).${det}</div>
      <div class="btns"><button class="btn" data-act="hdload">${I.re} Erneut versuchen</button><span></span></div>`);
  }
  return '';
}
function hNsCard(ns,title){
  const S_=HTD.settings; if(!S_[ns]) return '';
  return `<div class="card"><div class="cap">${esc(title||HD_NS[ns]||ns)}</div>${Object.keys(S_[ns]).map(k=>{ const d=S_[ns][k], id=ns+'.'+k, l=HD_LBL[id]||k;
      let ctl;
      if(d.type==='boolean') ctl=hTog('data-hdt',id,d.value);
      else if(d.type==='enum') ctl=hSel('data-hd',id,d.value,(d.allowed_values||[]).map(o=>[o,HD_EN[o]||o]));
      else if(d.type==='int32'){ const big=d.max==null||d.max>100000; ctl=hNum('data-hdn',id,d.value,d.min,big?86400:d.max,k==='target_power'?50:1,HD_U[id]); }
      else ctl=`<input class="q" ${d.sensitive?'type="password" placeholder="verborgen – neu eingeben"':''} style="min-width:0;width:200px;height:36px" data-hds="${esc(id)}" value="${d.sensitive?'':esc(d.value??'')}" autocomplete="off">`;
      const sub=HD_HINT[id]||(!d.sensitive&&d.default!=null&&String(d.default)!==String(d.value)?'Werk: '+d.default:'');
      return hRow(l,ctl,sub);
    }).join('')}<div class="note" style="display:flex;align-items:center;gap:10px"><span style="flex:1">Wird sofort im Heizstab gespeichert${HTD.via==='mqtt'?' – <b>über MQTT</b> (Weboberfläche gesperrt)':''}.</span><button class="btn sm" data-act="hdload">${I.re} Neu lesen</button></div></div>`;
}
function hMqCard(){
  const S_=HTD.settings, bu=((S_.mqtt||{}).broker_uri||{}).value||'', cloud=/novolto\.de/.test(bu), form=cloud||MQ_EDIT;
  return `<div class="card"><div class="cap">Verbindung zu Home Assistant</div>
      ${row('Heizstab sendet an',cloud?'<span class="pill dm">novolto-Cloud</span>':'<span class="pill">Home Assistant</span>',cloud?'':bu)}
      ${HTD.via==='mqtt'&&!cloud?'<div class="note">⚠️ Diese Einstellungen laufen gerade über MQTT. Falsche Broker-Daten trennen die Verbindung – dann geht es nur noch über den AP-Modus (WLAN „NOVOLTO-…“ → http://192.168.2.1/expert).</div>':''}
      ${form?`${hRow('Broker',hIn('hmb',HTD.ha_ip,150),'IP von Home Assistant')}
      ${hRow('Port',hIn('hmp','1883',80,'inputmode="numeric"'),'Mosquitto: 1883')}
      ${hRow('Benutzer',hIn('hmu','',150,'autocomplete="off"'),'HA-Benutzer oder Mosquitto-Login')}
      ${hRow('Passwort',hIn('hmw','',150,'type="password" autocomplete="off"'))}
      <div class="btns"><button class="btn pri" data-act="hmqtt">Auf Home Assistant umstellen</button>${cloud?'<span></span>':'<button class="btn" data-act="hmqx">Abbrechen</button>'}</div>
      <div class="note">Danach laufen novolto-App, Cloud-Regelung und Cloud-Updates nicht mehr. Rückweg: „Zurück zur novolto-Cloud“.</div>`
      :`<div class="btns"><button class="btn" data-act="hmqe">Zugangsdaten ändern</button><button class="btn" data-act="hcloud">Zurück zur novolto-Cloud</button></div>`}</div>`;
}
function hCardInfo(){
  const h=HT, c=hc(), lv=h.live||{}, web=h.web||{}, x=hInfo(), i=lv.info||{}, ck=h.check||{};
  const dv=HTD&&HTD.settings, hs=dv&&dv.heating;
  return `<div class="card"><div class="cap">Info</div>
      ${row('Seriennummer',esc(i.serial||'–'))}${row('Firmware',esc(i.fw||'–'))}${row('Hardware',esc(i.hw||'–'))}${lv.api?row('MQTT-API',esc(lv.api)):''}
      ${row('MQTT-Topic',esc(c.topic||'–'))}${row('Web-Adresse',esc(web.host||'–'))}
      ${x.bu?row('Broker im Gerät',x.cloud?'<span class="pill dm">novolto-Cloud</span>':esc(x.bu.replace(/^mqtts?:\/\//,''))):''}
      ${hs?row('Gerät heizt laut Einstellung',esc(HD_EN[(hs.mode||{}).value]||(hs.mode||{}).value||'–'),'Ziel '+((hs.target_temp||{}).value??'–')+' °C · '+((hs.target_power||{}).value??'–')+' W'):''}
      ${ck.ts?row('Letzte Prüfung',new Date(ck.ts*1000).toLocaleTimeString('de-DE'),'MQTT '+(ck.mqtt?'✓':'✗')+(ck.host?' · Web '+(ck.web?'✓ '+ck.web_ms+' ms':'✗'):'')):''}
      ${web.host?`<div class="btns"><a class="btn" href="${esc(web.expert_url)}" target="_blank" rel="noopener">${I.open} Experten-Seite</a><a class="btn" href="${esc(web.url)}" target="_blank" rel="noopener">${I.open} Startseite</a></div>`:''}
    </div>`;
}
function vHeater(){
  if(!HT) return '<div class="empty">Lade …</div>';
  if(!HSUBS.some(x=>x[0]===HSUB)) HSUB='start';
  const msg=HT_MSG?`<div class="hint ${HT_MSG.startsWith('ok:')?'okm':'err'}"><span class="hi">${HT_MSG.startsWith('ok:')?'✓':'⛔'}</span><span class="ht">${esc(HT_MSG.slice(HT_MSG.indexOf(':')+1))}</span></div>`:'';
  let body;
  if(HSUB==='system'){
    const miss=hDevMissing('Einstellungen im Gerät'), h=HT, c=hc(), lv=h.live||{}, i=lv.info||{}, web=h.web||{};
    const S_=HTD&&HTD.settings, bu=(S_&&((S_.mqtt||{}).broker_uri||{}).value)||'';
    const L=[
      bAcc('h-dev',BI.scan,'Gerät finden',esc(c.topic||'kein Gerät')+(web.host?' · '+esc(web.host):''),unwrap(hCardGeraet())),
      miss?'':bAcc('h-mq',BI.link,'Verbindung zu Home Assistant',/novolto\.de/.test(bu)?'novolto-Cloud':'Home Assistant'+(bu?' · '+esc(bu.replace(/^mqtts?:\/\//,'')):''),unwrap(hMqCard())),
      miss?'':bAcc('h-mqtt',BI.chip,'MQTT im Gerät','Einstellungen im Heizstab',unwrap(hNsCard('mqtt','MQTT im Gerät'))),
      miss?'':bAcc('h-wifi',BI.poll,'WLAN im Gerät','Einstellungen im Heizstab',unwrap(hNsCard('wifi','WLAN im Gerät'))),
      bAcc('h-info',BI.book,'Info',esc(i.serial||'–')+(i.fw?' · FW '+esc(i.fw):''),unwrap(hCardInfo()))].filter(Boolean);
    const hh=Math.ceil(L.length/2);
    body=`<div class="grid"><div class="col accs">${L.slice(0,hh).join('')}</div><div class="col accs">${L.slice(hh).join('')}${miss||''}</div></div>`;
  } else if(HSUB==='log'){
    body=vHLogCard().replace('style="margin-top:18px"','');
  } else if(HSUB==='regelung'){
    const miss=hDevMissing('Heizen im Gerät');
    body=`<div class="grid"><div class="col">${hCardRegelung()}</div><div class="col">${hCardSpeicher()}${miss||hNsCard('heating','Heizen im Gerät')}</div></div>`;
  } else {
    return hSubnav()+hTop()+vHints();
  }
  return hSubnav()+msg+body;
}

/* ---- Log (gleiche Seite, unten) ---- */
function hLogRows(){
  const es=((HLOG.data&&HLOG.data.entries)||[]).slice().reverse();
  if(HLOG.err) return `<div class="note top0">${esc(HLOG.err)}</div>`;
  if(!HLOG.data) return '<div class="empty">Lade …</div>';
  if(!es.length) return `<div class="empty">${hc().log_enabled?'Noch keine Einträge.':'Aufzeichnung ist aus. Bei Bedarf oben einschalten.'}</div>`;
  return `<div class="logs">${es.map(e=>`<div class="lr ${esc(e.level)}"><span class="lt">${new Date(e.ts*1000).toLocaleTimeString('de-DE')}</span><span class="ll">${esc(HLOG_CAT[e.cat]||e.cat)}</span><span class="lm">${esc(e.msg)}${e.detail?`<span class="ln">${esc(e.detail)}</span>`:''}</span></div>`).join('')}</div>`;
}
function vHLogCard(){
  const on=!!hc().log_enabled, ck=(HLOG.data&&HLOG.data.check)||(HT&&HT.check), n=((HLOG.data&&HLOG.data.entries)||[]).length;
  const f=k=>`<button class="chip ${HLOG.cat===k?'on':''}" data-hcat="${k}">${HLOG_CAT[k]}</button>`;
  const ckTxt=ck?`Letzte Prüfung ${new Date(ck.ts*1000).toLocaleTimeString('de-DE')}: MQTT ${ck.mqtt?'✓ Daten'+(ck.meas_age!=null?' (vor '+ck.meas_age+' s)':''):'✗ keine Daten'}${ck.host?' · Web '+(ck.web?'✓ '+ck.web_ms+' ms':'✗ antwortet nicht'):''}${ck.broker_uri?' · Broker '+ck.broker_uri:''}`:'';
  return `<div class="card" id="hlogcard" style="margin-top:18px"><div class="cap">Heizstab-Log</div>
    ${hRow('Aufzeichnung',hTog('data-hct','log_enabled',on),on?`läuft – ${n} Einträge (max. 1500, bis zum Neustart)`:'aus – nur bei Bedarf einschalten')}
    <div class="tools" style="margin:12px 0">${Object.keys(HLOG_CAT).map(f).join('')}<span class="sp"></span>
      <button class="btn sm" data-act="hcheck">${I.re} Verbindung prüfen</button>
      <button class="btn sm" data-act="hpause" ${on?'':'disabled'}>${HLOG.pause?'▶ Weiter':'❚❚ Pause'}</button>
      <button class="btn sm" data-act="hdl" ${n?'':'disabled'}>${I.dl} Herunterladen</button>
      <button class="btn sm" data-act="hcopy" ${n?'':'disabled'}>${I.copy} Kopieren</button><button class="btn sm" data-act="hclear" ${n?'':'disabled'}>Leeren</button></div>
    ${ckTxt?`<div class="note" style="padding-top:8px">${esc(ckTxt)}</div>`:''}
    <div id="hlogbody" style="max-height:480px;overflow:auto;border-top:1px solid var(--sep);padding-top:6px">${hLogRows()}</div>
    <div class="note">Zeichnet auf: gesendete Befehle (MQTT und Web-API) mit Inhalt, Antworten und Statuswechsel des Heizstabs, Regelentscheidungen, geänderte Einstellungen und Verbindungsprüfungen (im Hintergrund jede Minute – eingetragen wird nur eine Änderung). Messwerte 1× pro Minute. Passwörter erscheinen als ***.</div></div>`;
}
let HLSEQ=0;
async function loadHLog(action){
  const my=++HLSEQ;
  try{ const r=await ws(Object.assign({type:'ems_esp_manager/panel/heater_log',cat:HLOG.cat,limit:1500},action?{action}:{})); if(my!==HLSEQ) return; HLOG.data=r; HLOG.err=null; }
  catch(e){ if(my!==HLSEQ) return; HLOG.err=e&&e.code==='unauthorized'?'Log nur für Administratoren.':(e&&e.message)||String(e); }
}
function hLogRefresh(){ const b=$('#hlogbody'); if(b){ const top=b.scrollTop; b.innerHTML=hLogRows(); b.scrollTop=top; } }
function hLogText(){
  const h=HT||{}, c=hc(), web=h.web||{}, lv=h.live||{};
  const head=[`Heizstab-Log – EMS-ESP Manager ${(ST&&ST.version)||''}`,`Exportiert: ${new Date().toLocaleString('de-DE')}`,
    `Topic: ${c.topic||'–'} · Seriennr.: ${(lv.info&&lv.info.serial)||'–'} · Web: ${web.host||'–'} · Filter: ${HLOG_CAT[HLOG.cat]}`,''];
  return head.concat(((HLOG.data&&HLOG.data.entries)||[]).map(e=>`${new Date(e.ts*1000).toLocaleString('de-DE')}\t${e.level}\t${HLOG_CAT[e.cat]||e.cat}\t${e.msg}${e.detail?'\t'+e.detail:''}`)).join('\r\n');
}
async function heaterClick(b){
  const act=b.dataset.act;
  if(b.dataset.hstep){ const inp=b.parentNode.querySelector('input'); const k=hKey(inp);
    const cur=(k in HPEND)?HPEND[k]:parseFloat(inp.value); hSetPend(inp,(isNaN(cur)?0:cur)+(+b.dataset.hstep)*(+inp.step||1)); return true; }
  if(b.dataset.hct){ b.classList.toggle('on'); htSave({[b.dataset.hct]:!hc()[b.dataset.hct]}); return true; }
  if(b.dataset.hdt){ const [ns,k]=b.dataset.hdt.split('.'); const cur=((HTD.settings[ns]||{})[k]||{}).value; b.classList.toggle('on',!cur); await htdLoad({[ns]:{[k]:!cur}}); render(); return true; }
  if(b.dataset.hcat){ HLOG.cat=b.dataset.hcat; await loadHLog(); render(); return true; }
  if(act==='hgolog'){ HSUB='log'; try{localStorage.setItem('emsehs_hsub','log');}catch(e){} render(); window.scrollTo(0,0); return true; }
  if(b.dataset.hsub){ HSUB=b.dataset.hsub; try{localStorage.setItem('emsehs_hsub',HSUB);}catch(e){} HT_MSG=''; render(); return true; }
  if(act==='hcheck'){ b.disabled=true; await loadHLog('check'); await htLoad(); const c=(HLOG.data&&HLOG.data.check)||{};
    HT_MSG=(c.mqtt&&c.web!==false?'ok:':'err:')+'Prüfung: MQTT '+(c.mqtt?'– Daten kommen an':'– keine Daten')+(c.host?' · Web '+(c.web?'erreichbar ('+c.web_ms+' ms)':'antwortet nicht'):''); render(); return true; }
  if(act==='hpause'){ HLOG.pause=!HLOG.pause; render(); return true; }
  if(act==='hclear'){ if(!confirm('Heizstab-Log leeren?')) return true; await loadHLog('clear'); render(); return true; }
  if(act==='hdl'){ const blob=new Blob(['﻿'+hLogText()],{type:'text/plain;charset=utf-8'}); const u=URL.createObjectURL(blob);
    const d=new Date(), p=n=>String(n).padStart(2,'0'); const a=document.createElement('a'); a.href=u;
    a.download=`heizstab-log_${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}_${p(d.getHours())}-${p(d.getMinutes())}.txt`;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(u),5000); return true; }
  if(act==='hcopy'){ const txt=hLogText();
    try{ await navigator.clipboard.writeText(txt); }catch(e){ const t=document.createElement('textarea'); t.value=txt; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); } b.textContent='Kopiert ✓'; return true; }
  if(act==='hhost'){ const v=$('#hhost').value.trim(); if(!v){ alert('Bitte die IP des Heizstabs eingeben.'); return true; } b.disabled=true; HT_MSG='ok:Prüfe '+v+' …'; render();
    try{ HT=await ws({type:'ems_esp_manager/panel/heater',find:v}); }catch(e){ HT_MSG='err:'+((e&&e.message)||e); }
    setTimeout(async()=>{ await htLoad(); const web=(HT&&HT.web)||{}; const ok=(web.host===v.replace(/^https?:\/\//,'').split('/')[0]);
      HT_MSG=ok?'ok:Web-Adresse gespeichert':'err:'+(((web.scan||{}).log||['Unter dieser Adresse antwortet kein Heizstab.']).slice(-1)[0]);
      HTD=null; render(); await htdLoad(); render(); },3500); return true; }
  if(act==='hscan'){ try{ HT=await ws({type:'ems_esp_manager/panel/heater',find:''}); }catch(e){ alert(e&&e.message||e); } render(); return true; }
  if(act==='htopic'){ const v=prompt('MQTT-Topic des Heizstabs (Standard: Seriennummer, z. B. 143.143.A2DABC):',hc().topic||''); if(v!=null) htSave({topic:v.trim().replace(/\/+$/,'')}); return true; }
  if(act==='hdload'){ HTD=null; render(); await htdLoad(); render(); return true; }
  if(act==='hmqe'){ MQ_EDIT=true; render(); return true; }
  if(act==='hmqx'){ MQ_EDIT=false; render(); return true; }
  if(act==='hmqtt'){ const bb=$('#hmb').value.trim(), p=parseInt($('#hmp').value,10)||1883, u=$('#hmu').value.trim(), w=$('#hmw').value;
    if(!bb||!u||!w){ alert('Bitte Broker, Benutzer und Passwort eingeben.'); return true; }
    if(!confirm('Heizstab auf den MQTT-Broker von Home Assistant (mqtt://'+bb+':'+p+') umstellen?\n\nDie novolto-App und die Cloud funktionieren danach nicht mehr.')) return true;
    b.disabled=true; await htdLoad({mqtt:{broker_uri:'mqtt://'+bb+':'+p,username:u,password:w}}); render(); return true; }
  if(act==='hcloud'){ if(!confirm('Zurück zur novolto-Cloud (mqtts://mqtt.novolto.de:8883, Werks-Benutzer und -Passwort)?\n\nHome Assistant bekommt danach keine Daten mehr.')) return true;
    await htdLoad({mqtt:{broker_uri:'mqtts://mqtt.novolto.de:8883',username:'{default_username}',password:'{default_password}'}}); render(); return true; }
  return false;
}
function heaterChange(el){
  if(el.dataset.hc){ const k=el.dataset.hc; htSave({[k]:k==='surplus_invert'?el.value==='1':el.value}); return true; }
  if(el.dataset.hcn!=null || el.dataset.hdn!=null){ if(el.value==='') return true; hSetPend(el,parseFloat(el.value.replace(',','.')),50,false); return true; }
  const dev=el.dataset.hd||el.dataset.hds;
  if(dev){ const [ns,k]=dev.split('.'); let v=el.value;
    if(el.dataset.hds && el.type==='password' && !v) return true;
    const r=el.closest('.row'); r&&r.classList.add('busy');
    htdLoad({[ns]:{[k]:v}}).then(()=>render()); return true; }
  return false;
}

/* =================== ESP-Bus-Adapter (Aufbau wie Heizstab) =================== */
let BS=null, BS_MSG='', BS_BUSY=false, BSUB='start', BS_Q='', BS_CAT='', BLOG={cat:'all',pause:false,data:null,err:null}, BS_HOST='', BS_PR={p:'',s:'',e:''}, BS_PAIR={host:'',key:'',busy:false};
try{ BSUB=localStorage.getItem('emsehs_bsub')||'start'; }catch(e){}
// bis v1.18: Werte & Favoriten · Scan & Anlernen · Adapter · Verbindung · Info & Log
BSUB=({scan:'bus',adapter:'system',verbindung:'system',info:'log'})[BSUB]||BSUB;
const BLOG_CAT={all:'Alle',rx:'Empfangen',tx:'Gesendet',inf:'Rundsendung',err:'Fehler',scan:'Scan',conn:'Verbindung',http:'BSB-LAN'};
async function bsLoad(o,full){
  if(BS_BUSY && !o) return; BS_BUSY=true;
  try{ const r=await ws(Object.assign({type:'ems_esp_manager/panel/bsb',full:full!==false},o||{}));
    if(!r.params && BS && BS.params){ r.params=BS.params; r.entities=BS.entities; r.unknown=BS.unknown; }
    BS=r; BSBON=!!r.entry; if(o && Object.keys(o).length && !r.error) BS_MSG=BS_MSG||''; }
  catch(e){ BS_MSG='err:'+((e&&e.message)||e); }
  BS_BUSY=false; return BS;
}
const bsDo=async(o,okTxt)=>{ BS_MSG=''; await bsLoad(o); if(okTxt && !BS_MSG) BS_MSG='ok:'+okTxt; safeRender(); };
const bc=()=>(BS&&BS.config)||{};
const bsJob=()=>{ const j=BS&&BS.job; return j&&j.running?j:null; };
const bsAge=ts=>{ if(!ts) return ''; const s=Math.max(0,Math.round(Date.now()/1000-ts)); return s<90?s+' s':s<5400?Math.round(s/60)+' min':Math.round(s/3600)+' h'; };
const bsFav=l=>(bc().favorites||[]).map(String).includes(String(l));
function bsHints(){
  const b=BS, c=bc(), cp=b.caps||{}, i=b.ident||{}, out=[]; const add=(lvl,t,todo)=>out.push({lvl,t,todo});
  if(!c.kind) add('err','Kein ESP-Bus-Adapter gewählt.','Unter „Adapter“ suchen und übernehmen.');
  else if(cp.online===false) add('err','Der Adapter ist nicht erreichbar.','Strom und WLAN des Adapters prüfen; in Home Assistant unter ESPHome muss er „verbunden“ sein.');
  else if(c.kind==='esphome' && !cp.get) add('warn','Die Firmware des Adapters kann keine Parameter abfragen (Aktion „bsb_get“ fehlt).','BSB-Atom-Firmware 1.2 aufspielen – bis dahin nur die festen Werte des Adapters.');
  if(c.kind && !i.ident && i.family==null) add('info','Der Regler ist noch nicht erkannt.','Oben „Regler erkennen“.');
  if(c.kind && !b.scan_info) add('info','Noch kein Parameter-Scan.','Unter „Scan & Anlernen“ den Parameter-Scan starten (ca. 1 min).');
  if(c.poll_enabled && !(c.favorites||[]).length) add('warn','Bus-Abfrage ist an, aber es gibt keine Favoriten.','Unter „Werte & Favoriten“ Parameter mit ☆ markieren.');
  return out;
}
function bsHintsCard(){ const hs=bsHints(); if(!hs.length) return ''; const ico={err:'⛔',warn:'⚠️',info:'ℹ️'};
  return `<div class="card" style="margin-top:18px"><div class="cap">Hinweise</div>${hs.map(x=>`<div class="row" style="justify-content:flex-start;gap:12px"><span style="flex:none">${ico[x.lvl]}</span><span class="k">${esc(x.t)}${x.todo?`<small style="word-break:normal">→ ${esc(x.todo)}</small>`:''}</span></div>`).join('')}</div>`; }
function bsTop(){
  const b=BS, c=bc(), cp=b.caps||{}, i=b.ident||{}, si=b.scan_info, s=b.stats||{}, j=bsJob();
  const msgH=BS_MSG?`<div class="msg ${BS_MSG.startsWith('ok:')?'ok':'err'}" style="padding:10px 0 0">${esc(BS_MSG.slice(BS_MSG.indexOf(':')+1))}</div>`:'';
  const fav=(b.favorites||[]).filter(f=>f.valid!==false && f.text).slice(0,2);
  return `<div class="card pad"><div class="htop">
      <div class="hid"><div class="hico ${cp.online?'on':''}">${I.plug}</div><div><div class="t2">ESP-Bus-Adapter</div><div class="ts">${esc(b.title||'kein Adapter')}${i.ident?' · '+esc(i.ident):''}</div></div></div>
      <div class="hpills">${!c.kind?'<span class="pill no">kein Adapter</span>':cp.online?'<span class="pill">Adapter · verbunden</span>':'<span class="pill no">Adapter · offline</span>'}
        ${c.kind==='esphome'?(cp.get?'<span class="pill">Abfrage · bsb_get</span>':'<span class="pill dm">Firmware · ohne bsb_get</span>'):''}
        ${c.poll_enabled?'<span class="pill">Bus-Abfrage an</span>':'<span class="pill dm">Bus-Abfrage aus</span>'}
        ${c.log_enabled?'<span class="pill">Mitschnitt an</span>':'<span class="pill dm">Mitschnitt aus</span>'}</div>
      <div class="hbt"><button class="btn sm" data-act="bident" ${c.kind&&!j?'':'disabled'}>${I.re} Regler erkennen</button></div></div>
    <div class="tiles">
      ${tile('Regler',esc(i.ident||'–'),i.family!=null?'Familie '+i.family+' · Variante '+(i.variant??'–'):'nicht erkannt')}
      ${tile('Parameter',si?nf(si.found,0):'–',si?'Scan vor '+bsAge(si.t):'noch kein Scan')}
      ${fav[0]?tile(fav[0].name||'Favorit',esc(fav[0].text),'Zeile '+fav[0].line):tile('Favoriten',nf((c.favorites||[]).length,0),'als Sensoren in HA')}
      ${fav[1]?tile(fav[1].name||'Favorit',esc(fav[1].text),'Zeile '+fav[1].line):tile('Bus-Teilnehmer',nf((b.participants||[]).length,0),'')}
      ${j?tile(j.type==='scan'?'Scan läuft':j.type==='probe'?'Suchlauf läuft':'Erkennung',(j.total?Math.round(100*j.done/j.total):0)+' %',(j.found||0)+' gefunden','hot'):tile('Telegramme',nf((s.rx||0)+(s.tx||0),0),'empfangen '+(s.rx||0)+' · gesendet '+(s.tx||0))}
    </div>${msgH}</div>`;
}
/* --- Werte & Favoriten --- */
function bsCardFav(){
  const b=BS, c=bc(), fs=b.favorites||[];
  return `<div class="card"><div class="cap">Bus-Abfrage &amp; Favoriten</div>
    ${hRow('Bus-Abfrage',hTog('data-bct','poll_enabled',c.poll_enabled),c.poll_enabled?'fragt die Favoriten zyklisch ab':'aus – von Hand einschalten')}
    ${hRow('Intervall',`<span class="num"><input type="number" class="q" style="width:90px;height:36px" data-bcn="poll_interval" value="${esc(c.poll_interval||60)}" min="10" max="900" step="10"> s</span>`)}
    ${fs.length?fs.map(f=>`<div class="row"><span class="k">★ ${esc(f.line)} ${esc(f.name||'')}<small>${f.ts?'vor '+bsAge(f.ts):'noch nicht abgefragt'}</small></span><span class="v">${esc(f.text??'–')} <button class="btn sm" data-bfav="${esc(f.line)}" data-on="0" title="Favorit entfernen">✕</button></span></div>`).join('')
      :'<div class="note">Noch keine Favoriten – rechts in der Parameterliste mit ☆ markieren.</div>'}
    <div class="btns"><button class="btn" data-act="bpoll" ${c.kind&&fs.length?'':'disabled'}>${I.re} Jetzt abfragen</button></div>
    <div class="note">Favoriten erscheinen als Sensoren im Gerät „ESP-Bus-Adapter“ (Einstellungen → Geräte &amp; Dienste → EMS-ESP Manager).</div></div>`;
}
function bsCardParams(){
  const ps=(BS.params||[]); const cats=[...new Set(ps.map(p=>p.cat||'Sonstige'))];
  const q=BS_Q.toLowerCase();
  const list=ps.filter(p=>(!BS_CAT||(p.cat||'Sonstige')===BS_CAT) && (!q||(String(p.line)+' '+(p.name||'')+' '+(p.text||'')).toLowerCase().includes(q)));
  return `<div class="card"><div class="cap">Parameter (${ps.length})</div>
    <div class="tools" style="margin:6px 0 10px"><input class="q" id="bsq" placeholder="Suchen …" value="${esc(BS_Q)}" style="height:36px;min-width:160px">
      <select data-bcat="1" style="height:36px"><option value="">Alle Bereiche</option>${cats.map(k=>`<option ${k===BS_CAT?'selected':''}>${esc(k)}</option>`).join('')}</select></div>
    ${list.length?`<div style="max-height:560px;overflow:auto">${list.map(p=>`<div class="row${p.valid===false?' dim':''}"><span class="k">${esc(p.line)} ${esc(p.name||'')}<small>${esc(p.cat||'')}${p.cmd?' · '+esc(p.cmd):''}${p.user?' · angelernt':''}</small></span><span class="v">${esc(p.text??'–')}
        <button class="btn sm" data-bq="${esc(p.line)}" title="neu abfragen">${I.re}</button>
        <button class="btn sm" data-bfav="${esc(p.line)}" data-on="${bsFav(p.line)?0:1}" title="Favorit">${bsFav(p.line)?'★':'☆'}</button></span></div>`).join('')}</div>`
      :`<div class="empty">${ps.length?'Keine Treffer.':'Noch keine Werte – unter „Scan & Anlernen“ den Parameter-Scan starten. Mitgelesene Werte vom Bus erscheinen hier auch.'}</div>`}</div>`;
}
/* --- Scan & Anlernen --- */
function bsCardScan(){
  const b=BS, c=bc(), j=b.job, run=j&&j.running, si=b.scan_info, pct=j&&j.total?Math.round(100*j.done/j.total):0;
  const log=(j&&j.log)||[];
  return `<div class="card"><div class="cap">Parameter-Scan</div>
    ${si?row('Letzter Scan','vor '+bsAge(si.t),si.found+' Werte · '+(si.errors||0)+' nicht unterstützt · '+(si.duration||0)+' s'):''}
    ${j?row(j.type==='scan'?'Scan':j.type==='probe'?'ID-Suchlauf':'Erkennung',run?'läuft – '+pct+' %':'beendet',(j.done||0)+' / '+(j.total||0)+(j.found!=null?' · '+j.found+' gefunden':'')+(j.error?' · Fehler: '+j.error:'')):''}
    ${run&&j.total?`<div style="height:8px;border-radius:4px;background:var(--line);margin:8px 0"><div style="height:8px;border-radius:4px;width:${pct}%;background:var(--acc,#2a8fd6)"></div></div>`:''}
    <div class="btns">${run?`<button class="btn" data-act="bcancel">Abbrechen</button>`:`<button class="btn pri" data-act="bscan" ${c.kind?'':'disabled'}>Parameter-Scan starten</button>${c.kind==='bsblan'?'<button class="btn" data-act="bscanf">Alle Kategorien</button>':''}`}</div>
    ${log.length?`<div class="logs" style="max-height:260px;overflow:auto;margin-top:10px">${log.slice().reverse().map(l=>`<div class="lr"><span class="lm">${esc(l)}</span></div>`).join('')}</div>`:''}
    <div class="note">${c.kind==='bsblan'?'Liest die Kategorien aus BSB-LAN.':'Fragt alle Werte der eigenen Tabelle ab (ca. 90, mit Ausweich-IDs je Gerätefamilie) – nur lesen, ca. 1 Minute.'}</div></div>`;
}
function bsCardProbe(){
  const b=BS, c=bc(), ps=b.probe_presets||[], run=bsJob();
  if(c.kind!=='esphome') return '';
  return `<div class="card"><div class="cap">ID-Suchlauf</div>
    ${ps.map(x=>{ const n=parseInt(x[3],16)-parseInt(x[2],16)+1; return hRow(x[0],`<button class="btn sm" data-bprobe="${esc(x.join('|'))}" ${run?'disabled':''}>Starten</button>`,x[1]+x[2]+' … '+x[1]+x[3]+' · '+n+' IDs · ca. '+Math.round(n*0.9/60)+' min'); }).join('')}
    ${hRow('Eigener Bereich (hex)',`<span style="display:flex;gap:6px">${hIn('bpp',BS_PR.p,70,'placeholder="2D3D" maxlength="4"')}${hIn('bps',BS_PR.s,70,'placeholder="0500" maxlength="4"')}${hIn('bpe',BS_PR.e,70,'placeholder="06FF" maxlength="4"')}<button class="btn sm" data-act="bprobec" ${run?'disabled':''}>Starten</button></span>`,'oberer Teil · von · bis')}
    <div class="note">Fragt jede Befehls-ID im Bereich einmal ab. Was der Regler beantwortet und noch nicht in der Tabelle steht, landet unten bei „Unbekannte Werte“. Nur lesen.</div></div>`;
}
function bsCardUnknown(){
  const us=BS.unknown||[], cods=(BS.codecs||[]).map(x=>x[0]);
  return `<div class="card"><div class="cap">Unbekannte Werte (${us.length})</div>
    ${us.length?us.slice(0,80).map(u=>{ const g=u.guess||[], first=(g[0]||{}).codec||'u16'; const opts=[...g.map(x=>[x.codec,x.codec+' → '+x.text]),...cods.filter(k=>!g.some(x=>x.codec===k)).map(k=>[k,k])];
      return `<div class="row"><span class="k">${u.found?'🔎 ':''}${esc(u.cmd)}<small>${esc(u.type||'')} · ${u.count}× · ${esc(u.raw||'–')}</small></span><span class="v" style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
        ${hIn('bln_'+u.cmd,'',170,'placeholder="Name"')}${hSel('data-bcod',u.cmd,first,opts)}<button class="btn sm" data-blearn="${esc(u.cmd)}">Anlernen</button></span></div>`; }).join('')
      :'<div class="empty">Keine unbekannten Werte. „Bus-Mitschnitt“ einschalten und am Bedienteil Menüs öffnen oder einen ID-Suchlauf starten.</div>'}
    ${us.length?`<div class="btns"><button class="btn" data-act="bclrunk">Liste leeren</button></div>`:''}</div>`;
}
/* --- Adapter --- */
function bsCardAdapter(){
  const b=BS, c=bc(), f=b.find, run=f&&f.running, list=(f&&f.found)||[];
  const sel=x=>(x.kind==='esphome'&&c.device_id===x.device_id)||(x.kind==='bsblan'&&c.host===x.host);
  return `<div class="card"><div class="cap">Adapter</div>
    ${row('Gewählt',esc(b.title||'–'),c.kind==='esphome'?'ESPHome-Adapter (BSB-Atom)':c.kind==='bsblan'?'BSB-LAN':'')}
    ${bsHaEntryRow()}
    ${list.map(x=>hRow((x.kind==='esphome'?'ESPHome: ':x.kind==='esphome_new'?'ESPHome (nicht eingebunden): ':'BSB-LAN: ')+(x.title||x.host||''),
      x.pair?`<button class="btn sm pri" data-bpairhost="${esc(x.host||'')}">Koppeln</button>`:sel(x)?'<span class="pill">gewählt</span>':`<button class="btn sm" data-bsel="${esc(x.id)}" ${x.protected?'disabled':''}>Übernehmen</button>`,
      [x.node,x.host,x.has_get?'bsb_get ✓':(x.kind==='esphome'?'ohne bsb_get':''),x.bsb_entities?x.bsb_entities+' BSB-Werte':'',x.online===false?'offline':'',x.reauth?'⚠ Schlüssel passt nicht – neu koppeln':'',x.source||''].filter(Boolean).join(' · '))).join('')}
    ${f?`<div class="note">${run?'⏳ Suche läuft …':'Suche beendet – '+list.length+' Adapter'}${f.error?' · Fehler: '+esc(f.error):''}</div>`:''}
    ${hRow('Hersteller (Dashboard)',`<select data-bbrand="1"><option value="" ${!c.brand?'selected':''}>Automatisch – ${esc(b.brand||'Brötje')}</option>${(b.brands||b.manufacturers||[]).map(m=>`<option ${c.brand===m?'selected':''}>${esc(m)}</option>`).join('')}</select>`,'Name in der Seitenleiste: '+esc(b.brand_label||'Brötje EHS'))}
    ${hRow('Adresse prüfen',`<span style="display:flex;gap:6px">${hIn('bshost',BS_HOST,200,'placeholder="192.168.0.65 oder 192.168.0.88/1234"')}<button class="btn sm" data-act="bhost">Prüfen</button></span>`,'BSB-LAN oder ESPHome-Adapter · BSB-LAN mit Passkey: IP/Passkey')}
    ${hRow('ESPHome-Adapter koppeln',`<span style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">${hIn('bpahost',BS_PAIR.host,150,'placeholder="192.168.0.65"')}${hIn('bpakey',BS_PAIR.key,250,'placeholder="API-Schlüssel (44 Zeichen)" autocomplete="off" spellcheck="false"')}<button class="btn sm pri" data-act="bpair" ${BS_PAIR.busy?'disabled':''}>${BS_PAIR.busy?'Kopple …':'Koppeln'}</button></span>`,'Schlüssel aus der Adapter-Weboberfläche: System → Home Assistant → Kopieren. Bindet den Adapter in Home Assistant ein bzw. aktualisiert den Schlüssel.')}
    <div class="btns"><button class="btn" data-act="bfind">${I.re} Neu suchen</button><button class="btn" data-act="bfindlan">Heimnetz durchsuchen</button></div>
    ${f&&f.log&&f.log.length?`<div class="logs" style="max-height:180px;overflow:auto;margin-top:10px">${f.log.slice(-12).reverse().map(l=>`<div class="lr"><span class="lm">${esc(l)}</span></div>`).join('')}</div>`:''}</div>`;
}
function bsHaEntryRow(){ const h=BS&&BS.ha_entry; if(!h) return '';
  if(!h.exists) return row('Home Assistant','<span class="bad">nicht eingebunden</span>','ESPHome-Eintrag fehlt – unten mit dem API-Schlüssel „Koppeln“');
  const st=h.reauth?'<span class="bad">Schlüssel passt nicht</span>':h.online?'<span class="ok">verbunden</span>':h.state==='loaded'?'getrennt':esc(h.state||'–');
  return row('Home Assistant',st,(h.host?esc(h.host)+' · ':'')+(h.reauth?'unten mit dem neuen Schlüssel „Koppeln“':h.has_key?'verschlüsselt':'ohne Schlüssel')); }
/* --- Firmware des Adapters (OTA über WLAN) --- */
let BS_FWUP=null;
const FW_PH={connect:'Verbinde mit dem Adapter …',prepare:'Adapter bereitet den Speicher vor …',upload:'Übertrage Firmware …',finish:'Adapter prüft die Firmware …',reboot:'Adapter startet neu …',done:'Fertig',error:'Fehler'};
const fwKB=n=>n>=1048576?(n/1048576).toLocaleString('de-DE',{maximumFractionDigits:2})+' MB':Math.round(n/1024)+' KB';
const fwBar=(pct,cls)=>`<div style="height:8px;border-radius:4px;background:var(--sep);overflow:hidden;margin:10px 0 4px"><div style="height:100%;width:${Math.max(2,Math.min(100,pct||0))}%;background:${cls==='err'?'var(--err)':'var(--acc)'};transition:width .4s"></div></div>`;
function fwChecks(f){ const i=f.info||{}, out=[];
  if(i.node_ok===true) out.push('✓ Gerätename „'+(f.node||'')+'“'); else if(i.node_ok===false) out.push('⚠ Gerätename „'+(f.node||'')+'“ nicht in der Datei');
  if(i.psk_runtime) out.push('✓ API-Schlüssel im Adapter gespeichert (ab 2.4) – der bisherige Schlüssel wird nach dem Update übertragen');
  else if(i.psk_ok===true) out.push('✓ API-Schlüssel'); else if(i.psk_ok===false) out.push('⚠ API-Schlüssel nicht gefunden');
  return out; }
function bsCardFw(){
  const b=BS, c=bc(); if(c.kind!=='esphome') return '';
  const fw=b.fw||{}, ins=fw.installed||{}, j=fw.job, run=!!(j&&j.running), files=fw.files||[], a=b.adapter||{};
  let jobH='';
  if(j){ const err=j.phase==='error', pct=j.phase==='upload'?j.pct:(j.phase==='connect'||j.phase==='prepare')?2:100;
    jobH=`<div class="row" style="display:block"><span class="k">${run?'⏳ ':err?'⛔ ':j.warn?'⚠️ ':'✅ '}${esc(FW_PH[j.phase]||j.phase)}${j.phase==='upload'?' '+j.pct+' %':''}<small>${esc(j.file||'')}${j.version?' · Version '+esc(j.version):''}${j.from?' · vorher '+esc(j.from):''}</small></span>
      ${run||err?fwBar(pct,err?'err':''):''}${j.msg?`<div class="note top0" style="${err?'color:var(--err)':''}">${esc(j.msg)}</div>`:''}</div>`; }
  if(BS_FWUP) jobH+=`<div class="row" style="display:block"><span class="k">⏳ Datei wird hochgeladen und geprüft … ${BS_FWUP.pct} %<small>${esc(BS_FWUP.name)}</small></span>${fwBar(BS_FWUP.pct)}</div>`;
  const fl=files.map(f=>{ const i=f.info||{}, ck=fwChecks(f), cur=ins.version&&i.version===ins.version;
    const sub=[i.version?'Version '+i.version+(i.version_from?' (aus Dateiname)':''):'Version unbekannt',fwKB(i.size||0),i.chip,i.esphome?'ESPHome '+i.esphome:'',i.build?'erstellt '+i.build:''].filter(Boolean).join(' · ')+(ck.length?' · '+ck.join(' · '):'');
    return hRow(f.filename,`<span style="display:flex;gap:6px">${cur?'<span class="pill">installiert</span>':''}<button class="btn sm ${cur?'':'pri'}" data-bfwi="${esc(f.id)}" ${run||BS_FWUP||!a.host?'disabled':''}>Installieren</button><button class="btn sm" data-bfwd="${esc(f.id)}" ${run?'disabled':''} title="Datei löschen">✕</button></span>`,sub); }).join('');
  return `<div class="card"><div class="cap">Firmware des Adapters</div>
    ${row('Installiert',esc(ins.version||ins.sw||'–'),[ins.project,ins.esphome?'ESPHome '+ins.esphome:''].filter(Boolean).join(' · '))}
    ${row('Update über WLAN an',esc((a.host||'–')+':'+(fw.port||3232)),'ESPHome-OTA, Adapter muss online sein')}
    ${jobH}
    ${fl||'<div class="empty">Noch keine Firmware-Datei hochgeladen.</div>'}
    ${hRow('OTA-Passwort',`<input class="q" type="password" style="width:170px;height:36px" data-bfwpw="1" value="" placeholder="${fw.has_password?'gespeichert ••••':'keins'}" autocomplete="new-password">`,'nur wenn in der YAML „ota: password“ steht')}
    ${hRow('OTA-Port',`<span class="num"><input type="number" class="q" style="width:100px;height:36px" data-bcn="ota_port" value="${esc(fw.port||3232)}" min="1" max="65535" step="1"></span>`,'Standard 3232')}
    <div class="btns"><button class="btn" data-act="bfwup" ${run||BS_FWUP?'disabled':''}>${I.dl} Firmware-Datei hochladen (.bin)</button></div>
    <div class="note">Datei aus dem ESPHome Builder: beim Gerät ⋮ → <b>Herunterladen</b> → <b>OTA format</b> (firmware.ota.bin). WLAN-Zugang, Gerätename und API-Schlüssel stecken in der Datei – deshalb nur Dateien verwenden, die für diesen Adapter erstellt wurden. Bricht die Übertragung ab, bleibt die bisherige Firmware aktiv. Während des Updates pausieren Bus-Abfrage und Scan.</div></div>`;
}
async function bsFwUpload(f){
  if(!/\.bin$/i.test(f.name)){ BS_MSG='err:Bitte eine .bin-Datei wählen (firmware.ota.bin).'; safeRender(); return; }
  if(f.size>4*1024*1024){ BS_MSG='err:Datei größer als 4 MB – keine OTA-Firmware.'; safeRender(); return; }
  const buf=new Uint8Array(await f.arrayBuffer()), fid=Date.now().toString(36)+Math.random().toString(36).slice(2,8), CH=384*1024;
  BS_FWUP={name:f.name,pct:0}; BS_MSG=''; safeRender(); let r=null;
  try{ for(let o=0;o<buf.length;o+=CH){ const part=buf.subarray(o,o+CH); let bin=''; for(let i=0;i<part.length;i+=0x8000) bin+=String.fromCharCode.apply(null,part.subarray(i,i+0x8000));
      r=await ws({type:'ems_esp_manager/panel/bsb_fw',op:'put',fid,filename:f.name,offset:o,total:buf.length,data:btoa(bin)});
      BS_FWUP.pct=Math.round(100*Math.min(buf.length,o+CH)/buf.length); safeRender(); }
    if(BS&&r) BS.fw=r.fw; const e=r&&r.put&&r.put.file, i=(e&&e.info)||{}, warn=e?fwChecks(e).filter(x=>x.startsWith('⚠')):[];
    BS_MSG=(warn.length?'err:':'ok:')+'Datei geprüft und gespeichert'+(i.version?' – Version '+i.version:'')+(warn.length?' · '+warn.join(' · ')+' – passt die Datei zu diesem Adapter?':''); }
  catch(e){ BS_MSG='err:Nicht übernommen: '+((e&&e.message)||e); }
  BS_FWUP=null; safeRender();
}
async function bsFw(o,okTxt){ BS_MSG=''; try{ const r=await ws(Object.assign({type:'ems_esp_manager/panel/bsb_fw'},o)); if(BS) BS.fw=r.fw; if(okTxt) BS_MSG='ok:'+okTxt; }catch(e){ BS_MSG='err:'+((e&&e.message)||e); } safeRender(); }
// Zahlen höchstens mit 2 Nachkommastellen (z. B. 8,546875 → 8,55), deutsches Komma
// Temperaturen (°C / K) immer mit genau 1 Nachkommastelle
const bsVal=(v,u)=>{ if(v==null) return '–'; const n=Number(v); if(v===''||isNaN(n)||!/^-?[\d.]+(e[-+]?\d+)?$/i.test(String(v))) return String(v);
  if(/^(°C|K)$/.test(u||'')) return n.toLocaleString('de-DE',{minimumFractionDigits:1,maximumFractionDigits:1});
  return n.toLocaleString('de-DE',{minimumFractionDigits:0,maximumFractionDigits:2}); };
function bsCardEnts(){
  const es=BS.entities||[]; if(bc().kind!=='esphome') return '';
  return `<div class="card"><div class="cap">Werte des Adapters (ESPHome)</div>
    ${es.length?es.map(e=>{ const v=bsVal(e.state,e.unit)+(e.unit?' '+e.unit:'');
      if(e.domain==='switch') return hRow(e.name,`<button class="tog ${e.state==='on'?'on':''}" data-sw="${esc(e.entity_id)}" aria-label="umschalten"></button>`);
      if(e.domain==='select') return hRow(e.name,`<select data-sel="${esc(e.entity_id)}">${(e.options||[]).map(o=>`<option ${o===e.state?'selected':''}>${esc(o)}</option>`).join('')}</select>`);
      if(e.domain==='number') return hRow(e.name,`<span class="num"><input type="number" class="q" style="width:100px;height:36px" data-num="${esc(e.entity_id)}" value="${esc(e.state)}" min="${esc(e.min??'')}" max="${esc(e.max??'')}" step="${esc(e.step??1)}"> ${esc(e.unit||'')}</span>`);
      if(e.domain==='button') return hRow(e.name,`<button class="btn sm" data-btn="${esc(e.entity_id)}">Auslösen</button>`);
      return row(e.name,esc(v)); }).join(''):'<div class="empty">Lade …</div>'}
    <div class="note">Diese Werte fragt die Firmware fest ab; Ändern geht hier direkt über Home Assistant.</div></div>`;
}
/* --- Info & Log --- */
function bsCardInfo(){
  const b=BS, i=b.ident||{}, cp=b.caps||{}, s=b.stats||{};
  return `<div class="card"><div class="cap">Info</div>
    ${row('Regler',esc(i.ident||'–'))}${row('Baureihe',esc(i.series||'–'))}${row('Gerätefamilie / Variante',esc((i.family??'–')+' / '+(i.variant??'–')))}
    ${i.known_device?row('Geprüftes Gerät',esc(i.known_device)):''}${row('Werte in der Tabelle',nf(i.lines||0,0),(b.learned_count||0)+' angelernt')}
    ${row('Adapter',esc(b.title||'–'))}${row('Log-Abo',cp.log_stream?'aktiv':'aus')}${row('Telegramme',nf((s.rx||0)+(s.tx||0),0),'Rundsendungen '+(s.inf||0)+' · Fehler '+(s.err||0)+' · unbekannte IDs '+(s.unknown||0))}
    <div class="cap" style="margin-top:14px">Bus-Teilnehmer</div>
    ${(b.participants||[]).length?b.participants.map(p=>row('0x'+(+p.addr).toString(16).toUpperCase().padStart(2,'0')+' '+p.name,nf(p.count,0)+' Telegramme','vor '+p.age+' s · '+(p.cmds||[]).join(', '))).join(''):'<div class="note">Noch keine – „Bus-Mitschnitt“ einschalten.</div>'}
    <div class="cap" style="margin-top:14px">Hersteller mit Siemens-BSB/LPB-Reglern</div><div class="note">${esc((b.manufacturers||[]).join(' · '))}</div></div>`;
}
function bLogRows(){
  if(BLOG.err) return `<div class="note top0">${esc(BLOG.err)}</div>`;
  if(!BLOG.data) return '<div class="empty">Lade …</div>';
  const es=(BLOG.data.entries||[]).slice().reverse();
  if(!es.length) return `<div class="empty">${bc().log_enabled?'Noch keine Einträge.':'Mitschnitt ist aus. Bei Bedarf oben einschalten.'}</div>`;
  return `<div class="logs">${es.slice(0,800).map(e=>`<div class="lr ${esc(e.level)}"><span class="lt">${new Date(e.ts*1000).toLocaleTimeString('de-DE')}</span><span class="ll">${esc(BLOG_CAT[e.cat]||e.cat)}</span><span class="lm">${esc(e.msg)}${e.detail?`<span class="ln">${esc(e.detail)}</span>`:''}</span></div>`).join('')}</div>`;
}
function bsCardLog(){
  const on=!!bc().log_enabled, n=((BLOG.data&&BLOG.data.entries)||[]).length;
  return `<div class="card" id="blogcard"><div class="cap">BSB-Log</div>
    ${hRow('Bus-Mitschnitt',hTog('data-bct','log_enabled',on),on?`läuft – ${n} Einträge (max. 3000, bis zum Neustart)`:'aus – nur bei Bedarf einschalten')}
    <div class="tools" style="margin:12px 0">${Object.keys(BLOG_CAT).map(k=>`<button class="chip ${BLOG.cat===k?'on':''}" data-bcatlog="${k}">${BLOG_CAT[k]}</button>`).join('')}<span class="sp"></span>
      <button class="btn sm" data-act="bpause" ${on?'':'disabled'}>${BLOG.pause?'▶ Weiter':'❚❚ Pause'}</button>
      <button class="btn sm" data-act="bdl" ${n?'':'disabled'}>${I.dl} Herunterladen</button>
      <button class="btn sm" data-act="bcopy" ${n?'':'disabled'}>${I.copy} Kopieren</button><button class="btn sm" data-act="bclear" ${n?'':'disabled'}>Leeren</button></div>
    <div id="blogbody" style="max-height:520px;overflow:auto;border-top:1px solid var(--sep);padding-top:6px">${bLogRows()}</div>
    <div class="note">Zeichnet jedes Telegramm auf dem Bus auf (Abfragen, Antworten, Rundsendungen, Fehler) – dekodiert mit Zeilennummer, Name und Wert; dazu Scan- und Verbindungsmeldungen.</div></div>`;
}
let BLSEQ=0;
async function loadBLog(action){ const my=++BLSEQ;
  try{ const r=await ws(Object.assign({type:'ems_esp_manager/panel/bsb_log',cat:BLOG.cat,limit:3000},action?{action}:{})); if(my!==BLSEQ) return; BLOG.data=r; BLOG.err=null; }
  catch(e){ if(my!==BLSEQ) return; BLOG.err=e&&e.code==='unauthorized'?'Log nur für Administratoren.':(e&&e.message)||String(e); } }
function bLogRefresh(){ const b=$('#blogbody'); if(b){ const top=b.scrollTop; b.innerHTML=bLogRows(); b.scrollTop=top; } }
function bLogText(){ const b=BS||{}, i=b.ident||{};
  return [`BSB-Log – EMS-ESP Manager ${(ST&&ST.version)||''}`,`Exportiert: ${new Date().toLocaleString('de-DE')}`,`Adapter: ${b.title||'–'} · Regler: ${i.ident||'–'} (Familie ${i.family??'–'}/${i.variant??'–'}) · Filter: ${BLOG_CAT[BLOG.cat]}`,'']
    .concat(((BLOG.data&&BLOG.data.entries)||[]).map(e=>`${new Date(e.ts*1000).toLocaleString('de-DE')}\t${e.level}\t${BLOG_CAT[e.cat]||e.cat}\t${e.msg}${e.detail?'\t'+e.detail:''}`)).join('\r\n'); }
/* --- Verbindung (Aufbau wie EHS Sentinel) --- */
const ST_DE={loaded:'Geladen',setup_error:'Einrichtungsfehler',setup_retry:'Einrichtung wird wiederholt',not_loaded:'Nicht geladen',failed_unload:'Entladen fehlgeschlagen',setup_in_progress:'Wird eingerichtet','BSB-LAN':'BSB-LAN (JSON-API)'};
function bsConnTop(){
  const b=BS, c=bc(), a=b.adapter||{}, cp=b.caps||{}, s=b.stats||{};
  const yn=(l,v)=>v?`<span class="pill">${l} ein</span>`:`<span class="pill dm">${l} aus</span>`;
  return `<div class="card pad"><div class="htop">
      <div class="hid"><div class="hico ${a.available?'ok':''}">${I.plug}</div><div><div class="t2">${c.kind==='bsblan'?'BSB-LAN':'ESP-Bus-Adapter'}</div><div class="ts">${esc((a.host||'–')+(a.port?':'+a.port:''))}${a.sw?' · Version '+esc(a.sw):''}</div></div></div>
      <div class="hpills">${!c.kind?'<span class="pill dm">nicht eingerichtet</span>':a.available?'<span class="pill">Verbunden</span>':'<span class="pill no">Getrennt</span>'}${c.kind?yn('Bus-Abfrage',c.poll_enabled)+yn('Mitschnitt',c.log_enabled):''}</div>
      <div class="hbt">${c.kind?`<button class="btn sm" data-act="brec">${I.re} Neu verbinden</button>`:''}</div></div>
    <div class="tiles">
      ${tile('Telegramme empfangen',nf(s.rx||0),'')}
      ${tile('Gesendet',nf(s.tx||0),'eigene Abfragen')}
      ${tile('Rundsendungen',nf(s.inf||0),'INF vom Regler')}
      ${tile('Fehler',nf(s.err||0),'NACK / ERR')}
      ${tile('Unbekannte IDs',nf(s.unknown||0),'zum Anlernen')}
    </div></div>`;
}
function bsConnCard(){
  const b=BS, c=bc(), a=b.adapter||{}, cp=b.caps||{}; const yn=v=>v==null?'–':v?'Ein':'Aus';
  if(!c.kind) return '<div class="card pad"><div class="note top0">Kein ESP-Bus-Adapter gewählt – unter „ESP-Bus-Adapter → Adapter“ suchen und übernehmen.</div></div>';
  return `<div class="card"><div class="cap">${esc(a.name||b.title||'Adapter')}</div>
    ${row(c.kind==='bsblan'?'Adresse (BSB-LAN)':'Adresse (ESPHome-Adapter)',esc((a.host||'–')+(a.port?':'+a.port:'')))}
    ${row('Integrationsstatus',esc(ST_DE[a.state]||a.state||'–'))}
    ${row('Verbindung',a.available?'verbunden':'getrennt')}
    ${row('Firmware',esc(a.sw||'–'),a.model||'')}
    ${c.kind==='esphome'?row('Abfrage (bsb_get)',yn(cp.get),cp.get?'Scan und Bus-Abfrage möglich':'BSB-Atom-Firmware 1.2 aufspielen'):''}
    ${hRow('Bus-Abfrage',hTog('data-bct','poll_enabled',c.poll_enabled),'Favoriten alle '+(c.poll_interval||60)+' s')}
    ${hRow('Bus-Mitschnitt',hTog('data-bct','log_enabled',c.log_enabled),'nötig für das BSB-Log')}
    ${c.kind==='esphome'?row('Log-Abo',cp.log_stream?'aktiv':'aus','ESPHome-Log des Adapters'):''}
    <div class="btns b3"><button class="btn" data-act="brec">${I.re} Neu verbinden</button><a class="btn" href="${esc(a.ha_url||'/config/integrations/integration/esphome')}" target="_top">${I.open} ${c.kind==='bsblan'?'BSB-LAN':'ESPHome'}</a>
      ${a.web?`<a class="btn" href="${esc(a.web)}" target="_blank" rel="noopener">${I.open} Web-Oberfläche</a>`:`<button class="btn" disabled>${I.open} Web-Oberfläche</button>`}</div>
    <div class="note">„Neu verbinden“ lädt das ESPHome-Gerät in Home Assistant neu. Bus-Abfrage und Mitschnitt schalten bei Bedarf auch Scan und Anlernen ein. Die Web-Oberfläche des Adapters gibt es nur mit „web_server“ in der Firmware bzw. bei BSB-LAN und nur im Heimnetz.</div></div>`;
}
function bsConnTiles(){ return bsCardLog(); }
function vBsbConnLog(){
  if(!BS) return '<div class="empty">Lade …</div>';
  return bsConnTop()+bsHintsCard()+`<div style="height:18px"></div><div class="grid"><div class="col">${bsConnCard()}</div><div class="col">${bsCardLog()}</div></div>`;
}
function vBsbOverview(){
  if(!BS) return '<div class="empty">Lade …</div>';
  const b=BS, i=b.ident||{}, a=b.adapter||{};
  const sys=`<div class="card"><div class="cap">Brötje / Siemens-BSB</div>
    ${row('Regler',esc(i.ident||'–'),i.series||'')}${row('Gerätefamilie / Variante',esc((i.family??'–')+' / '+(i.variant??'–')))}
    ${i.known_device?row('Geprüftes Gerät',esc(i.known_device)):''}${row('Adapter',esc(b.title||'–'),a.sw?'Firmware '+a.sw:'')}
    ${row('Werte in der Tabelle',nf(i.lines||0,0),(b.learned_count||0)+' angelernt')}${row('Manager-Version',esc((b.version)||'')||'–')}
    <div class="btns"><button class="btn pri" data-act="open">${I.open} BSB-Panel öffnen</button><a class="btn" href="${esc(a.ha_url||'/config/integrations/integration/ems_esp_manager')}" target="_top">${I.open} Gerät in Home Assistant</a></div>
    <div class="note">Einstellungen und Messwerte zeigen die festen Werte der Adapter-Firmware; alle weiteren Parameter, Scan, Anlernen und Log unter „ESP-Bus-Adapter“.</div></div>`;
  return bsTop()+bsHintsCard()+`<div style="height:18px"></div>`+logsHint()+`<div class="grid"><div class="col">${sys}</div><div class="col">${bsCardFav()}</div></div>`;
}
/* ===== Reiter „<Hersteller> EHS“ – Aufbau wie die Weboberfläche des ESP-Bus-Adapters (v1.19) =====
   Übersicht · Werte · System · Bus · Logs – Kacheln, Info-Symbole bei den wichtigsten Funktionen. */
const BI={
  clock:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  globe:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/></svg>',
  home:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 11l8-7 8 7v9a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1z"/></svg>',
  list:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/></svg>',
  bus:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l2-6 4 12 2-6h6"/></svg>',
  log:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M7 3h8l4 4v14H7z"/><path d="M15 3v4h4M10 12h6M10 16h6"/></svg>',
  gear:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/></svg>',
  inf:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>',
  cv:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
  scan:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></svg>',
  learn:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M3 9l9-5 9 5-9 5z"/><path d="M7 11v5c3 2 7 2 10 0v-5"/></svg>',
  poll:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/></svg>',
  dev:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><rect x="3" y="4" width="7" height="7" rx="1"/><rect x="14" y="4" width="7" height="7" rx="1"/><rect x="8.5" y="14" width="7" height="7" rx="1"/></svg>',
  chip:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>',
  link:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
  up:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M12 20V9M7 14l5-5 5 5M5 4h14"/></svg>',
  sl:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/></svg>',
  tel:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/></svg>',
  wp:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="12" r="3.5"/><path d="M9 8.5v7M5.5 12h7M16 9h2M16 12h2M16 15h2"/></svg>',
  book:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 21V5M19 19v2H6"/></svg>',
};
const BINFO={
  quali:['Bus-Qualität','<p>Anteil der Abfragen des Adapters, die der Regler beantwortet hat.</p><ul><li><b>über 95 %</b> – alles in Ordnung</li><li><b>unter 90 %</b> – Regleradresse und Kennungen prüfen</li></ul><p>Welche Kennungen ohne Antwort bleiben, steht unter <b>Logs</b> → Filter „Fehler“.</p>'],
  ba:['Betriebsart Heizkreis','<ul><li><b>Automatik</b> – folgt dem Zeitprogramm des Reglers (Komfort/Reduziert)</li><li><b>Komfort</b> / <b>Reduziert</b> – dauerhaft der jeweilige Sollwert</li><li><b>Schutzbetrieb</b> – nur Frostschutz</li></ul><p>Die Änderung geht sofort über den Adapter an den Regler.</p>'],
  kom:['Komfortsollwert','<p>Gewünschte <b>Raumtemperatur in den Heizzeiten</b>. Daraus berechnet der Regler mit der Heizkennlinie die Vorlauftemperatur.</p><p>Wird es überall zu kalt oder zu warm, zuerst diesen Wert in kleinen Schritten (0,5 °C) ändern.</p>'],
  red:['Reduziertsollwert','<p>Raumtemperatur <b>außerhalb der Heizzeiten</b> (Nacht/Absenkung). Bei Fußbodenheizung und Wärmepumpe nur wenig absenken (1–2 °C).</p>'],
  kl:['Heizkennlinie Steilheit','<p>Wie stark die Vorlauftemperatur steigt, wenn es draußen kälter wird.</p><ul><li>Fußbodenheizung: meist <b>0,3 – 0,8</b></li><li>Heizkörper: meist <b>1,0 – 1,5</b></li></ul><p>Nur in kleinen Schritten (0,1) ändern und einige Tage beobachten. Zu kalt nur bei Frost → Steilheit erhöhen; immer zu kalt → Komfortsollwert erhöhen.</p>'],
  twb:['Trinkwasserbetrieb','<p><b>Ein</b>: Der Speicher wird nach dem Zeitprogramm auf den Nennsollwert geladen. <b>Aus</b>: keine Trinkwasserladung (Frostschutz bleibt aktiv).</p>'],
  tws:['Trinkwasser Nennsollwert','<p>Speichertemperatur in den Freigabezeiten. Aus Hygienegründen sollte der Speicher regelmäßig <b>60 °C</b> erreichen (Legionellenfunktion im Regler).</p>'],
  fav:['Favoriten','<p>Mit ☆ markierte Parameter fragt der Manager zyklisch ab (<b>Bus-Abfrage</b>) und legt sie als <b>Sensoren</b> im Gerät „ESP-Bus-Adapter“ an – damit stehen sie in Home Assistant für Verlauf, Automationen und Dashboards bereit.</p>'],
  poll:['Bus-Abfrage','<p>Fragt die <b>Favoriten</b> im eingestellten Intervall beim Regler ab und aktualisiert ihre Sensoren in Home Assistant.</p><p>60 s reichen für Temperaturen und Zustände. Kürzere Intervalle belasten den Bus unnötig.</p>'],
  mit:['Bus-Mitschnitt','<p>Zeichnet jedes Telegramm auf dem BSB-Bus im Log auf – dekodiert mit Zeilennummer, Name und Wert:</p><ul><li><b>Empfangen</b> – Antworten des Reglers</li><li><b>Gesendet</b> – Abfragen des Adapters</li><li><b>Rundsendung</b> – Meldungen anderer Teilnehmer (Raumgeräte)</li></ul><p>Nur zur Fehlersuche einschalten (max. 3000 Einträge, bis zum Neustart). Scan und Anlernen schalten ihn bei Bedarf selbst ein.</p>'],
  scan:['Parameter-Scan','<p>Fragt alle Werte der eigenen Parametertabelle beim Regler ab (ca. 90, mit Ausweich-IDs je Gerätefamilie). Dauert etwa <b>1 Minute</b> und <b>liest nur</b> – am Regler wird nichts verändert.</p><p>Danach stehen alle gefundenen Werte unter <b>Werte → Parameter</b> und können als Favorit markiert werden.</p>'],
  probe:['ID-Suchlauf','<p>Fragt jede Befehls-ID eines Bereichs einmal ab. Was der Regler beantwortet und noch nicht in der Tabelle steht, landet bei <b>Unbekannte Werte</b> zum Anlernen.</p><p>Nur lesen. Je nach Bereich einige Minuten.</p>'],
  learn:['Unbekannte Werte anlernen','<p>IDs, die vom Bus mitgelesen oder im Suchlauf gefunden wurden, aber nicht in der Tabelle stehen.</p><p>Namen eingeben, Datenformat wählen (Vorschlag mit Wert steht dabei) und <b>Anlernen</b> – der Wert erscheint dann wie alle anderen unter „Parameter“.</p>'],
  pair:['ESPHome-Adapter koppeln','<p>Bindet den Adapter in Home Assistant ein bzw. trägt einen neuen Schlüssel ein.</p><p><b>Adresse</b> (IP oder bsb-heizung.local) und <b>API-Schlüssel</b> aus der Weboberfläche des Adapters eintragen: System → Home Assistant → Kopieren.</p>'],
  brand:['Hersteller (Dashboard)','<p>Bestimmt den Namen dieses Reiters und des Dashboards in der Seitenleiste – z. B. „Brötje EHS“, „Elco EHS“.</p><p>„Automatisch“ nimmt den Hersteller, den der Regler meldet.</p>'],
  fw:['Firmware des Adapters','<p>Datei im ESPHome Builder erzeugen: beim Gerät ⋮ → <b>Herunterladen</b> → <b>OTA format</b> (firmware.ota.bin), hier hochladen und <b>Installieren</b>.</p><p>Nur Dateien verwenden, die für diesen Adapter erstellt wurden. Bricht die Übertragung ab, bleibt die bisherige Firmware aktiv. Ab Firmware 2.4 wird der API-Schlüssel nach dem Update automatisch übertragen.</p>'],
  ident:['Regler erkennen','<p>Liest Gerätefamilie und Variante des Reglers. Danach wählt der Manager die passenden Kennungen (Ausweich-IDs) für Scan und Abfrage.</p>'],
};
const bib=k=>BINFO[k]?`<button class="ibx" data-binfo="${k}" aria-label="Info">${BI.inf}</button>`:'';
function bInfoDlg(k){ const x=BINFO[k]; if(!x) return; const d=document.createElement('div'); d.className='dlg';
  d.innerHTML=`<div class="card pad"><h3>${BI.inf}${esc(x[0])}</h3><div class="dtx">${x[1]}</div><div class="btns" style="grid-template-columns:1fr"><button class="btn pri">Verstanden</button></div></div>`;
  d.addEventListener('click',ev=>{ if(ev.target===d||ev.target.closest('.btn')) d.remove(); }); document.body.appendChild(d); }
const capI=(t,k)=>`<div class="cap capi">${t}${bib(k)}</div>`;
const rowI=(k,ctl,sub,info)=>`<div class="row"><span class="k"><span class="kt">${esc(k)}</span>${bib(info)}${sub?`<small>${esc(sub)}</small>`:''}</span>${ctl}</div>`;
// Karten der bisherigen Unterseiten ohne äußeren Rahmen (für aufklappbare Abschnitte)
const unwrap=h=>String(h||'').replace(/^\s*<div class="card"[^>]*>\s*<div class="cap"[^>]*>[^<]*(<span[^>]*>[^<]*<\/span>)?[^<]*<\/div>/,'').replace(/<\/div>\s*$/,'');
let BS_OPEN={}; try{ BS_OPEN=JSON.parse(localStorage.getItem('emsehs_bopen')||'{}')||{}; }catch(e){}
function bAcc(k,icon,title,sub,body,info){ const o=!!BS_OPEN[k]; if(!body) return '';
  return `<div class="acc ${o?'open':''}" id="bacc-${k}"><button class="ah" data-bacc="${k}"><span class="ai">${icon}</span><span class="at"><b>${esc(title)}</b><small>${sub}</small></span><span class="cv">${BI.cv}</span></button>
    <div class="ab">${o?(info?`<div class="ainfo">${bib(info)}</div>`:'')+body:''}</div></div>`; }
// Fest geöffnete Karte ohne Pfeil (z. B. „Version & Panel“ unter System & Panel)
function bAccFix(k,icon,title,sub,body){ if(!body) return '';
  return `<div class="acc open fix" id="bacc-${k}"><div class="ah"><span class="ai">${icon}</span><span class="at"><b>${esc(title)}</b><small>${sub}</small></span></div>
    <div class="ab">${body}</div></div>`; }
/* --- Werte des Adapters (ESPHome-Entitäten) --- */
const AE=()=>(BS&&BS.entities)||[];
function aId(key){ const re=new RegExp('(^|_)'+key+'$'); const e=AE().find(x=>re.test(x.entity_id.split('.')[1]||'')); return e?e.entity_id:null; }
function aS(key){ const id=aId(key); return id?S(id):null; }
function aNum(key){ const s=aS(key); if(!s) return null; const v=parseFloat(s.state); return isNaN(v)?null:v; }
const aTxt=key=>{ const s=aS(key); return s?fmtState(s):'–'; };
const aOn=key=>{ const s=aS(key); return s?s.state==='on':null; };
const aVal=key=>{ const id=aId(key); return id?`<span class="v" data-val="${esc(id)}">${fmtState(S(id))}</span>`:'<span class="v d">–</span>'; };
const aCtl=key=>{ const id=aId(key); return id?ctlHtml({id}):'<span class="v d">–</span>'; };
function aPrefix(){ const ns=AE().map(e=>String(e.name||'')); if(ns.length<2) return ''; let p=ns[0];
  for(const n of ns){ while(p && !n.startsWith(p)) p=p.slice(0,p.lastIndexOf(' ')>0?p.lastIndexOf(' '):0); }
  return p && p.endsWith(' ')?p:(p?p+' ':''); }
const aLbl=e=>{ const p=aPrefix(); const n=String(e.name||e.entity_id); return p&&n.startsWith(p)?n.slice(p.length):n; };
const B_GROUPS=[['Heizkreis 1',/HK1|Heizkreis 1|Q2/],['Heizkreis 2',/HK2|Heizkreis 2|Q6/],['Trinkwasser',/Trinkwasser|Q3/],['Wärmeerzeuger',/Kessel|Brenner|Außentemp/],
  ['Solar',/Solar|Kollektor/],['Regler',/Geräte|Raumtemperatur an Regler/],['Adapter & Verbindung',/Bus |BSB-Bus|WLAN|IP-Adresse|MAC|Laufzeit|Neustart|Status-LED|Verbindung|Firmware|ESPHome|Freier Speicher|MQTT|Zugangspunkt|API|Taste/]];
const B_KINFO={'Komfortsollwert':'kom','Reduziertsollwert':'red','Heizkennlinie Steilheit':'kl','Betriebsart':'ba','Trinkwasserbetrieb':'twb','Trinkwasser Nennsollwert':'tws','BSB-Bus-Mitschnitt':'mit'};
const bKInfo=n=>{ for(const k in B_KINFO) if(n.endsWith(k)) return B_KINFO[k]; return ''; };
let BS_WV='adapter'; try{ BS_WV=localStorage.getItem('emsehs_bwv')||'adapter'; }catch(e){}
/* --- Kopf: Statusleiste, Hinweise, Kacheln --- */
function bQuality(){ const tx=aNum('bus_telegramme_gesendet'), er=aNum('bus_fehler'); if(tx>0) return Math.max(0,Math.min(100,100-(er||0)*100/tx));
  const s=(BS&&BS.stats)||{}; return s.tx>0?Math.max(0,Math.min(100,100-(s.err||0)*100/s.tx)):null; }
function bPills(){ const b=BS, c=bc(), cp=b.caps||{}, h=b.ha_entry||{};
  return `<div class="pills">${!c.kind?'<span class="pill no">kein Adapter</span>':cp.online===false?'<span class="pill no">Adapter offline</span>':'<span class="pill">Adapter</span>'}
    ${c.kind==='esphome'&&h.exists!=null?(h.reauth?'<span class="pill no">Schlüssel passt nicht</span>':h.online?'<span class="pill">Home Assistant</span>':'<span class="pill dm">HA getrennt</span>'):''}
    ${c.kind==='esphome'?(cp.get?'':'<span class="pill dm">ohne bsb_get</span>'):''}
    ${c.poll_enabled?'<span class="pill">Bus-Abfrage '+(c.poll_interval||60)+' s</span>':''}${c.log_enabled?'<span class="pill dm">Mitschnitt an</span>':''}
    ${bsJob()?'<span class="pill dm">'+(bsJob().type==='scan'?'Scan läuft':bsJob().type==='probe'?'Suchlauf läuft':'Erkennung läuft')+'</span>':''}</div>`; }
function bHints(){
  const b=BS, c=bc(), cp=b.caps||{}, i=b.ident||{}, h=b.ha_entry||{}, out=[]; const add=(lvl,t,todo,btn)=>out.push({lvl,t,todo,btn});
  const go=(sub,acc,l)=>`<button class="btn sm" data-bgo="${sub}${acc?':'+acc:''}">${l||'Öffnen'}</button>`;
  if(!c.kind) add('err','Kein ESP-Bus-Adapter gewählt.','Adapter suchen und übernehmen oder mit API-Schlüssel koppeln.',go('system','setup','Einrichten'));
  else if(cp.online===false) add('err','Der Adapter ist nicht erreichbar.','Strom und WLAN des Adapters prüfen; in Home Assistant unter ESPHome muss er verbunden sein.',go('system','conn'));
  else if(h.reauth) add('err','Der API-Schlüssel passt nicht – Home Assistant ist getrennt.','Schlüssel aus der Adapter-Oberfläche kopieren und neu koppeln.',go('system','setup','Koppeln'));
  else if(c.kind==='esphome' && !cp.get) add('warn','Die Firmware kann keine Parameter abfragen (Aktion „bsb_get“ fehlt).','Aktuelle Adapter-Firmware aufspielen – bis dahin nur die festen Werte.',go('system','fw','Firmware'));
  if(c.kind && !i.ident && i.family==null) add('info','Der Regler ist noch nicht erkannt.','Erkennung dauert wenige Sekunden.',`<button class="btn sm" data-act="bident" ${bsJob()?'disabled':''}>Erkennen</button>`);
  else if(c.kind && !b.scan_info) add('info','Noch kein Parameter-Scan.','Liest alle bekannten Werte des Reglers (ca. 1 min, nur lesen).',go('bus','scan','Scan'));
  if(c.poll_enabled && !(c.favorites||[]).length) add('warn','Bus-Abfrage ist an, aber es gibt keine Favoriten.','Unter Werte → Parameter mit ☆ markieren.',go('werte',''));
  const q=bQuality(); if(q!=null && q<90) add('warn','Viele Abfragen bleiben ohne Antwort ('+nf(q,0)+' %).','Betroffene Kennungen im Log unter „Fehler“.',go('log',''));
  if(!out.length) return ''; const ico={err:'⛔',warn:'⚠️',info:'ℹ️'};
  return out.map(x=>`<div class="hint ${x.lvl}"><span class="hi">${ico[x.lvl]}</span><span class="ht">${esc(x.t)}<small>${esc(x.todo)}</small></span>${x.btn||''}</div>`).join(''); }
function bMsg(){ return BS_MSG?`<div class="hint ${BS_MSG.startsWith('ok:')?'okm':'err'}"><span class="hi">${BS_MSG.startsWith('ok:')?'✓':'⛔'}</span><span class="ht">${esc(BS_MSG.slice(BS_MSG.indexOf(':')+1))}</span></div>`:''; }
const btile=(l,v,s,cls,info,go)=>`<div class="tile ${cls||''}${go?' lnk':''}"${go?` data-bgo="${go}"`:''}><div class="tl">${esc(l)}${bib(info)}</div><div class="tv">${v}</div><div class="ts">${s||'&nbsp;'}</div></div>`;
function bTilesStart(){ const i=BS.ident||{}, j=bsJob(), q=bQuality(), st=String((aS('status_kessel')||{}).state||'');
  const t=(k,d)=>{ const v=aNum(k); return v==null?'–':nf(v,d==null?1:d)+' °C'; };
  return `<div class="tiles t6">
    ${btile('Außen',t('aussentemperatur'),'vom Regler')}
    ${btile('Vorlauf HK1',t('vorlauftemperatur_hk1'),aNum('vorlaufsollwert_hk1')!=null?'Soll '+t('vorlaufsollwert_hk1'):'')}
    ${btile('Trinkwasser',t('trinkwassertemperatur'),aNum('trinkwasser_nennsollwert')!=null?'Soll '+t('trinkwasser_nennsollwert',0):'')}
    ${btile('Kessel',esc(st&&st!=='unknown'&&st!=='unavailable'?st:'–'),aNum('brennermodulation')!=null?'Modulation '+nf(aNum('brennermodulation'),0)+' %':'',/Betrieb/.test(st)?'hot':'')}
    ${j?btile(j.type==='scan'?'Scan läuft':j.type==='probe'?'Suchlauf':'Erkennung',(j.total?Math.round(100*j.done/j.total):0)+' %',(j.found||0)+' gefunden','hot','','bus:scan')
      :btile('Regler',esc(i.ident||'–'),i.family!=null?'Familie '+i.family+' · Variante '+(i.variant??'–'):'nicht erkannt','','ident','system:info')}
    ${btile('Bus-Qualität',q==null?'–':nf(q,0)+' %',(()=>{ const s=BS.stats||{}; return nf((s.rx||0)+(s.tx||0),0)+' Telegramme'; })(),q==null?'':q<90?'bad':'good','quali','bus')}
  </div>`; }
/* --- Übersicht --- */
function bHk(n){ if(!aId('hk'+n+'_komfortsollwert') && !aId('vorlauftemperatur_hk'+n)) return '';
  const r=[]; const vl=aNum('vorlauftemperatur_hk'+n), vs=aNum('vorlaufsollwert_hk'+n);
  r.push(`<div class="row"><span class="k">Vorlauf<small>Ist / Soll</small></span><span class="v">${vl==null?'–':nf(vl,1)+' °C'}${vs!=null?`<small>Soll ${nf(vs,1)} °C</small>`:''}</span></div>`);
  if(aId('hk'+n+'_betriebsart')) r.push(rowI('Betriebsart',aCtl('hk'+n+'_betriebsart'),'','ba'));
  if(aId('hk'+n+'_komfortsollwert')) r.push(rowI('Komfortsollwert',aCtl('hk'+n+'_komfortsollwert'),'','kom'));
  if(aId('hk'+n+'_reduziertsollwert')) r.push(rowI('Reduziertsollwert',aCtl('hk'+n+'_reduziertsollwert'),'','red'));
  if(aId('hk'+n+'_heizkennlinie_steilheit')) r.push(rowI('Heizkennlinie',aCtl('hk'+n+'_heizkennlinie_steilheit'),'Steilheit','kl'));
  const s=[]; if(aId('status_heizkreis_'+n)) s.push(aTxt('status_heizkreis_'+n)); const p=aOn('heizkreispumpe_hk'+n+(n===1?'_q2':'_q6')); if(p!=null) s.push('Pumpe '+(p?'<span class="ok">läuft</span>':'aus'));
  if(s.length) r.push(`<div class="row"><span class="k">Status</span><span class="v">${s.join(' · ')}</span></div>`);
  return `<div class="card">${capI('Heizkreis '+n)}${r.join('')}</div>`; }
function bTww(){ if(!aId('trinkwassertemperatur') && !aId('trinkwasser_nennsollwert')) return '';
  const u=aNum('trinkwassertemperatur_unten'); const r=[`<div class="row"><span class="k">Speicher</span><span class="v">${aTxt('trinkwassertemperatur')}${u!=null?`<small>unten ${nf(u,1)} °C</small>`:''}</span></div>`];
  if(aId('trinkwasserbetrieb')) r.push(rowI('Trinkwasserbetrieb',aCtl('trinkwasserbetrieb'),'','twb'));
  if(aId('trinkwasser_nennsollwert')) r.push(rowI('Nennsollwert',aCtl('trinkwasser_nennsollwert'),'','tws'));
  const s=[]; if(aId('status_trinkwasser')) s.push(aTxt('status_trinkwasser')); const p=aOn('trinkwasserpumpe_q3'); if(p!=null) s.push('Ladepumpe '+(p?'<span class="ok">läuft</span>':'aus'));
  if(s.length) r.push(`<div class="row"><span class="k">Status</span><span class="v">${s.join(' · ')}</span></div>`);
  return `<div class="card">${capI('Trinkwasser')}${r.join('')}</div>`; }
function bWez(){ const r=[];
  if(aId('status_kessel')) r.push(`<div class="row"><span class="k">Status</span>${aVal('status_kessel')}</div>`);
  if(aId('brennermodulation')) r.push(`<div class="row"><span class="k">Brennermodulation</span>${aVal('brennermodulation')}</div>`);
  if(aId('aussentemperatur')) r.push(`<div class="row"><span class="k">Außentemperatur</span>${aVal('aussentemperatur')}</div>`);
  if(aId('kollektortemperatur')){ r.push(`<div class="row"><span class="k">Kollektor</span>${aVal('kollektortemperatur')}</div>`);
    if(aId('solarertrag_heute')) r.push(`<div class="row"><span class="k">Solarertrag heute</span>${aVal('solarertrag_heute')}</div>`); }
  return r.length?`<div class="card">${capI('Wärmeerzeuger'+(aId('kollektortemperatur')?' & Solar':''))}${r.join('')}</div>`:''; }
function bFavCompact(){ const fs=(BS.favorites||[]).filter(f=>f.valid!==false);
  return `<div class="card">${capI('Favoriten','fav')}${fs.length?fs.slice(0,12).map(f=>`<div class="row"><span class="k">★ ${esc(f.name||f.line)}<small>Zeile ${esc(f.line)}${f.ts?' · vor '+bsAge(f.ts):''}</small></span><span class="v">${esc(f.text??'–')}</span></div>`).join('')
    :'<div class="note top0">Noch keine Favoriten – unter <b>Werte → Parameter</b> mit ☆ markieren.</div>'}
    <div class="btns"><button class="btn" data-bgo="werte:fav">Alle Favoriten</button><button class="btn" data-act="bpoll" ${bc().kind&&fs.length?'':'disabled'}>${I.re} Jetzt abfragen</button></div></div>`; }
function bStart(){
  if(!AE().length && bc().kind!=='bsblan') return bTilesStart()+`<div class="card pad" style="margin-top:14px"><div class="note top0">Noch keine Werte des Adapters in Home Assistant. Unter <b>System → Adapter einrichten</b> den ESPHome-Adapter wählen oder koppeln.</div></div>`;
  const hk=[bHk(1),bHk(2)].filter(Boolean).join('');
  const left=hk||bTww()+bWez(), right=hk?bTww()+bWez()+bFavCompact():bFavCompact();
  return bTilesStart()+`<div class="grid" style="margin-top:14px"><div class="col">${left}</div><div class="col">${right}</div></div>`; }
/* --- Werte: Adapter · Parameter · Favoriten --- */
function bWerteAdapter(){ const q=BS_Q.toLowerCase(); const es=AE().filter(e=>e.domain!=='button'&&(!q||(aLbl(e)+' '+e.entity_id).toLowerCase().includes(q)));
  if(!es.length) return `<div class="card pad"><div class="empty">${AE().length?'Keine Treffer.':'Keine Werte des Adapters gefunden (nur beim ESPHome-Adapter).'}</div></div>`;
  const used=new Set(), cards=[];
  for(const [t,re] of B_GROUPS){ const l=es.filter(e=>!used.has(e.entity_id)&&re.test(aLbl(e))); l.forEach(e=>used.add(e.entity_id)); if(l.length) cards.push([t,l]); }
  const rest=es.filter(e=>!used.has(e.entity_id)); if(rest.length) cards.splice(cards.length-1,0,['Weitere',rest]);
  const ctl=e=>/^(number|select|switch)$/.test(e.domain);
  const html=cards.map(([t,l])=>`<div class="card"><div class="cap">${esc(t)} <span class="n">· ${l.length}</span></div>${l.map(e=>`<div class="row" data-row="${esc(e.entity_id)}"><span class="k"><span class="kt">${esc(aLbl(e))}</span>${bib(bKInfo(aLbl(e)))}${ctl(e)?'<small>Einstellung</small>':''}</span>${ctl(e)?ctlHtml({id:e.entity_id}):`<span class="v" data-val="${esc(e.entity_id)}">${fmtState(S(e.entity_id))}</span>`}</div>`).join('')}</div>`);
  const h=Math.ceil(html.length/2);
  return `<div class="grid"><div class="col">${html.slice(0,h).join('')}</div><div class="col">${html.slice(h).join('')}</div></div>
    <div class="note top0" style="padding-top:12px">Diese Werte fragt die Firmware des Adapters fest ab. Einstellungen gehen sofort über den Adapter an den Regler.</div>`; }
function bWerte(){
  const ps=(BS.params||[]).length, fs=(bc().favorites||[]).length;
  const sg=(k,l)=>`<button class="${BS_WV===k?'on':''}" data-bwv="${k}">${l}</button>`;
  const search=BS_WV!=='adapter'?'':`<input class="q bq" id="bsq" placeholder="${BS_WV==='adapter'?'Wert suchen …':'Zeile, Name oder Wert suchen …'}" value="${esc(BS_Q)}">`;
  const body=BS_WV==='param'?bsCardParams():BS_WV==='fav'?`<div class="grid"><div class="col">${bsCardFav().replace('<div class="cap">Bus-Abfrage &amp; Favoriten</div>',capI('Bus-Abfrage &amp; Favoriten','fav'))}</div><div class="col">${bFavCompact()}</div></div>`:bWerteAdapter();
  return `<div class="seg">${sg('adapter','Adapter'+(AE().length?' · '+AE().length:''))}${sg('param','Parameter'+(ps?' · '+ps:''))}${sg('fav','★ Favoriten'+(fs?' · '+fs:''))}</div>${search}<div style="margin-top:12px">${body}</div>`; }
/* --- Bus --- */
function bBus(){ const b=BS, c=bc(), s=b.stats||{}, si=b.scan_info, j=b.job, q=bQuality(), us=(b.unknown||[]).length;
  const tiles=`<div class="tiles t6">${btile('Empfangen',nf(s.rx||0,0),'Telegramme')}${btile('Gesendet',nf(s.tx||0,0),'eigene Abfragen')}${btile('Rundsendungen',nf(s.inf||0,0),'INF vom Regler')}
    ${btile('Fehler',nf(s.err||0,0),'NACK / ohne Antwort',s.err?'bad':'')}${btile('Unbekannte IDs',nf(s.unknown||0,0),'zum Anlernen')}${btile('Bus-Qualität',q==null?'–':nf(q,0)+' %','',q==null?'':q<90?'bad':'good','quali')}</div>`;
  const poll=`${rowI('Bus-Abfrage',hTog('data-bct','poll_enabled',c.poll_enabled),c.poll_enabled?'fragt die Favoriten zyklisch ab':'aus','poll')}
    ${hRow('Intervall',`<span class="num"><input type="number" class="q" style="width:90px;height:36px;min-width:0" data-bcn="poll_interval" value="${esc(c.poll_interval||60)}" min="10" max="900" step="10"></span><span class="u">s</span>`)}
    ${rowI('Bus-Mitschnitt',hTog('data-bct','log_enabled',c.log_enabled),c.log_enabled?'zeichnet alle Telegramme auf – siehe Logs':'aus – nur zur Fehlersuche','mit')}
    <div class="btns"><button class="btn" data-act="bpoll" ${c.kind&&(c.favorites||[]).length?'':'disabled'}>${I.re} Favoriten jetzt abfragen</button><button class="btn" data-bgo="log">${I.log} Log ansehen</button></div>`;
  const parts=(b.participants||[]).length?b.participants.map(p=>row('0x'+(+p.addr).toString(16).toUpperCase().padStart(2,'0')+' '+p.name,nf(p.count,0)+' Telegramme','vor '+p.age+' s · '+(p.cmds||[]).join(', '))).join(''):'<div class="note top0">Noch keine – Bus-Mitschnitt einschalten.</div>';
  const accs=[
    bAcc('poll',BI.poll,'Bus-Abfrage & Mitschnitt',(c.poll_enabled?'Abfrage alle '+(c.poll_interval||60)+' s':'Abfrage aus')+' · Mitschnitt '+(c.log_enabled?'an':'aus'),poll),
    bAcc('scan',BI.scan,'Parameter-Scan',bsJob()&&j.type==='scan'?'läuft – '+(j.total?Math.round(100*j.done/j.total):0)+' %':si?si.found+' Werte · vor '+bsAge(si.t):'noch kein Scan',unwrap(bsCardScan()),'scan'),
    c.kind==='esphome'?bAcc('probe',BI.dev,'ID-Suchlauf',bsJob()&&j.type==='probe'?'läuft – '+(j.total?Math.round(100*j.done/j.total):0)+' %':'weitere Werte des Reglers finden',unwrap(bsCardProbe()),'probe'):'',
    bAcc('learn',BI.learn,'Unbekannte Werte anlernen',us+' offen · '+(b.learned_count||0)+' angelernt',unwrap(bsCardUnknown()),'learn'),
    bAcc('parts',BI.bus,'Bus-Teilnehmer',((b.participants||[]).length)+' Geräte',parts)].filter(Boolean);
  const h=Math.ceil(accs.length/2);
  return tiles+`<div class="grid" style="margin-top:14px"><div class="col accs">${accs.slice(0,h).join('')}</div><div class="col accs">${accs.slice(h).join('')}</div></div>`; }
/* --- Logs --- */
function bLog(){
  return (bsCardLog().replace('<div class="cap">BSB-Log</div>',capI('BSB-Log','mit'))); }
/* --- System --- */
function bSystem(){ return sysNav()+(SYSV==='wpa'?vWpa():bSystemBody()); }
function bSystemBody(){ const b=BS, c=bc(), a=b.adapter||{}, cp=b.caps||{}, i=b.ident||{}, fw=b.fw||{}, ins=fw.installed||{}, h=b.ha_entry||{};
  const L=[
    bAcc('conn',BI.link,'Verbindung',!c.kind?'nicht eingerichtet':(a.available?'verbunden':'getrennt')+(a.host?' · '+esc(a.host):''),c.kind?unwrap(bsConnCard()):'<div class="note top0">Kein Adapter gewählt – unter „Adapter einrichten“ suchen oder koppeln.</div>'),
    bAcc('setup',BI.chip,'Adapter einrichten',esc(b.title||'kein Adapter')+(c.kind==='esphome'&&h.exists!=null?' · '+(h.reauth?'Schlüssel passt nicht':h.has_key?'verschlüsselt':'ohne Schlüssel'):''),unwrap(bsCardAdapter())
      .replace('<span class="k">Hersteller (Dashboard)','<span class="k">Hersteller (Dashboard)'+bib('brand')).replace('<span class="k">ESPHome-Adapter koppeln','<span class="k">ESPHome-Adapter koppeln'+bib('pair'))),
    c.kind==='esphome'?bAcc('fw',BI.up,'Firmware des Adapters','installiert '+esc(ins.version||ins.sw||a.sw||'–'),unwrap(bsCardFw()),'fw'):'',
    bAcc('info',BI.book,'Regler & Info',esc(i.ident||'Regler nicht erkannt')+(i.family!=null?' · Familie '+i.family:''),
      `<div class="btns" style="grid-template-columns:1fr;margin-top:10px"><button class="btn" data-act="bident" ${c.kind&&!bsJob()?'':'disabled'}>${I.re} Regler neu erkennen</button></div>`
      +unwrap(bsCardInfo()).replace(/<div class="cap" style="margin-top:14px">Bus-Teilnehmer<\/div>[\s\S]*?(?=<div class="cap" style="margin-top:14px">Hersteller)/,''),'ident')].filter(Boolean);
  return `<div class="grid"><div class="col accs">${L[0]}${L[1]}</div><div class="col accs">${L.slice(2).join('')}</div></div>`; }
const BSUBS=[['start','Übersicht',BI.home],['werte','Werte',BI.list],['system','System',BI.gear],['bus','Bus',BI.bus],['log','Logs',BI.log]];
function bsSubnav(){ return `<nav class="subnav top bnav">${BSUBS.map(([k,l,ic])=>`<button class="chip ${BSUB===k?'on':''}" data-bsub="${k}">${ic}<span>${l}</span></button>`).join('')}</nav>`; }
function vBsb(){
  if(!BS) return '<div class="empty">Lade …</div>';
  if(!BSUBS.some(x=>x[0]===BSUB)) BSUB='start';
  const body=BSUB==='werte'?bWerte():BSUB==='bus'?bBus():BSUB==='log'?bLog():BSUB==='system'?bSystem():bStart();
  return bsSubnav()+(BSUB==='start'?bPills()+bHints():'')+bMsg()+body;
}

async function bsbClick(b){
  const act=b.dataset.act;
  if(b.dataset.binfo){ bInfoDlg(b.dataset.binfo); return true; }
  if(b.dataset.bsub||b.dataset.bgo){ const [sub,acc]=(b.dataset.bsub||b.dataset.bgo).split(':'); BSUB=sub; try{localStorage.setItem('emsehs_bsub',BSUB);}catch(e){}
    if(acc==='fav'||acc==='param'){ BS_WV=acc; try{localStorage.setItem('emsehs_bwv',acc);}catch(e){} }
    else if(acc){ BS_OPEN[acc]=1; try{localStorage.setItem('emsehs_bopen',JSON.stringify(BS_OPEN));}catch(e){} }
    BS_MSG=''; render(); if(b.dataset.bgo) window.scrollTo(0,0); if(acc){ const x=$('#bacc-'+acc); x&&x.scrollIntoView({block:'start'}); }
    if(BSUB==='log') loadBLog().then(()=>{ if(TAB==='bsb') bLogRefresh(); }); return true; }
  if(b.dataset.bacc){ const k=b.dataset.bacc; if(BS_OPEN[k]) delete BS_OPEN[k]; else BS_OPEN[k]=1; try{localStorage.setItem('emsehs_bopen',JSON.stringify(BS_OPEN));}catch(e){} render(); return true; }
  if(b.dataset.bwv){ BS_WV=b.dataset.bwv; try{localStorage.setItem('emsehs_bwv',BS_WV);}catch(e){} render(); return true; }
  if(b.dataset.bct){ b.classList.toggle('on'); await bsDo({config:{[b.dataset.bct]:!bc()[b.dataset.bct]}},'Gespeichert'); if(b.dataset.bct==='log_enabled'){ await loadBLog(); safeRender(); } return true; }
  if(b.dataset.bfav){ await bsDo({favorite:{line:b.dataset.bfav,on:b.dataset.on==='1'}},b.dataset.on==='1'?'Als Favorit gespeichert – erscheint als Sensor':'Favorit entfernt'); return true; }
  if(b.dataset.bq){ b.disabled=true; await bsDo({lines:[b.dataset.bq]},'Neu abgefragt'); return true; }
  if(b.dataset.bsel){ b.disabled=true; await bsDo({select:b.dataset.bsel},'Adapter übernommen – Regler wird erkannt'); return true; }
  if(b.dataset.bprobe){ const x=b.dataset.bprobe.split('|'); if(!confirm('ID-Suchlauf „'+x[0]+'“ starten? Der Adapter fragt dabei nur Werte ab.')) return true; await bsDo({probe:{prefix:x[1],start:x[2],end:x[3],name:x[0]}},'Suchlauf gestartet'); return true; }
  if(b.dataset.blearn){ const cmd=b.dataset.blearn, n=(($('#bln_'+cmd)||{}).value||'').trim(), cod=(document.querySelector('[data-bcod="'+cmd+'"]')||{}).value;
    if(!n){ alert('Bitte einen Namen eingeben.'); return true; } await bsDo({learn:{cmd,name:n,codec:cod}},'Angelernt: '+n); return true; }
  if(b.dataset.bcatlog){ BLOG.cat=b.dataset.bcatlog; await loadBLog(); render(); return true; }
  if(act==='bfwup'){ $('#fwfile').click(); return true; }
  if(b.dataset.bfwi){ const f=((BS.fw||{}).files||[]).find(x=>x.id===b.dataset.bfwi); if(!f) return true; const i=f.info||{}, warn=fwChecks(f).filter(x=>x.startsWith('⚠'));
    if(!confirm('Firmware „'+f.filename+'“'+(i.version?' (Version '+i.version+')':'')+' auf den Adapter aufspielen?\n\nDer Adapter startet danach neu (ca. 1 Minute ohne Verbindung).'+(warn.length?'\n\nACHTUNG: '+warn.join(', ')+'.\nMit einer fremden Datei verliert der Adapter evtl. WLAN oder die Verbindung zu Home Assistant.':''))) return true;
    b.disabled=true; await bsFw({op:'install',fid:f.id},'Firmware-Update gestartet'); return true; }
  if(b.dataset.bfwd){ if(!confirm('Firmware-Datei löschen?')) return true; await bsFw({op:'delete',fid:b.dataset.bfwd},'Datei gelöscht'); return true; }
  if(act==='brec'){ b.disabled=true; await bsDo({action:'reconnect'},'Adapter neu verbunden'); return true; }
  if(act==='bident'){ await bsDo({action:'identify'},'Regler-Erkennung gestartet'); return true; }
  if(act==='bscan'){ await bsDo({action:'scan_quick'},'Scan gestartet'); return true; }
  if(act==='bscanf'){ await bsDo({action:'scan_full'},'Scan gestartet'); return true; }
  if(act==='bcancel'){ await bsDo({action:'cancel'},'Abbruch angefordert'); return true; }
  if(act==='bpoll'){ b.disabled=true; await bsDo({action:'poll'},'Favoriten abgefragt'); return true; }
  if(act==='bclrunk'){ if(!confirm('Liste der unbekannten Werte leeren?')) return true; await bsDo({action:'clear_unknown'},'Liste geleert'); return true; }
  if(act==='bprobec'){ const v=[$('#bpp').value,$('#bps').value,$('#bpe').value].map(x=>x.trim().toUpperCase()); BS_PR={p:v[0],s:v[1],e:v[2]};
    if(!v.every(x=>/^[0-9A-F]{4}$/.test(x))){ alert('Je 4 Hex-Zeichen eingeben, z. B. 2D3D · 0500 · 06FF.'); return true; }
    await bsDo({probe:{prefix:v[0],start:v[1],end:v[2],name:'Eigener Bereich'}},'Suchlauf gestartet'); return true; }
  if(act==='bfind'){ await bsDo({find:{lan:false}},'Suche gestartet'); return true; }
  if(act==='bfindlan'){ await bsDo({find:{lan:true}},'Heimnetz-Suche gestartet'); return true; }
  if(b.dataset.bpairhost!=null){ BS_PAIR.host=b.dataset.bpairhost||BS_PAIR.host; render(); const k=$('#bpakey'); if(k){ k.focus(); k.scrollIntoView({block:'center'}); } return true; }
  if(act==='bpair'){ const h=($('#bpahost').value||'').trim(), k=($('#bpakey').value||'').replace(/\s|"/g,''); BS_PAIR.host=h; BS_PAIR.key=k;
    if(!h){ alert('Bitte die Adresse des Adapters eingeben (IP-Adresse oder bsb-heizung.local).'); return true; }
    if(!/^[A-Za-z0-9+/]{43}=$/.test(k)){ alert('Der API-Schlüssel hat 44 Zeichen und endet mit „=“.\nIn der Weboberfläche des Adapters: System → Home Assistant → Kopieren.'); return true; }
    BS_PAIR.busy=true; BS_MSG=''; render(); const r=await bsLoad({pair:{host:h,key:k}}); BS_PAIR.busy=false;
    if(r&&r.pair){ BS_MSG='ok:'+r.pair.msg; BS_PAIR.key=''; } safeRender(); return true; }
  if(act==='bhost'){ const v=$('#bshost').value.trim(); BS_HOST=v; if(!v){ alert('Bitte die Adresse des BSB-LAN eingeben.'); return true; } await bsDo({find:{lan:false,host:v}},'Prüfe '+v+' …'); return true; }
  if(act==='bpause'){ BLOG.pause=!BLOG.pause; render(); return true; }
  if(act==='bclear'){ if(!confirm('BSB-Log leeren?')) return true; await loadBLog('clear'); render(); return true; }
  if(act==='bdl'){ const blob=new Blob(['﻿'+bLogText()],{type:'text/plain;charset=utf-8'}); const u=URL.createObjectURL(blob);
    const d=new Date(), p=n=>String(n).padStart(2,'0'); const a=document.createElement('a'); a.href=u;
    a.download=`bsb-log_${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}_${p(d.getHours())}-${p(d.getMinutes())}.txt`;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(u),5000); return true; }
  if(act==='bcopy'){ const txt=bLogText(); try{ await navigator.clipboard.writeText(txt); }catch(e){ const t=document.createElement('textarea'); t.value=txt; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); } b.textContent='Kopiert ✓'; return true; }
  return false;
}
function bsbChange(el){
  if(el.dataset.bfwpw){ const v=el.value; el.value=''; bsDo({config:{ota_password:v}},v?'OTA-Passwort gespeichert':'OTA-Passwort entfernt'); return true; }
  if(el.dataset.bcn){ const v=parseFloat(el.value.replace(',','.')); if(!isNaN(v)) bsDo({config:{[el.dataset.bcn]:v}},'Gespeichert'); return true; }
  if(el.dataset.bcat){ BS_CAT=el.value; render(); return true; }
  if(el.dataset.bbrand!=null){ bsDo({config:{brand:el.value}},'Dashboard heißt jetzt „'+((el.value||'').split(' (')[0]||'automatisch')+(el.value?' EHS':'')+'“').then(()=>head()); return true; }
  if(el.dataset.bcod) return true;
  return false;
}

/* =================== Verbindung & EHS-Log (Aufbau wie Heizstab) =================== */
let ESUB='verbindung';
try{ ESUB=localStorage.getItem('emsehs_esub')||'verbindung'; }catch(e){}
const ESUBS=[['verbindung','Verbindung'],['log','EHS-Log']];
const STAT_TXT={loaded:'Geladen',setup_error:'Einrichtungsfehler',setup_retry:'Einrichtung wird wiederholt',not_loaded:'Nicht geladen',failed_unload:'Entladen fehlgeschlagen',setup_in_progress:'Wird eingerichtet'};
function eTop(){
  const d=ST||{}, x=(d.instances||[])[0], s=(x&&x.stats)||{};
  const pill=!x?'<span class="pill dm">nicht eingerichtet</span>':!x.loaded?'<span class="pill dm">Nicht geladen</span>':x.connected?'<span class="pill">Verbunden</span>':'<span class="pill no">Getrennt</span>';
  const yn=(l,v)=>v==null?'':v?`<span class="pill">${l} ein</span>`:`<span class="pill dm">${l} aus</span>`;
  return `<div class="card pad"><div class="htop">
      <div class="hid"><div class="hico ${x&&x.connected?'ok':''}">${I.plug}</div><div><div class="t2">EHS Sentinel</div><div class="ts">${x?esc((x.ip||'–')+':'+(x.port||'–')):'RS485-Adapter'}${d.ehs_version?' · Version '+esc(d.ehs_version):''}</div></div></div>
      <div class="hpills">${pill}${x?yn('Schreibmodus',x.write_mode)+yn('Polling',x.polling):''}</div>
      <div class="hbt">${x?`<button class="btn sm" data-re="${esc(x.entry_id)}">${I.re} Neu verbinden</button>`:''}</div></div>
    <div class="tiles">
      ${tile('Pakete gelesen',nf(s.packets_read),'')}
      ${tile('Verarbeitet',nf(s.packets_processed),'')}
      ${tile('Angefordert',nf(s.packets_requested),'')}
      ${tile('Fremde Pakete',nf(s.packets_processed_not_indoor_outdoor),'nicht Innen/Außen')}
      ${tile('Warteschlange',x?nf(x.queue):'–','')}
    </div></div>`;
}
function eHints(){
  const d=ST||{}, xs=d.instances||[], out=[];
  const add=(lvl,t,todo)=>out.push({lvl,t,todo});
  if(d.err) add('err',d.err,'');
  else if(!xs.length) add('err','EHS Sentinel ist nicht eingerichtet.','HACS-Integration „EHS Sentinel“ mit RS485-Adapter installieren und einrichten.');
  xs.forEach(x=>{
    if(!x.loaded) add('err',`${x.title}: ${STAT_TXT[x.state]||x.state||'nicht geladen'}.`,'Unter Einstellungen → Geräte & Dienste → EHS Sentinel prüfen und neu laden.');
    else if(!x.connected) add('err',`Keine Verbindung zum RS485-Adapter ${x.ip||''}:${x.port||''}.`,'Adapter mit Strom und Netzwerk prüfen, dann „Neu verbinden“.');
    if(x.loaded && x.write_mode===false) add('info','Schreibmodus ist aus – Einstellungen der Wärmepumpe können nicht geändert werden.','In EHS Sentinel unter „Konfigurieren“ den Steuermodus einschalten.');
    if(x.queue>20) add('warn',`Warteschlange mit ${x.queue} Paketen – die Verbindung kommt nicht hinterher.`,'Adapter und WLAN-Empfang prüfen.');
  });
  if(!out.length) return '';
  const ico={err:'⛔',warn:'⚠️',info:'ℹ️'};
  return `<div class="card" style="margin-top:18px"><div class="cap">Hinweise</div>${out.map(x=>`<div class="row" style="justify-content:flex-start;gap:12px"><span style="flex:none">${ico[x.lvl]}</span><span class="k">${esc(x.t)}${x.todo?`<small style="word-break:normal">→ ${esc(x.todo)}</small>`:''}</span></div>`).join('')}</div>`;
}
function eConnBody(){
  const d=ST||{}, lvl=d.log_level||'default';
  const inst=(d.instances||[]).map(x=>{ const yn=v=>v==null?'–':v?'Ein':'Aus';
    return `<div class="card"><div class="cap">${esc(x.title)}</div>`
      +row('Adresse (RS485-Adapter)',esc((x.ip||'–')+':'+(x.port||'–')))+row('Integrationsstatus',esc(STAT_TXT[x.state]||x.state))
      +row('Schreibmodus (Steuermodus)',yn(x.write_mode))+row('Polling',yn(x.polling))+row('Erweitertes Logging',yn(x.extended_logging),'nötig für das Paket-Log')
      +`<div class="btns b3"><button class="btn" data-re="${esc(x.entry_id)}">${I.re} Neu verbinden</button><a class="btn" href="/config/integrations/integration/ehs_sentinel" target="_top">${I.open} EHS Sentinel</a>`
      +(x.ip?`<a class="btn" href="http://${esc(x.ip)}" target="_blank" rel="noopener">${I.open} Web-Oberfläche</a>`:`<button class="btn" disabled>${I.open} Web-Oberfläche</button>`)+`</div>`
      +`<div class="note">Schreibmodus, Polling und erweitertes Logging stellst du in EHS Sentinel unter „Konfigurieren“ ein. Die Web-Oberfläche des RS485-Adapters (z. B. Elfin EW11) öffnet sich in einem neuen Fenster und ist nur im Heimnetz erreichbar.</div></div>`;
  }).join('') || '<div class="card pad"><div class="note top0">EHS Sentinel ist nicht eingerichtet (HACS-Integration „EHS Sentinel“ mit RS485-Adapter).</div></div>';
  return `<div class="grid"><div class="col">${inst}</div><div class="col">${vLog()}</div></div>`;
}
function vConnLog(){
  if(!ST) return '<div class="empty">Lade …</div>';
  return eTop()+eHints()+`<div style="height:18px"></div>`+eConnBody();
}

/* =================== Bosch (v1.22.0) – Inhalt des bisherigen Dashboards „EMS-ESP“ im EMS-EHS-Design =================== */
let BD=null, BD_ERR='', BD_T=0, BV='system', BMD=[], BOSUBS=[];
try{ const v=localStorage.getItem('emsehs_bview'); if(v) BV=v; }catch(e){}
const B_ICON={system:'home',einstellungen:'sl',messwerte:'list',zuordnung:'dev',log:'log',mqtt:'link',uhrzeit:'clock',webui:'globe',auswertung:'wp'};
const B_SKIP_IFRAME='/api/ems_esp_manager/panel/admin';   // Panel-Version/Upload steht schon unter „System & Panel“
const HVAC={heat:'Heizen',cool:'Kühlen',auto:'Automatik',off:'Aus',heat_cool:'Heizen/Kühlen',dry:'Trocknen',fan_only:'Lüften'};
async function bLoad(force){
  if(BD && !force && Date.now()-BD_T<60000) return false;
  try{ BD=await ws({type:'ems_esp_manager/bosch/dashboard'}); BD_ERR=''; BD_T=Date.now(); return true; }
  catch(e){ BD_ERR=(e&&e.message)||String(e); return !BD; }
}
const bViews=()=>((BD&&BD.views)||[]).filter(v=>v.path!=='bsb'&&v.path!=='webui');
const bView=()=>{ const vs=bViews(); return vs.find(v=>v.path===BV)||vs[0]; };
window.addEventListener('pagehide',()=>bUnsub());
function bUnsub(){ BOSUBS.forEach(p=>Promise.resolve(p).then(u=>{ try{ u&&u(); }catch(e){} }).catch(()=>{})); BOSUBS=[]; }
function bName(c,id){ if(c&&c.name) return c.name; const s=id&&S(id); return (s&&s.attributes&&s.attributes.friendly_name)||id||''; }
let BCS=[];
function bCond(conds){ const r=bCondEval(conds); BCS.push([conds,r]); return r; }
function bCondEval(conds){ return (conds||[]).every(c=>{ const s=S(c.entity); const st=s?s.state:'unavailable';
  const L=x=>Array.isArray(x)?x.map(String):[String(x)];
  if(c.state!=null && !L(c.state).includes(st)) return false;
  if(c.state_not!=null && L(c.state_not).includes(st)) return false; return true; }); }
// Markdown (Ausgabe von HA-Vorlagen) → HTML, nur was die EMS-ESP-Ansicht braucht
function bMd(t){
  const inl=x=>esc(x).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+|\/[^)\s]*)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
  const lines=String(t||'').split('\n'); let out='', tab=null, para=[];
  const flushP=()=>{ if(para.length){ out+='<p>'+para.join('<br>')+'</p>'; para=[]; } };
  const flushT=()=>{ if(tab){ out+='<table class="bmt">'+tab.map((r,i)=>'<tr>'+r.map(c=>i?`<td>${inl(c)}</td>`:`<th>${inl(c)}</th>`).join('')+'</tr>').join('')+'</table>'; tab=null; } };
  for(const raw of lines){ const l=raw.replace(/\s+$/,'');
    if(/^\s*\|/.test(l)){ flushP(); const cells=l.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());
      if(cells.every(c=>/^:?-{2,}:?$/.test(c)||c==='')) continue; (tab=tab||[]).push(cells); continue; }
    flushT();
    if(!l.trim()){ flushP(); continue; }
    let m=l.match(/^(#{1,4})\s+(.*)$/); if(m){ flushP(); out+=`<div class="bmh">${inl(m[2])}</div>`; continue; }
    m=l.match(/^>\s?(.*)$/); if(m){ flushP(); out+=`<div class="bq">${inl(m[1])}</div>`; continue; }
    para.push(inl(l.trim())); }
  flushT(); flushP(); return out;
}
function bMdBox(tpl){ const i=BMD.length; BMD.push(tpl); return `<div class="bmd" data-md="${i}"><span class="v d">…</span></div>`; }
function bSubscribe(){ const h=H(); if(!h) return;
  BMD.forEach((tpl,i)=>{ BOSUBS.push(h.connection.subscribeMessage(r=>{ const el=document.querySelector(`[data-md="${i}"]`); if(el && r && r.result!=null) el.innerHTML=bMd(r.result); },
    {type:'render_template',template:tpl,report_errors:false}).catch(()=>{ const el=document.querySelector(`[data-md="${i}"]`); if(el) el.innerHTML=bMd(tpl.replace(/\{[{%][^}]*[}%]\}/g,'')); })); }); }
const bVal=id=>`<span class="v" data-val="${esc(id)}">${fmtState(S(id))}</span>`;
function bRow(id,name){ const s=S(id); if(!s) return ''; const dom=id.split('.')[0];
  const ctl=['switch','select','number','button'].includes(dom);
  const nv=ctl&&s.state==='unavailable';
  return `<div class="row"${nv?` data-bnv="${esc(id)}"`:''}><span class="k">${esc(name||bName(null,id))}</span>${ctl?ctlHtml({id}):bVal(id)}</div>`; }
function bClimate(id,name){ const s=S(id); if(!s) return ''; const a=s.attributes||{}, dom=id.split('.')[0];
  const step=+a.target_temp_step||(dom==='water_heater'?1:0.5), u=a.temperature_unit||'°C';
  let h=`<div class="row"><span class="k">Ist</span><span class="v" data-ccur="${esc(id)}">${a.current_temperature!=null?nf(a.current_temperature,1)+' °C':'–'}</span></div>`;
  h+=`<div class="row"><span class="k">Soll</span><span class="ctl"><span class="num"><button data-cstep="-1" data-ce="${esc(id)}">−</button><input type="number" data-ct="${esc(id)}" value="${a.temperature??''}" step="${step}" min="${a.min_temp??''}" max="${a.max_temp??''}"><button data-cstep="1" data-ce="${esc(id)}">+</button></span><span class="u">°C</span></span></div>`;
  if(dom==='climate' && (a.hvac_modes||[]).length) h+=`<div class="row"><span class="k">Betriebsart</span><span class="ctl hvs">${a.hvac_modes.map(m=>`<button class="chip ${s.state===m?'on':''}" data-hvac="${esc(m)}" data-ce="${esc(id)}">${esc(HVAC[m]||m)}</button>`).join('')}</span></div>`;
  return `<div class="card"><div class="cap">${esc(name||bName(null,id))}</div>${h}</div>`;
}
// einzelne Lovelace-Karte → Zeilen (innerhalb einer Karte)
function bPiece(c){
  if(!c) return '';
  switch(c.type){
    case 'heading': if(c.icon==='mdi:thermostat-box') return ''; return `<div class="cap bcap">${esc(c.heading||'')}</div>`;
    case 'conditional': return bCond(c.conditions)?bPiece(c.card):'';
    case 'vertical-stack': case 'grid': return (c.cards||[]).map(bPiece).join('');
    case 'markdown': return (c.title?`<div class="cap bcap">${esc(c.title)}</div>`:'')+bMdBox(c.content||'');
    case 'iframe': if(c.url===B_SKIP_IFRAME) return ''; { const r=(c.grid_options&&c.grid_options.rows)||0;
      return `<iframe class="bif" src="${esc(c.url)}" style="height:${r?Math.max(r*62,240):520}px" title="EMS-ESP"></iframe>`; }
    case 'button': { const p=c.tap_action&&c.tap_action.navigation_path||''; const v=p.split('/').pop();
      return `<div class="row"><span class="k">${esc(c.name||'')}</span><span class="v"><button class="btn sm" data-bv="${esc(v)}">${I.open} Öffnen</button></span></div>`; }
    case 'entities': return (c.title?`<div class="cap bcap">${esc(c.title)}</div>`:'')+(c.entities||[]).map(r=>{
      if(typeof r==='string') return bRow(r);
      if(r.type==='conditional') return bCond(r.conditions)?bRow(r.row.entity,r.row.name):'';
      return r.entity?bRow(r.entity,r.name):''; }).join('');
    case 'tile': { const id=c.entity, s=S(id); if(!s) return ''; const dom=id.split('.')[0], ta=c.tap_action||{};
      if(ta.action==='perform-action' && ta.perform_action==='button.press'){
        const conf=(ta.confirmation&&ta.confirmation.text)||'';
        return `<div class="row"><span class="k">${esc(bName(c,id))}</span><span class="v"><button class="btn sm ${c.color==='orange'?'pri':''}" data-bpress="${esc(id)}" data-conf="${esc(conf)}">Ausführen</button></span></div>`; }
      if(dom==='climate'||dom==='water_heater') return '';   // eigene Karte (bClimate)
      return bRow(id,bName(c,id)); }
    default: return '';
  }
}
function bCard(cards,title){ const inner=cards.map(bPiece).join(''); if(!inner.trim()) return '';
  return `<div class="card">${title?`<div class="cap">${esc(title)}</div>`:''}${inner.replace(/^<div class="cap bcap">/,'<div class="cap">')}</div>`; }
// v1.22.7: Bosch-Menü aufgebaut wie „Brötje/Samsung EHS“: wenige Hauptpunkte als Chips,
// Unterseiten als Umschalter (.seg) oben im Inhalt. Weboberfläche entfällt.
// v1.22.8: „System“ ist eine Seite mit Kacheln + Ausklappmenü (WP-Auswertung, Zuordnung, MQTT, Uhrzeit)
const B_MENU=[['uebersicht','Übersicht','home',['system']],['werte','Werte','list',['einstellungen','messwerte']],
  ['bsystem','System','gear',['bsystem']],['logs','Logs','log',['log']]];
const B_SYSV={zuordnung:'b-map',mqtt:'b-mqtt',uhrzeit:'b-ntp',auswertung:'b-wpa'};
let BMEM={}; try{ BMEM=JSON.parse(localStorage.getItem('emsehs_bmem')||'{}')||{}; }catch(e){}
const bGrp=p=>B_MENU.find(g=>g[3].includes(p)||(g[0]==='bsystem'&&B_SYSV[p]));
function bNav(){
  const cur=BV==='bsystem'?'bsystem':(bView()||{}).path, have=bViews().map(v=>v.path).concat('bsystem'), g0=bGrp(cur);
  if(g0 && BMEM[g0[0]]!==cur){ BMEM[g0[0]]=cur; try{ localStorage.setItem('emsehs_bmem',JSON.stringify(BMEM)); }catch(e){} }
  const chips=B_MENU.map(([k,l,ic,subs])=>{ const av=subs.filter(x=>have.includes(x)); if(!av.length) return '';
      const to=av.includes(BMEM[k])?BMEM[k]:av[0];
      return `<a href="#bosch/${to}" class="chip ${g0&&g0[0]===k?'on':''}">${BI[ic]||''}<span>${l}</span></a>`; }).join('')
    +bViews().filter(v=>!v.subview&&!bGrp(v.path)).map(v=>`<a href="#bosch/${esc(v.path)}" class="chip ${cur===v.path?'on':''}">${BI[B_ICON[v.path]]||''}<span>${esc(v.title)}</span></a>`).join('');
  let seg='';
  if(g0 && g0[3].length>1){ const sv=bViews().filter(v=>g0[3].includes(v.path)&&!v.subview);
    if(sv.length>1) seg=`<div class="seg">${sv.map(v=>`<button data-bv="${esc(v.path)}" class="${cur===v.path?'on':''}">${esc(v.title)}</button>`).join('')}</div>`; }
  return `<nav class="subnav top bnav">${chips}</nav>${seg}`; }
// ---- System (v1.22.8) ----
let BSYS={}, BSYS_T=0, BSYS_L=false;
async function bSysLoad(force){
  if(BSYS_L || (!force && Date.now()-BSYS_T<60000)) return; BSYS_L=true;
  const g=t=>ws({type:'ems_esp_manager/'+t+'/get'}).then(r=>r,e=>({err:(e&&e.message)||String(e)}));
  const [m,n]=await Promise.all([g('mqtt'),g('ntp')]); BSYS={mqtt:m,ntp:n}; BSYS_T=Date.now(); BSYS_L=false;
  const el=document.getElementById('bsys-tiles'); if(el && TAB==='bosch' && BV==='bsystem') el.innerHTML=bSysTiles(); }
const bHasWp=()=>bViews().some(v=>v.path==='auswertung');
function bSysTiles(){
  const t=(k,ic,l,v,sub,c)=>`<div class="tile lnk ${c||''}" data-btile="${k}"><div class="tl"><span class="ti">${BI[ic]||''}</span>${esc(l)}</div><div class="tv">${v}</div><div class="ts">${sub}</div></div>`;
  const out=[];
  if(bHasWp()){ waVendor('bosch'); const P=WPR_PER[WPA.per], c=waC()[P.k]||{}; if(!c.res&&!c.loading&&!c.err) waLoad(P.k);
    const r=c.res, kr=r?r.find.filter(x=>x.lv===2).length:0;
    out.push(t('b-wpa','wp','WP-Auswertung',r?'AZ '+waN(r.K.az,2):c.loading?'lädt …':'–',
      r?`${nf(r.K.starts)} Starts · ${esc(P.l)}${kr?' · '+kr+' kritisch':''}`:esc(P.l),r?(kr?'bad':'good'):'')); }
  out.push(t('b-map','dev','Zuordnung','Panel ↔ HA','Entitäten der Panel-Funktionen'));
  const m=BSYS.mqtt||{}, ms=m.status||{}, mh=(m.settings||{}).host;
  out.push(t('b-mqtt','link','MQTT',!BSYS.mqtt?'…':m.err?'–':ms.connected?'verbunden':ms.enabled===false?'aus':'getrennt',
    m.err?esc(m.err):esc(mh||''),m.err?'':ms.connected?'good':BSYS.mqtt?'bad':''));
  const n=BSYS.ntp||{}, ns=n.status||{}, off=ns.offset_s, srv=(n.settings||{}).server;
  const ok=off!=null&&Math.abs(off)<=60;
  out.push(t('b-ntp','clock','Uhrzeit',!BSYS.ntp?'…':n.err?'–':off==null?'unbekannt':ok?'stimmt':(off>0?'+':'')+off+' s',
    n.err?esc(n.err):esc((ns.state_text||'')+(srv?' · '+srv:'')),n.err||off==null?'':ok?'good':'bad'));
  return `<div class="tiles s4">${out.join('')}</div>`; }
function vBSys(){
  bSysLoad();
  const url=p=>{ const v=bViews().find(x=>x.path===p); const c=v&&(v.cards||[])[0]; return c&&c.url; };
  const fr=(u,h,t)=>u?`<iframe class="bif" src="${esc(u)}" style="height:${h}px" title="${esc(t)}"></iframe>`:'';
  const m=BSYS.mqtt||{}, ms=m.status||{}, n=BSYS.ntp||{}, ns=n.status||{};
  const L=[];
  if(bHasWp()) L.push(bAcc('b-wpa',BI.wp,'WP-Auswertung','Laufzeit, Taktung, Arbeitszahl · PDF-Bericht',BS_OPEN['b-wpa']?`<div class="wab">${vWpa()}</div>`:' '));
  L.push(bAcc('b-map',BI.dev,'Zuordnung','Welche Entität zu welcher Panel-Funktion gehört',fr(url('zuordnung'),640,'Zuordnung')));
  L.push(bAcc('b-mqtt',BI.link,'MQTT',m.err?'nur als Administrator':ms.connected?'verbunden'+((m.settings||{}).host?' · '+esc(m.settings.host):''):BSYS.mqtt?'nicht verbunden':'…',fr(url('mqtt'),620,'MQTT')));
  L.push(bAcc('b-ntp',BI.clock,'Uhrzeit',n.err?'nur als Administrator':esc(ns.state_text||'…')+((n.settings||{}).server?' · '+esc(n.settings.server):''),fr(url('uhrzeit'),560,'Uhrzeit')));
  return `<div id="bsys-tiles">${bSysTiles()}</div><div class="accs">${L.join('')}</div>`; }
function vBosch(){
  bUnsub(); BMD=[]; BCS=[];
  if(BD_ERR && !BD) return `<div class="card pad"><div class="note top0">Bosch-Ansicht nicht lesbar: ${esc(BD_ERR)}</div></div>`;
  if(!BD) return '<div class="empty">Lade …</div>';
  if(B_SYSV[BV]){ BS_OPEN[B_SYSV[BV]]=1; try{ localStorage.setItem('emsehs_bopen',JSON.stringify(BS_OPEN)); }catch(e){} BV='bsystem'; try{ history.replaceState(null,'','#bosch/bsystem'); localStorage.setItem('emsehs_bview',BV); }catch(e){} }
  if(BV==='bsystem') return bNav()+vBSys();
  const v=bView(); if(!v) return '<div class="empty">Keine Daten vom EMS-ESP-Gateway.</div>';
  let body='';
  if(v.subview) body=`<div class="subnav top"><a href="#bosch/${esc((v.back_path||'/system').split('/').pop())}" class="chip">‹ Zurück</a></div>`;
  if(v.type==='panel'){ const c=(v.cards||[])[0]||{};
    if(c.type==='iframe') body+=`<iframe class="fr" src="${esc(c.url)}" title="${esc(v.title)}"></iframe>`;
    else body+=`<div class="card">${bPiece(c)}</div>`; }
  else if(v.type==='sections'){
    const secs=(v.sections||[]).map(s=>bCard(s.cards||[])).filter(Boolean);
    const n=secs.length>=3?3:2, cols=Array.from({length:n},()=>[]); secs.forEach((h,i)=>cols[i%n].push(h));
    body+=`<div class="grid ${n===3?'g3':''}">${cols.map(c=>`<div class="col">${c.join('')}</div>`).join('')}</div>`; }
  else { const cl=[], other=[];
    (v.cards||[]).forEach(c=>{ if(c.type==='tile' && /^(climate|water_heater)\./.test(c.entity||'')) cl.push(bClimate(c.entity,c.name)); else other.push(c.type==='entities'?bCard([{...c,title:null}],c.title):bCard([c])); });
    body+=`<div class="mas">${cl.join('')}${other.join('')}</div>`; }
  return bNav()+body;
}
function bUpdate(){
  const a=document.activeElement, busy=a&&a.closest&&a.closest('#main')&&/^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);
  if(!busy && (BCS.some(([c,r])=>bCondEval(c)!==r) || [...document.querySelectorAll('#main [data-bnv]')].some(el=>{ const s=S(el.dataset.bnv); return s&&s.state!=='unavailable'; }))){ render(); return; }
  document.querySelectorAll('[data-ccur]').forEach(el=>{ const s=S(el.dataset.ccur); const t=s&&s.attributes&&s.attributes.current_temperature; el.textContent=t!=null?nf(t,1)+' °C':'–'; });
  document.querySelectorAll('[data-ct]').forEach(el=>{ if(document.activeElement===el||(el._dirty&&Date.now()-el._dirty<6000)) return; const s=S(el.dataset.ct); const t=s&&s.attributes&&s.attributes.temperature; if(t!=null&&+el.value!==+t) el.value=t; });
  document.querySelectorAll('[data-hvac]').forEach(el=>{ const p=el.parentNode; if(p._dirty && Date.now()-p._dirty<10000) return; const s=S(el.dataset.ce); if(s) el.classList.toggle('on',s.state===el.dataset.hvac); });
}
async function boschClick(b){
  if(b.dataset.btile){ const k=b.dataset.btile; BS_OPEN[k]=1; try{ localStorage.setItem('emsehs_bopen',JSON.stringify(BS_OPEN)); }catch(e){} render(); const el=document.getElementById('bacc-'+k); if(el) el.scrollIntoView({behavior:'smooth',block:'start'}); return true; }
  if(b.dataset.bv){ location.hash='bosch/'+b.dataset.bv; window.scrollTo(0,0); return true; }
  if(b.dataset.bpress){ const c=b.dataset.conf; if(c && !confirm(c)) return true; setVal(b,()=>call('button','press',{entity_id:b.dataset.bpress})); return true; }
  if(b.dataset.hvac){ b.parentNode._dirty=Date.now(); setVal(b,()=>call('climate','set_hvac_mode',{entity_id:b.dataset.ce,hvac_mode:b.dataset.hvac})); b.parentNode.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===b)); return true; }
  if(b.dataset.cstep){ const inp=b.parentNode.querySelector('input'); if(inp.value==='') return true; const st=+inp.step||0.5; let v=(parseFloat(inp.value)||0)+(+b.dataset.cstep)*st;
    if(inp.min!=='') v=Math.max(+inp.min,v); if(inp.max!=='') v=Math.min(+inp.max,v); inp.value=+v.toFixed(2); inp._dirty=Date.now();
    clearTimeout(inp._t); inp._t=setTimeout(()=>bSetTemp(inp),800); return true; }
  return false;
}
function bSetTemp(inp){ const id=inp.dataset.ct, dom=id.split('.')[0]; const v=parseFloat(String(inp.value).replace(',','.'));
  if(isNaN(v)){ inp._dirty=0; bUpdate(); return; } inp._dirty=Date.now();
  return setVal(inp,()=>call(dom,'set_temperature',{entity_id:id,temperature:v})); }

/*__WPA_JS__*/
/* =================== Rendern & Ereignisse =================== */
function render(full){
  if(!H()) return;
  if(!BSBMODE && VEN() && !HAS('samsung') && inVG(TAB)){ TAB='system'; try{ history.replaceState(null,'','#system'); }catch(e){} head(); }
  const m=$('#main'); const y=window.scrollY;
  if(TAB!=='bosch') bUnsub();
  const html = TAB==='system'?(BSBMODE?vBsbOverview():vOverview()):TAB==='bosch'?vBosch():TAB==='sstart'?sStart():TAB==='ssystem'?sSystem():TAB==='sbus'?sBus():TAB==='einstellungen'?(BSBMODE?vGroups('settings'):sWerte('settings')):TAB==='messwerte'?(BSBMODE?vGroups('values'):sWerte('values')):TAB==='verbindung'?(BSBMODE?vBsbConnLog():vLogs()):TAB==='webui'?vWeb():TAB==='heizstab'?vHeater():TAB==='bsb'?vBsb():vLog();
  const lb=$('#hlogbody')||$('#elogbody'), lt=lb?lb.scrollTop:0, lid=lb?lb.id:'';
  m.innerHTML=(inVG(TAB)?vgNav():'')+html; if(!full) window.scrollTo(0,y);
  if(TAB==='bosch') bSubscribe();
  const lb2=lid&&document.getElementById(lid); if(lb2&&lt) lb2.scrollTop=lt;
  const q=$('#q'); if(q){ q.oninput=()=>{ Q=q.value; const pos=q.selectionStart; render(); const n=$('#q'); n.focus(); n.setSelectionRange(pos,pos); }; }
}
$('#main').addEventListener('click',async ev=>{
  const b=ev.target.closest('button,[data-act],[data-btile]'); if(!b) return;
  const act=b.dataset.act;
  if(((TAB==='ssystem' && !BSBMODE) || (TAB==='bsb' && BSUB==='system') || (TAB==='bosch' && BV==='bsystem')) && waClick(b)) return;
  if(b.dataset.btile && TAB==='ssystem' && !BSBMODE){ const k=b.dataset.btile; BS_OPEN[k]=1; try{ localStorage.setItem('emsehs_bopen',JSON.stringify(BS_OPEN)); }catch(e){} render(); const el=document.getElementById('bacc-'+k); if(el) el.scrollIntoView({behavior:'smooth',block:'start'}); return; }
  if(b.dataset.bacc && TAB!=='bsb'){ const k=b.dataset.bacc; if(BS_OPEN[k]) delete BS_OPEN[k]; else BS_OPEN[k]=1; try{localStorage.setItem('emsehs_bopen',JSON.stringify(BS_OPEN));}catch(e){} render(); return; }
  if(b.dataset.hash){ location.hash=b.dataset.hash; window.scrollTo(0,0); return; }
  if((b.dataset.loff||b.dataset.lgo) && await logsClick(b)) return;
  if(act==='gopkt'){ LOG.pkt=true; LOG.data=null; location.hash='verbindung'; return; }
  if(b.dataset.esub){ ESUB=b.dataset.esub; try{localStorage.setItem('emsehs_esub',ESUB);}catch(e){} if(location.hash==='#log') history.replaceState(null,'','#verbindung'); render(); if(ESUB==='log') loadLog(); return; }
  if(TAB==='bosch' && await boschClick(b)) return;
  if(TAB==='heizstab' && await heaterClick(b)) return;
  if((TAB==='bsb' || (BSBMODE && (TAB==='verbindung'||TAB==='system'))) && await bsbClick(b)) return;
  if(b.dataset.re){ b.disabled=true; try{ await ws({type:'ems_esp_manager/samsung/reconnect',entry_id:b.dataset.re}); }catch(e){ alert(e&&e.message||e); } setTimeout(loadAll,1500); return; }
  if(b.dataset.sw){ const on=!b.classList.contains('on'); b._dirty=Date.now(); setVal(b,()=>call('switch',on?'turn_on':'turn_off',{entity_id:b.dataset.sw})); b.classList.toggle('on',on); return; }
  if(b.dataset.btn){ setVal(b,()=>call('button','press',{entity_id:b.dataset.btn})); return; }
  if(b.dataset.step){ const inp=b.parentNode.querySelector('input'); const st=+inp.step||1; const base=inp.min!==''?+inp.min:0;
    let v=(parseFloat(inp.value)||0)+(+b.dataset.step)*st; v=base+Math.round((v-base)/st)*st;
    if(inp.min!=='') v=Math.max(+inp.min,v); if(inp.max!=='') v=Math.min(+inp.max,v); inp.value=+v.toFixed(4); inp._dirty=Date.now();
    clearTimeout(inp._t); inp._t=setTimeout(async()=>{ const val=+inp.value; await setVal(inp,()=>call('number','set_value',{entity_id:inp.dataset.num,value:val})); inp._dirty=Date.now(); },800); return; }
  if(b.dataset.lv){ LOG.level=b.dataset.lv; LOG.pkt=false; LOG.data=null; render(); loadLog(true); return; }
  if(act==='ids'){ SHOW_ID=!SHOW_ID; render(); return; }
  if(act==='pkt'){ LOG.pkt=!LOG.pkt; LOG.data=null; render(); loadLog(true); return; }
  if(act==='pause'){ LOG.pause=!LOG.pause; render(); return; }
  if(act==='elog'){ const on=!(ST&&ST.log_enabled===false); b.classList.toggle('on',!on);
    try{ const r=await ws({type:'ems_esp_manager/samsung/log_enable',enabled:!on}); if(ST) ST.log_enabled=r.enabled; }catch(e){ alert(e&&e.message||e); } loadLog(true); return; }
  if(act==='edl'){ const blob=new Blob(['\ufeff'+eLogText()],{type:'text/plain;charset=utf-8'}); const u=URL.createObjectURL(blob);
    const d=new Date(), p=n=>String(n).padStart(2,'0'); const a=document.createElement('a'); a.href=u;
    a.download=`ehs-log_${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}_${p(d.getHours())}-${p(d.getMinutes())}.txt`;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(u),5000); return; }
  if(act==='copy'){ const txt=LOG.pkt?((LOG.data&&LOG.data.lines)||[]).join('\n'):((LOG.data&&LOG.data.entries)||[]).map(e=>`${new Date(e.ts*1000).toLocaleString('de-DE')} ${e.level} ${e.name}: ${e.msg}`).join('\n');
    try{ await navigator.clipboard.writeText(txt); b.textContent='Kopiert ✓'; }catch(e){ const t=document.createElement('textarea'); t.value=txt; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); b.textContent='Kopiert ✓'; } return; }
  if(act==='diag'){ try{ await ws({type:'ems_esp_manager/samsung/diagnostics'}); setTimeout(loadLog,800); }catch(e){ alert(e&&e.message||e); } return; }
  if(act==='clear'){ if(!confirm('Alle aufgezeichneten Meldungen löschen?')) return; try{ await ws({type:'ems_esp_manager/samsung/clear_logs'}); loadLog(); }catch(e){ alert(e&&e.message||e); } return; }
  if(act==='open'){ openPanel(b.dataset.path); return; }
  if(act==='upload'){ $('#file').click(); return; }
  if(act==='yes'){ const f=PENDING; PENDING=null; $('#ask').innerHTML=''; if(f) await f(); return; }
  if(act==='no'){ PENDING=null; $('#ask').innerHTML=''; msg(''); return; }
});
$('#main').addEventListener('change',ev=>{
  const el=ev.target;
  if(TAB==='heizstab' && heaterChange(el)) return;
  if(el.dataset.ct){ bSetTemp(el); return; }
  if((TAB==='bsb' || (BSBMODE && (TAB==='verbindung'||TAB==='system'))) && bsbChange(el)) return;
  if(el.dataset.sel){ el._dirty=Date.now(); setVal(el,()=>call('select','select_option',{entity_id:el.dataset.sel,option:el.value})); return; }
  if(el.dataset.num){ const raw=el.value.replace(',','.'); if(raw===''||isNaN(+raw)){ el._dirty=0; updateLive(); return; } clearTimeout(el._t); let v=+raw; if(el.min!=='') v=Math.max(+el.min,v); if(el.max!=='') v=Math.min(+el.max,v); el.value=v;
    el._dirty=Date.now(); setVal(el,()=>call('number','set_value',{entity_id:el.dataset.num,value:v})); return; }
  if(el.dataset.act==='lvl'){ ws({type:'ems_esp_manager/samsung/log_level',level:el.value}).then(()=>{ if(ST) ST.log_level=el.value; loadLog(); }).catch(e=>alert(e&&e.message||e)); }
});
$('#main').addEventListener('input',ev=>{ const t=ev.target; if(TAB==='bsb' && t.id==='bsq'){ BS_Q=t.value; const pos=t.selectionStart; render(); const n=$('#bsq'); if(n){ n.focus(); n.setSelectionRange(pos,pos); } } });
$('#main').addEventListener('pointerdown',()=>{ if(TAB==='heizstab') H_TOUCH=Date.now(); },true);
$('#main').addEventListener('keydown',ev=>{ const t=ev.target; if(ev.key==='Enter' && (t.dataset.num||t.dataset.hcn||t.dataset.hdn||t.dataset.hds)) t.blur(); if(ev.key==='Enter' && t.id==='hhost'){ const b=document.querySelector('[data-act="hhost"]'); b&&b.click(); } });
/* Panel-Datei hochladen */
let PENDING=null; const msg=(t,c)=>{ const m=$('#msg'); if(m){ m.className='msg '+(c||''); m.textContent=t; } };
$('#fwfile').onchange=ev=>{ const f=ev.target.files[0]; ev.target.value=''; if(f) bsFwUpload(f); };
$('#file').onchange=async ev=>{
  const f=ev.target.files[0]; ev.target.value=''; if(!f) return;
  if(f.size>3*1024*1024){ msg('Datei ist größer als 3 MB.','err'); return; }
  const text=await f.text(); const m=text.match(/PANEL_VERSION\s*=\s*["']([^"']+)["']/);
  PENDING=async()=>{ msg('Prüfe und übernehme …'); try{ const r=await ws({type:'ems_esp_manager/panel/upload',content:text,filename:f.name}); await loadAll(); msg('Neue Version aktiv: '+(r.version||'ohne Nummer')+'. Geöffnete Panels bitte neu laden.','ok'); }catch(e){ msg('Nicht übernommen: '+(e&&e.message||e),'err'); } };
  $('#ask').innerHTML=`<div class="msg">„${esc(f.name)}“ (${m?'Version '+esc(m[1]):'ohne Versionsnummer'}) als Panel übernehmen? Die bisherige Version wird gesichert.</div><div class="btns" style="margin-top:0"><button class="btn pri" data-act="yes">Übernehmen</button><button class="btn" data-act="no">Abbrechen</button></div>`;
};
async function loadAll(){
  if(BSBMODE){
    const [gr]=await Promise.allSettled([ws({type:'ems_esp_manager/bsb/entities'}),htLoad(),bsLoad(null,true),logsLoad()]);
    if(gr.status==='fulfilled') GR=gr.value; ST=ST||{instances:[]};
    head(); if(TAB==='einstellungen' && document.activeElement && document.activeElement.closest('#main')) return; safeRender(); return;
  }
  const [st]=await Promise.allSettled([ws({type:'ems_esp_manager/samsung/status'})]);
  if(st.status==='fulfilled') ST=st.value;
  const [info,gr]=await Promise.allSettled([ws({type:'ems_esp_manager/panel/info',vendor:PV()}),ws({type:'ems_esp_manager/samsung/entities'}),htLoad(),bsbLoad(),logsLoad()]);
  ST = st.status==='fulfilled' ? st.value : {err: st.reason&&st.reason.code==='unauthorized'?'Status nur für Administratoren.':((st.reason&&st.reason.message)||'Status nicht lesbar'), instances:[]};
  INFO = info.status==='fulfilled' ? info.value : null; if(gr.status==='fulfilled') GR=gr.value;
  head();
  if(TAB==='ssystem' && BS_OPEN['s-map']) return;   // iframe der Zuordnung nicht neu laden
  if(TAB==='bsb'){ safeRender(); return; }
  if(TAB==='bosch') return;
  if(PENDING && TAB==='system' && $('#ask')){ const o=$('#o_ehs'); o&&(o.innerHTML=ehsRows()); return; }   // Rückfrage „Übernehmen?“ stehen lassen
  if(TAB==='verbindung' && document.activeElement && document.activeElement.tagName==='SELECT') return;

  if(TAB!=='einstellungen' || !document.activeElement || !document.activeElement.closest('#main')) render();
}
(function wait(n){ const h=H(); if(h&&h.connection){ route(); loadAll(); let ABUSY=false, HBUSY=false;
    setInterval(()=>{ if(!document.hidden && /^(system|sstart|einstellungen|messwerte|bsb|bosch)$/.test(TAB)){ updateLive(); if(TAB==='bosch') bUpdate(); } },3000);
    setInterval(async()=>{ if(document.hidden || ABUSY || !/^(system|sstart|ssystem|sbus|verbindung)$/.test(TAB)) return; ABUSY=true; try{ await loadAll(); }finally{ ABUSY=false; } },15000);
    setInterval(()=>{ if(!document.hidden && !BSBMODE && TAB==='verbindung' && LSUB!=='wpa' && !LOG.pause && !LBUSY) loadLog(); },4000);
    let BTICK=0;
    setInterval(async()=>{ if(document.hidden || !(TAB==='bsb' || (BSBMODE && (TAB==='verbindung'||TAB==='system')))) return; BTICK++;
      const busy=!!(bsJob() || (BS&&BS.find&&BS.find.running) || (BS&&BS.fw&&BS.fw.job&&BS.fw.job.running));
      if(busy || BTICK%4===0){ await bsLoad(null, true); const a=document.activeElement; if(!(a && a.closest && a.closest('#main') && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName))) safeRender(); }
      if(((TAB==='bsb'&&BSUB==='log'&&LSUB!=='wpa')||TAB==='verbindung') && bc().log_enabled && !BLOG.pause){ await loadBLog(); bLogRefresh(); } },2500);
    setInterval(async()=>{ if(document.hidden || HBUSY || TAB!=='heizstab') return; HBUSY=true;
      try{ await htLoad(); if(HSUB==='log' && hc().log_enabled && !HLOG.pause) await loadHLog();
        const a=document.activeElement, inField=a && a.closest('#main') && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);
        if(!inField && !hBusy()) render(); else hLogRefresh(); }
      finally{ HBUSY=false; } },4000);
    return; }
  if(n>100){ $('#main').innerHTML='<div class="empty">Diese Seite funktioniert nur innerhalb von Home Assistant (Seitenleiste „EMS-EHS“).</div>'; return; } setTimeout(()=>wait(n+1),100); })(0);
</script></body></html>
"""
