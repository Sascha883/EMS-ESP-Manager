"""Bosch EHS panel: served by Home Assistant, mapping/token kept in HA.

- The panel HTML is shipped with the integration (ems-panel.html).
  A newer version can be uploaded from the dashboard; it is stored in
  /config/ems_esp_manager/ (survives updates of the integration), the
  previous one is kept as a backup.
- Entity mapping and heating-circuit names are stored in HA (.storage),
  not in the HTML file and not only in the browser.
- The long-lived token is kept in the integration options. It is only handed
  to the page when it is opened with the secret kiosk key (access from
  outside HA, e.g. iPhone home screen). Inside HA the panel uses the login
  of the HA frontend and needs no token at all.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
from pathlib import Path
import re
import secrets
from typing import Any

from aiohttp import web
import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .entity_map import build_auto_map, ems_devices, ems_entities, ems_ha_entities, ems_value_ids
from .const import (
    CONF_PANEL_ADMIN_ONLY,
    CONF_PANEL_SHOW_IN_SIDEBAR,
    CONF_PANEL_TOKEN,
    DOMAIN,
    PANEL_FILE,
    PANEL_FILE_OLD,
    URL_PANEL,
    URL_PANEL_ADMIN,
    VENDOR_BOSCH,
    VENDOR_HEATER,
    VENDOR_SAMSUNG,
    VENDORS,
    VENDOR_BSB,
    ems_entries,
)

# sidebar entry per manufacturer – one panel file, the manager tells it the vendor
# v1.24.0: aus dem Hersteller-Register (vendors/base.py) statt eigener Tabelle
from .vendors import REGISTRY as _VREG  # noqa: E402

_SIDEBAR = {k: spec.sidebar for k, spec in _VREG.items()}
# frühere Adressen je Hersteller (ohne Seitenleiste weiter erreichbar)
_OLD_URLS = {k: spec.old_urls for k, spec in _VREG.items() if spec.old_urls}
_VENDOR_SCHEMA = vol.In(VENDORS)

_LOGGER = logging.getLogger(__name__)

_DATA = f"{DOMAIN}_panel"
_STORE_KEY = f"{DOMAIN}.panel"
_STORE_VERSION = 1
_SCHEMA = 2   # v1.24.0: Daten je Hersteller unter vendors.<key>

_HERE = Path(__file__).parent
# shipped panel: next to the code (older versions: subfolder "panel")
BUNDLED_FILE = _HERE / PANEL_FILE
if not BUNDLED_FILE.exists() and (_HERE / "panel" / PANEL_FILE).exists():
    BUNDLED_FILE = _HERE / "panel" / PANEL_FILE
MAX_SIZE = 3 * 1024 * 1024

_RE_VERSION = re.compile(r"""PANEL_VERSION\s*=\s*["']([^"']+)["']""")
_RE_TOKEN = re.compile(r"""(const\s+HA_TOKEN\s*=\s*)(["'])([^"']*)\2""")
_RE_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def panel_version(html: str) -> str | None:
    """Version string written in the panel (const PANEL_VERSION = "...")."""
    m = _RE_VERSION.search(html)
    return m.group(1).strip() if m else None


def _version_key(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    return tuple(int(x) for x in re.findall(r"\d+", version))


def validate_panel(html: str) -> list[str]:
    """Return a list of problems (empty = file looks like the manager panel)."""
    problems: list[str] = []
    if len(html.encode("utf-8")) > MAX_SIZE:
        problems.append("Datei ist größer als 3 MB")
    low = html[:4000].lower()
    if "<html" not in low:
        problems.append("keine HTML-Datei")
    if not any(k in html.lower() for k in ("ems-esp manager panel", "bosch ehs", "bh-800", "hmi 800")):
        problems.append("kein Panel des EMS-ESP Managers (Kennung fehlt)")
    for marker in ("const MENUS", "function connect(", "</script>"):
        if marker not in html:
            problems.append(f"Panel-Code unvollständig ({marker} fehlt)")
            break
    return problems


def strip_token(html: str) -> tuple[str, bool]:
    """Empty a token that was written into the file (it belongs into the options)."""
    found = False

    def _sub(m: re.Match[str]) -> str:
        nonlocal found
        if m.group(3):
            found = True
        return f"{m.group(1)}{m.group(2)}{m.group(2)}"

    return _RE_TOKEN.sub(_sub, html, count=1), found


@dataclass
class _PanelFile:
    html: str
    version: str | None
    source: str  # "bundled" | "uploaded"
    mtime: float


class PanelManager:
    """Holds settings, mapping and the active panel file."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(hass, _STORE_VERSION, _STORE_KEY)
        self.data: dict[str, Any] = {}
        self.dir = Path(hass.config.path(DOMAIN))
        # options of the config entry per manufacturer (sidebar, token, installer)
        self.vendor_options: dict[str, dict[str, Any]] = {}
        self._cache: dict[Path, _PanelFile] = {}

    @property
    def vendors(self) -> list[str]:
        """Manufacturers whose panel is set up (sidebar entry registered)."""
        return [v for v in VENDORS if v in self.vendor_options]

    @property
    def options(self) -> dict[str, Any]:
        """Panel options – Bosch entry first, otherwise the Samsung entry."""
        for v in VENDORS:
            if v in self.vendor_options:
                return self.vendor_options[v]
        return {}

    def opts(self, vendor: str) -> dict[str, Any]:
        return self.vendor_options.get(vendor) or self.options

    # ---------- storage ----------
    async def async_load(self) -> None:
        self.data = await self.store.async_load() or {}
        self.data.setdefault("vendors", {})
        changed = False
        if self.data.get("schema", 1) < _SCHEMA:
            changed = await self._async_migrate_v2()
        if not self.data.get("kiosk_key"):
            self.data["kiosk_key"] = secrets.token_urlsafe(24)
            changed = True
        for v in VENDORS:
            self.vdata(v)
        if changed:
            await self.store.async_save(self.data)

    async def _async_migrate_v2(self) -> bool:
        """v1.24.0: Herstellerdaten von der obersten Ebene nach ``vendors.<key>`` umziehen.

        Bis v1.23 lagen Bosch (Zuordnung, Warmwasseroptimierung, Leistungsbegrenzung …),
        Heizstab und ESP-Bus-Adapter direkt auf oberster Ebene, Samsung dagegen unter
        ``vendors.samsung``. Jetzt hat jeder Hersteller seinen eigenen Block; oben bleiben
        nur gemeinsame Werte (kiosk_key, schema). Vorher wird der alte Stand einmalig als
        ``.storage/ems_esp_manager.panel_vor_v1.24`` gesichert. Vorhandene Werte im Ziel
        haben Vorrang (nie überschreiben).
        """
        from .vendors import REGISTRY

        moved: list[str] = []
        if any(k in self.data for spec in REGISTRY.values() for k in spec.store_keys):
            try:
                backup = Store(self.hass, _STORE_VERSION, f"{_STORE_KEY}_vor_v1.24")
                if await backup.async_load() is None:
                    await backup.async_save(json.loads(json.dumps(self.data)))
            except Exception:  # noqa: BLE001 - Sicherung darf den Start nicht verhindern
                _LOGGER.warning("Sicherung des Panel-Speichers vor dem Umzug fehlgeschlagen", exc_info=True)
        vendors = self.data["vendors"]
        for spec in REGISTRY.values():
            for key in spec.store_keys:
                if key not in self.data:
                    continue
                val = self.data.pop(key)
                block = vendors.setdefault(spec.key, {})
                cur = block.get(key)
                if cur in (None, {}, []) or key not in block:
                    block[key] = val
                moved.append(f"{key}→{spec.key}")
        self.data["schema"] = _SCHEMA
        if moved:
            _LOGGER.info("Panel-Speicher auf Herstellerblöcke umgestellt: %s", ", ".join(moved))
        return True

    async def async_save(self) -> None:
        await self.store.async_save(self.data)

    def vdata(self, vendor: str = VENDOR_BOSCH) -> dict[str, Any]:
        """Eigener Speicherblock eines Herstellers (``vendors.<key>``).

        Ab v1.24.0 für alle Hersteller gleich – auch Bosch, Heizstab und ESP-Bus-Adapter.
        Ein Hersteller schreibt nur hierhin, nie in den Block eines anderen.
        """
        d = self.data.setdefault("vendors", {}).setdefault(vendor, {})
        d.setdefault("map", {})
        d.setdefault("hc_names", {})
        return d

    @property
    def token(self) -> str:
        for v in [*self.vendors, *VENDORS]:
            tok = ((self.vendor_options.get(v) or {}).get(CONF_PANEL_TOKEN) or "").strip()
            if tok:
                return tok
        return ""

    @property
    def kiosk_key(self) -> str:
        return self.data["kiosk_key"]

    async def async_new_kiosk_key(self) -> None:
        self.data["kiosk_key"] = secrets.token_urlsafe(24)
        await self.async_save()

    def kiosk_url(self, vendor: str = VENDOR_BOSCH) -> str:
        try:
            base = get_url(self.hass, allow_external=False)
        except NoURLAvailableError:
            try:
                base = get_url(self.hass)
            except NoURLAvailableError:
                base = "http://homeassistant.local:8123"
        q = _VREG[vendor].panel_query if vendor in _VREG else ""
        extra = "&" + q[1:] if q else ""
        return f"{base}{URL_PANEL}?key={self.kiosk_key}{extra}"

    def key_valid(self, key: str | None) -> bool:
        return bool(key) and hmac.compare_digest(key, self.kiosk_key)

    # ---------- files ----------
    @property
    def uploaded_path(self) -> Path:
        return self.dir / PANEL_FILE

    def _migrate_old_name(self) -> None:
        """Blocking: hochgeladenes Panel und Sicherung vom alten Dateinamen (bis v1.15.10) übernehmen."""
        for old, new in ((self.dir / PANEL_FILE_OLD, self.uploaded_path),
                         (self.dir / PANEL_FILE_OLD.replace(".html", ".backup.html"), self.backup_path)):
            try:
                if old.exists() and not new.exists():
                    old.replace(new)
            except OSError:
                _LOGGER.debug("Alte Panel-Datei %s nicht übernommen", old, exc_info=True)

    @property
    def backup_path(self) -> Path:
        return self.dir / PANEL_FILE.replace(".html", ".backup.html")

    @property
    def meta_path(self) -> Path:
        return self.dir / "panel.json"

    def _read(self, path: Path, source: str) -> _PanelFile | None:
        """Blocking: read a panel file (cached by mtime)."""
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self._cache.get(path)
        if cached and cached.mtime == mtime:
            return cached
        html = path.read_bytes().decode("utf-8", errors="replace")
        pf = _PanelFile(html, panel_version(html), source, mtime)
        self._cache[path] = pf
        return pf

    def active(self) -> _PanelFile:
        """Blocking: the panel that is served.

        An uploaded panel is used, unless the integration ships a newer one
        (both with version number) – then the shipped one wins.
        """
        self._migrate_old_name()
        bundled = self._read(BUNDLED_FILE, "bundled")
        uploaded = self._read(self.uploaded_path, "uploaded")
        if uploaded is None:
            if bundled is None:
                raise FileNotFoundError(
                    f"Panel-Datei fehlt: {BUNDLED_FILE} – bitte die Datei mitkopieren "
                    "oder über „Neue Version hochladen“ einspielen"
                )
            return bundled
        if (
            bundled is not None
            and uploaded.version
            and bundled.version
            and _version_key(bundled.version) > _version_key(uploaded.version)
        ):
            return bundled
        return uploaded

    def _meta(self) -> dict[str, Any]:
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def info(self) -> dict[str, Any]:
        """Blocking: version info for the dashboard."""
        act = self.active()
        bundled = self._read(BUNDLED_FILE, "bundled")
        backup = self._read(self.backup_path, "backup")
        meta = self._meta()
        return {
            "version": act.version,
            "source": act.source,
            "size": len(act.html.encode("utf-8")),
            "bundled_version": bundled.version if bundled else None,
            "uploaded": meta.get("current") if act.source == "uploaded" else None,
            "backup": (
                {"version": backup.version, **(meta.get("backup") or {})} if backup else None
            ),
        }

    def store_upload(self, html: str, filename: str) -> dict[str, Any]:
        """Blocking: keep the current file as backup and activate the new one."""
        self.dir.mkdir(parents=True, exist_ok=True)
        meta = self._meta()
        try:
            current: _PanelFile | None = self.active()
        except FileNotFoundError:
            current = None
        # backup = what was active until now (uploaded or shipped version)
        if current is not None:
            self.backup_path.write_text(current.html, encoding="utf-8")
            meta["backup"] = meta.get("current") if current.source == "uploaded" else {
                "filename": "mitgeliefert",
                "date": None,
            }
        tmp = self.uploaded_path.with_suffix(".tmp")
        tmp.write_text(html, encoding="utf-8")
        tmp.replace(self.uploaded_path)
        meta["current"] = {
            "filename": filename,
            "date": dt_util.now().isoformat(timespec="seconds"),
            "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest()[:12],
        }
        self.meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        self._cache.clear()
        return self.info()

    def restore_backup(self) -> dict[str, Any]:
        """Blocking: swap active and backup file."""
        if not self.backup_path.exists():
            raise FileNotFoundError("Keine Sicherung vorhanden")
        meta = self._meta()
        current = self.active()
        backup_html = self.backup_path.read_text(encoding="utf-8")
        self.backup_path.write_text(current.html, encoding="utf-8")
        self.uploaded_path.write_text(backup_html, encoding="utf-8")
        cur_meta = meta.get("current") if current.source == "uploaded" else {
            "filename": "mitgeliefert",
            "date": None,
        }
        meta["current"], meta["backup"] = (meta.get("backup") or {}), cur_meta
        self.meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        self._cache.clear()
        return self.info()


def _get(hass: HomeAssistant) -> PanelManager | None:
    return hass.data.get(_DATA)


# =====================================================================
# HTTP views
# =====================================================================
def _inject(html: str, cfg: dict[str, Any]) -> str:
    """Put the manager config in front of the panel's own script."""
    tag = (
        "<script>window.EMS_ESP_MANAGER="
        + json.dumps(cfg).replace("</", "<\\/")
        + ";</script>"
    )
    idx = html.find("<script")
    if idx < 0:
        return html
    return html[:idx] + tag + "\n" + html[idx:]


_NO_CACHE = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class PanelView(HomeAssistantView):
    """GET /api/ems_esp_manager/panel – the panel itself (Bosch EHS, Samsung EHS …).

    No login needed to load the page (an iframe cannot send the HA token);
    the page contains no secrets. Data only flows after login: inside HA via
    the frontend connection, outside only with ?key=<kiosk key>.
    """

    url = URL_PANEL
    name = "api:ems_esp_manager:panel"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        mgr = _get(hass)
        if mgr is None:
            return web.Response(status=404, text="EMS-ESP Manager nicht geladen")
        try:
            pf = await hass.async_add_executor_job(mgr.active)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Panel could not be read")
            return web.Response(status=500, text=f"Panel: {type(err).__name__}: {err}")
        vendor = request.query.get("vendor", VENDOR_BOSCH)
        if vendor not in VENDORS:
            vendor = VENDOR_BOSCH
        opts = mgr.opts(vendor)
        cfg: dict[str, Any] = {
            "managed": True,
            "vendor": vendor,
            "version": pf.version,
            "page": request.query.get("page", ""),
            "installer": {
                "name": opts.get("installer_name", ""),
                "addr": opts.get("installer_addr", ""),
                "tel": opts.get("installer_tel", ""),
            },
        }
        key = request.query.get("key")
        if key:
            if not mgr.key_valid(key):
                return web.Response(status=403, text="Ungültiger Kiosk-Schlüssel")
            if not mgr.token:
                cfg["kiosk_error"] = (
                    "Kein Token hinterlegt. Token in HA unter Profil → Sicherheit → Langlebige Zugriffstoken erstellen und unter EMS-ESP Manager → Konfigurieren einfügen."
                )
            else:
                cfg["token"] = mgr.token
                cfg["kiosk"] = True
        return web.Response(
            text=_inject(pf.html, cfg),
            content_type="text/html",
            charset="utf-8",
            headers=_NO_CACHE,
        )


class PanelAdminView(HomeAssistantView):
    """GET /api/ems_esp_manager/panel/admin – version + upload (dashboard card)."""

    url = URL_PANEL_ADMIN
    name = "api:ems_esp_manager:panel_admin"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(
            text=ADMIN_PAGE, content_type="text/html", charset="utf-8", headers=_NO_CACHE
        )


# =====================================================================
# Websocket commands (used by the panel and the admin card)
# =====================================================================
def _report_errors(func):
    """Send the real error text to the card instead of 'Unknown error'."""
    import functools

    @functools.wraps(func)
    async def wrapper(hass: HomeAssistant, connection, msg) -> None:
        try:
            await func(hass, connection, msg)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Panel: %s failed", msg.get("type"))
            connection.send_error(
                msg["id"], "panel_error", f"{type(err).__name__}: {err}"
            )

    return wrapper


async def _ems_ids(hass: HomeAssistant) -> list[str] | None:
    """Values EMS-ESP delivers (short-name object ids) – None if unknown."""
    from homeassistant.config_entries import ConfigEntryState

    for e in ems_entries(hass):
        if e.state is ConfigEntryState.LOADED:
            try:
                return await ems_value_ids(e.runtime_data.api)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("EMS-ESP values not readable", exc_info=True)
    return None


def _runtime(hass: HomeAssistant):
    """Runtime data of the first loaded gateway (or None)."""
    from homeassistant.config_entries import ConfigEntryState

    for e in ems_entries(hass):
        if e.state is ConfigEntryState.LOADED:
            return e.runtime_data
    return None


async def _ems_devices(hass: HomeAssistant) -> list[dict] | None:
    """EMS devices detected by EMS-ESP – None if unknown."""
    from homeassistant.config_entries import ConfigEntryState

    for e in ems_entries(hass):
        if e.state is ConfigEntryState.LOADED:
            try:
                return await ems_devices(e.runtime_data.api)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("EMS-ESP system info not readable", exc_info=True)
    return None


def _dhw_opt_snapshot(hass: HomeAssistant) -> dict | None:
    from .dhw_optimizer import get as _get_opt

    opt = _get_opt(hass)
    return opt.snapshot() if opt else None


def _plim_snapshot(hass: HomeAssistant) -> dict | None:
    from .power_limit import get as _get_plim

    pl = _get_plim(hass)
    return pl.snapshot() if pl else None


def _heater_snapshot(hass: HomeAssistant) -> dict | None:
    from .heater import get as _get_heater

    h = _get_heater(hass)
    return h.snapshot() if h else None


def _mgr_or_error(hass: HomeAssistant, connection, msg) -> PanelManager | None:
    mgr = _get(hass)
    if mgr is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
    return mgr


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/get",
        vol.Optional("vendor", default=VENDOR_BOSCH): _VENDOR_SCHEMA,
    }
)
@websocket_api.async_response
@_report_errors
async def ws_get(hass: HomeAssistant, connection, msg) -> None:
    """Mapping and heating-circuit names (every logged-in user)."""
    if (mgr := _mgr_or_error(hass, connection, msg)) is None:
        return
    if msg["vendor"] != VENDOR_BOSCH:
        # Samsung: no EMS-ESP scan – the panel finds the EHS Sentinel entities itself
        vd = mgr.vdata(msg["vendor"])
        connection.send_result(
            msg["id"],
            {
                "vendor": msg["vendor"],
                "map": vd["map"],
                "auto": {},
                "ems_ids": None,
                "devices": None,
                "check": None,
                "ha_ems": [],
                "ha_ems_off": [],
                "created": [],
                "dhw_opt": None,
                "heater": _heater_snapshot(hass),
                "show_cooling": vd.get("show_cooling", True),
                "hc_names": vd["hc_names"],
                "is_admin": connection.user.is_admin,
            },
        )
        return
    rt = _runtime(hass)
    scan = rt.extra.get("scan") if rt else None
    connection.send_result(
        msg["id"],
        {
            "map": mgr.vdata(VENDOR_BOSCH)["map"],
            "auto": build_auto_map(hass),
            "ems_ids": await _ems_ids(hass),
            "devices": (scan or {}).get("devices") or await _ems_devices(hass),
            "check": {"entities": scan.get("entities"), "t": scan.get("t")} if scan and scan.get("entities") else None,
            "ha_ems": ems_ha_entities(hass),
            "ha_ems_off": ems_ha_entities(hass, disabled=True),
            "created": sorted((mgr.vdata(VENDOR_BOSCH).get("created") or {}).keys()),
            "dhw_opt": _dhw_opt_snapshot(hass),
            "plim": _plim_snapshot(hass),
            "heater": _heater_snapshot(hass),
            "show_cooling": mgr.vdata(VENDOR_BOSCH).get("show_cooling", True),
            "hc_names": mgr.vdata(VENDOR_BOSCH)["hc_names"],
            "is_admin": connection.user.is_admin,
        },
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/panel/check"})
@websocket_api.async_response
@_report_errors
async def ws_check(hass: HomeAssistant, connection, msg) -> None:
    """Read/write check: every EMS-ESP entity with value and writeable flag."""
    from homeassistant.config_entries import ConfigEntryState

    result = None
    t = None
    for e in ems_entries(hass):
        if e.state is ConfigEntryState.LOADED:
            try:
                result = await ems_entities(e.runtime_data.api)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("EMS-ESP entities not readable", exc_info=True)
            if result is not None:
                import time

                t = time.time()
                scan = dict(e.runtime_data.extra.get("scan") or {})
                scan.update(entities=result, t=t)
                e.runtime_data.extra["scan"] = scan
                break
    connection.send_result(msg["id"], {"entities": result, "t": t})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/create",
        vol.Required("ids"): [str],
        vol.Optional("labels", default={}): {str: str},
    }
)
@websocket_api.require_admin
@websocket_api.async_response
@_report_errors
async def ws_create(hass: HomeAssistant, connection, msg) -> None:
    """Create manager entities for EMS-ESP values that have no entity in Home Assistant."""
    from homeassistant.config_entries import ConfigEntryState

    from .extra_values import async_create

    entry = next((e for e in ems_entries(hass) if e.state is ConfigEntryState.LOADED), None)
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    scan = entry.runtime_data.extra.get("scan") or {}
    ents = scan.get("entities") or {}
    if not ents:
        try:
            ents = await ems_entities(entry.runtime_data.api) or {}
        except Exception:  # noqa: BLE001
            ents = {}
    done: dict[str, str] = {}
    errors: dict[str, str] = {}
    for std_id in msg["ids"]:
        obj = std_id.split(".", 1)[-1]
        info = ents.get(obj)
        if info is None:
            errors[std_id] = "EMS-ESP liefert diesen Wert nicht"
            continue
        try:
            done[std_id] = await async_create(hass, entry, std_id, msg["labels"].get(std_id, ""), info)
        except Exception as err:  # noqa: BLE001
            errors[std_id] = str(err)
    connection.send_result(msg["id"], {"created": done, "errors": errors})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/panel/uncreate", vol.Required("id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
@_report_errors
async def ws_uncreate(hass: HomeAssistant, connection, msg) -> None:
    """Remove an entity the manager created."""
    from homeassistant.config_entries import ConfigEntryState

    from .extra_values import async_remove

    for e in ems_entries(hass):
        if e.state is ConfigEntryState.LOADED:
            await async_remove(hass, e, msg["id"])
            break
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/dhwopt",
        vol.Optional("config"): dict,
        vol.Optional("run", default=False): bool,
    }
)
@websocket_api.async_response
@_report_errors
async def ws_dhwopt(hass: HomeAssistant, connection, msg) -> None:
    """EMS-Regelung → Warmwasseroptimierung: save settings (admin) and/or check now."""
    from .dhw_optimizer import get as _get_opt

    opt = _get_opt(hass)
    if opt is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    if "config" in msg:
        if not connection.user.is_admin:
            connection.send_error(msg["id"], "unauthorized", "Nur Administratoren")
            return
        await opt.async_save_cfg(msg["config"])
    elif msg["run"]:
        await opt.async_tick()
    connection.send_result(msg["id"], opt.snapshot())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/plim",
        vol.Optional("config"): dict,
        vol.Optional("set"): bool,
        vol.Optional("test", default=False): bool,
        vol.Optional("writes", default=True): bool,
        vol.Optional("read", default=False): bool,
        vol.Optional("input"): dict,
        vol.Optional("learn"): dict,
        vol.Optional("itest"): dict,
        vol.Optional("dimprog"): dict,
    }
)
@websocket_api.async_response
@_report_errors
async def ws_plim(hass: HomeAssistant, connection, msg) -> None:
    """EMS-Regelung → Leistungsbegrenzung: state, settings, dim on/off, test (admin for changes)."""
    from .power_limit import get as _get_plim

    pl = _get_plim(hass)
    if pl is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    if ("config" in msg or "set" in msg or "input" in msg or "learn" in msg or "itest" in msg or "dimprog" in msg or msg["test"]) and not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Nur Administratoren")
        return
    if "config" in msg:
        await pl.async_save_cfg(msg["config"])
    if "set" in msg:
        await pl.async_set(msg["set"], "manuell", force=True)
    if "input" in msg:
        inp = msg["input"]
        await pl.async_set_input(
            int(inp.get("n", 0)), inp.get("bit"),
            bool(inp["on"]) if "on" in inp else None,
            int(inp["mod"]) if inp.get("mod") is not None else None,
        )
    if "learn" in msg:
        lr = msg["learn"]
        if lr.get("stop"):
            pl.async_stop_learn()
        elif lr.get("start") and not pl.async_start_learn(int(lr.get("secs") or 180), str(lr.get("note") or "")[:120]):
            connection.send_error(msg["id"], "busy", "Ein Test oder Anlernen läuft bereits")
            return
    if "dimprog" in msg:
        dp = msg["dimprog"]
        if isinstance(dp.get("config"), dict):
            await pl.async_save_dcfg(dp["config"])
        if dp.get("stop"):
            pl.async_stop_program()
        elif dp.get("start") and not pl.async_start_dim_program():
            connection.send_error(msg["id"], "busy", "Ein Test oder Anlernen läuft bereits")
            return
    if "itest" in msg:
        it = msg["itest"]
        n = it.get("n")
        if not pl.async_start_input_test(int(n) if n else None, bool(it.get("all"))):
            connection.send_error(msg["id"], "busy", "Ein Test oder Anlernen läuft bereits")
            return
    if msg["test"] and not pl.async_start_test(msg["writes"]):
        connection.send_error(msg["id"], "busy", "Ein Test läuft bereits")
        return
    if msg["read"]:
        try:
            await pl.read()
        except Exception:  # noqa: BLE001 - live values are optional
            _LOGGER.debug("power limit values not readable", exc_info=True)
    connection.send_result(msg["id"], pl.snapshot())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/heater",
        vol.Optional("config"): dict,
        vol.Optional("run", default=False): bool,
        vol.Optional("settings", default=False): bool,
        # Startseite des Heizstabs suchen: "" = ganzes Heimnetz, sonst IP/Hostname prüfen
        vol.Optional("find"): str,
    }
)
@websocket_api.async_response
@_report_errors
async def ws_heater(hass: HomeAssistant, connection, msg) -> None:
    """EMS-Regelung → Heizstab (novolto): read state, save settings (admin), regulate now."""
    from .heater import get as _get_heater

    h = _get_heater(hass)
    if h is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    if "config" in msg:
        if not connection.user.is_admin:
            connection.send_error(msg["id"], "unauthorized", "Nur Administratoren")
            return
        await h.async_save_cfg(msg["config"])
    elif msg["run"]:
        h._last_ctrl = 0  # noqa: SLF001
        await h.async_tick()
    if msg["settings"]:
        await h.async_request_settings()
    if "find" in msg:
        if not connection.user.is_admin:
            connection.send_error(msg["id"], "unauthorized", "Nur Administratoren")
            return
        await h.async_find_web(msg["find"].strip() or None)
    connection.send_result(msg["id"], h.snapshot())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/heater_dev",
        # {namespace: {key: value}} – schreibt Einstellungen direkt in den Heizstab
        vol.Optional("set"): {str: {str: vol.Any(str, int, float, bool)}},
    }
)
@websocket_api.require_admin
@websocket_api.async_response
@_report_errors
async def ws_heater_dev(hass: HomeAssistant, connection, msg) -> None:
    """Einstellungen des novolto lesen/schreiben – über die Weboberfläche, sonst über MQTT. Nur Administratoren."""
    import time as _time

    import aiohttp
    from homeassistant.components.network import async_get_source_ip

    from .heater import _mask, async_web_get_settings, async_web_set_settings, get as _get_heater

    h = _get_heater(hass)
    host = (h.cfg.get("host") if h is not None else "") or ""
    try:
        ha_ip = await async_get_source_ip(hass)
    except Exception:  # noqa: BLE001
        ha_ip = ""
    out: dict = {"host": host, "ha_ip": ha_ip, "settings": None, "error": None, "saved": False, "via": None}
    mqtt_ok = bool(h is not None and h.mqtt_ok and h.cfg.get("topic") and h._reachable())  # noqa: SLF001
    # Weboberfläche kürzlich nicht erreichbar (z. B. gesperrt im MQTT-Betrieb) → gleich MQTT nehmen
    use_mqtt = mqtt_ok and (not host or _time.time() - h.web_fail_t < 120)
    if not host and not mqtt_ok:
        out["error"] = "Weder Web-Adresse noch MQTT-Verbindung – zuerst „Gerät finden“."
        connection.send_result(msg["id"], out)
        return
    net_err = (TimeoutError, aiohttp.ClientError, OSError)
    if msg.get("set"):
        safe = _mask(msg["set"])
        done = False
        if not use_mqtt:
            try:
                await async_web_set_settings(hass, host, msg["set"])
                done = True
                h and h._log("web", f"→ http://{host}/api/unstable/settings/set gespeichert", detail=safe)  # noqa: SLF001
            except net_err as err:
                h.web_fail_t = _time.time() if h else 0
                if not mqtt_ok:
                    out["error"] = f"Nicht gespeichert: Weboberfläche antwortet nicht ({type(err).__name__})"
                use_mqtt = mqtt_ok
            except Exception as err:  # noqa: BLE001 – vom Gerät/der Prüfung abgelehnt
                out["error"] = f"Nicht gespeichert: {err}"
                h and h._log("web", f"→ settings/set abgelehnt: {err}", "ERROR", detail=safe)  # noqa: SLF001
        if not done and use_mqtt and not out["error"]:
            try:
                await h.async_mqtt_set_settings(msg["set"])
                done = True
                h._log("send", "Einstellungen über MQTT gespeichert", detail=safe)  # noqa: SLF001
            except Exception as err:  # noqa: BLE001
                out["error"] = f"Nicht gespeichert (MQTT): {err}"
        out["saved"] = done
    if not use_mqtt:
        try:
            out["settings"] = await async_web_get_settings(hass, host)
            out["via"] = "web"
            h and h._log("web", f"← http://{host}/api/unstable/settings/get gelesen", "DEBUG")  # noqa: SLF001
        except Exception as err:  # noqa: BLE001
            if h is not None:
                h.web_fail_t = _time.time()
                h._log("web", f"← http://{host}/api/unstable/settings/get: {type(err).__name__}: {err}", "WARNING")  # noqa: SLF001
            out["error_detail"] = f"{type(err).__name__}: {err}"
            use_mqtt = mqtt_ok
            if not mqtt_ok:
                out["error"] = out["error"] or f"Die Weboberfläche des Heizstabs unter {host} antwortet nicht."
    if use_mqtt and out["settings"] is None:
        try:
            out["settings"] = await h.async_mqtt_get_settings()
            out["via"] = "mqtt"
        except Exception as err:  # noqa: BLE001
            out["error"] = out["error"] or f"Einstellungen weder über die Weboberfläche noch über MQTT lesbar: {err}"
    connection.send_result(msg["id"], out)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/heater_log",
        vol.Optional("cat", default="all"): vol.In(["all", "send", "recv", "conn", "web", "ctrl"]),
        vol.Optional("since", default=0): int,
        vol.Optional("limit", default=500): vol.All(int, vol.Range(min=1, max=1500)),
        vol.Optional("action"): vol.In(["clear", "check"]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
@_report_errors
async def ws_heater_log(hass: HomeAssistant, connection, msg) -> None:
    """Heizstab-Log lesen; action=check prüft die Verbindung, action=clear leert das Log."""
    from .heater import get as _get_heater

    h = _get_heater(hass)
    if h is None:
        connection.send_error(msg["id"], "not_loaded", "Heizstab-Steuerung nicht geladen")
        return
    check = None
    if msg.get("action") == "clear":
        h.log.clear()
        h._log("conn", "Log geleert")  # noqa: SLF001
    elif msg.get("action") == "check":
        h._log("conn", "Verbindungsprüfung gestartet (von Hand)")  # noqa: SLF001
        check = await h.async_check(manual=True)
    connection.send_result(msg["id"], {
        "enabled": bool(h.cfg.get("log_enabled")),
        "entries": h.log_entries(msg["cat"], msg["limit"], msg["since"]),
        "last": h._log_seq,  # noqa: SLF001
        "check": check or h.last_check,
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/bsb",
        vol.Optional("config"): dict,
        vol.Optional("full", default=False): bool,
        # find: {"lan": bool, "host": str} – Adapter suchen (ESPHome, BSB-LAN)
        vol.Optional("find"): dict,
        vol.Optional("select"): str,
        # ESPHome-Adapter mit API-Schlüssel koppeln: {"host": "192.168.0.65", "key": "…="}
        vol.Optional("pair"): {vol.Required("host"): str, vol.Required("key"): str},
        vol.Optional("action"): vol.In(["identify", "scan_quick", "scan_full", "cancel", "poll", "clear_scan", "clear_unknown", "reconnect"]),
        # ID-Suchlauf: {"prefix": "2D3D", "start": "0500", "end": "06FF", "name": "..."}
        vol.Optional("probe"): {vol.Required("prefix"): str, vol.Required("start"): str, vol.Required("end"): str, vol.Optional("name"): str},
        # Anlernen: {"cmd": "053D0B26", "name": "...", "codec": "temp"}
        vol.Optional("learn"): {vol.Required("cmd"): str, vol.Required("name"): str, vol.Required("codec"): str, vol.Optional("ro", default=True): bool},
        vol.Optional("forget"): vol.Coerce(str),
        vol.Optional("lines"): [vol.Coerce(str)],
        vol.Optional("write"): {vol.Required("line"): vol.Coerce(str), vol.Required("value"): vol.Any(str, int, float)},
        vol.Optional("favorite"): {vol.Required("line"): vol.Coerce(str), vol.Required("on"): bool},
        # Zeitprogramme / Regler-Uhr (ab 1.20.0):
        #   {"op": "zp_set", "prog": "hk1", "tage": "01234", "phasen": "06:00-22:00"}
        #   {"op": "uhr_set", "zeit": ""}  (leer = Zeit von Home Assistant) · {"op": "auto", "on": true} · {"op": "read"}
        vol.Optional("zeit"): {
            vol.Required("op"): vol.In(["zp_set", "uhr_set", "auto", "read"]),
            vol.Optional("prog"): str, vol.Optional("tage"): str, vol.Optional("phasen", default=""): str,
            vol.Optional("zeit", default=""): str, vol.Optional("on"): bool,
        },
    }
)
@websocket_api.async_response
@_report_errors
async def ws_bsb(hass: HomeAssistant, connection, msg) -> None:
    """EMS-Regelung → ESP-Bus-Adapter: Zustand, Suche, Erkennung, Scan, Abfrage, Schreiben."""
    from . import bsb as _bsb

    ad = _bsb.get(hass)
    if ad is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    changing = any(k in msg for k in ("config", "find", "select", "pair", "action", "write", "favorite", "lines", "probe", "learn", "forget", "zeit"))
    if changing and not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Nur Administratoren")
        return
    await ad.async_catalog()
    out: dict[str, Any] = {}
    if "config" in msg:
        await ad.async_save_cfg(msg["config"])
    if "favorite" in msg:
        fav = [str(x) for x in ad.cfg["favorites"]]
        ln = msg["favorite"]["line"]
        fav = [x for x in fav if x != ln] + ([ln] if msg["favorite"]["on"] else [])
        await ad.async_save_cfg({"favorites": fav})
    if "find" in msg:
        await _bsb.async_find_into(ad, bool(msg["find"].get("lan")), (msg["find"].get("host") or "").strip() or None)
    if "select" in msg:
        await _bsb.async_select(ad, msg["select"])
    if "pair" in msg:
        out["pair"] = await _bsb.async_pair(ad, msg["pair"]["host"], msg["pair"]["key"])
    act = msg.get("action")
    if act == "identify":
        await ad.async_identify()
    elif act in ("scan_quick", "scan_full"):
        await ad.async_scan("quick" if act == "scan_quick" else "full")
    elif act == "cancel":
        ad.cancel()
    elif act == "clear_scan":
        ad.st.pop("scan", None)
        await ad.mgr.async_save()
    elif act == "reconnect":
        await ad.async_reconnect()
    elif act == "clear_unknown":
        ad.unknown.clear()
        ad.st.pop("unknown", None)
        await ad.mgr.async_save()
    elif act == "poll":
        out["values"] = await ad.query_lines([str(x) for x in ad.cfg["favorites"]])
    if "probe" in msg:
        pr = msg["probe"]
        await ad.async_probe(int(pr["prefix"], 16), int(pr["start"], 16), int(pr["end"], 16), pr.get("name", ""))
    if "learn" in msg:
        out["learned"] = await ad.async_learn(msg["learn"]["cmd"], msg["learn"]["name"], msg["learn"]["codec"], msg["learn"]["ro"])
    if "forget" in msg:
        await ad.async_forget(msg["forget"])
    if msg.get("lines"):
        out["values"] = await ad.query_lines(msg["lines"][:30])
    if "write" in msg:
        out["write"] = await ad.async_write(msg["write"]["line"], msg["write"]["value"])
    from . import bsb_zeit as _zeit

    if "zeit" in msg:
        z = msg["zeit"]
        try:
            if z["op"] == "zp_set":
                out["zeit_result"] = await _zeit.set_zp(ad, z.get("prog") or "", z.get("tage") or "", z.get("phasen") or "")
            elif z["op"] == "uhr_set":
                out["zeit_result"] = await _zeit.set_uhr(ad, z.get("zeit") or None)
            elif z["op"] == "auto":
                await _zeit.set_auto(ad, bool(z.get("on")))
            else:
                await _zeit.lesen(ad)
        except ValueError as err:
            connection.send_error(msg["id"], "invalid", str(err))
            return
    out["zeit"] = _zeit.snapshot(ad)
    from .const import VENDOR_BSB as _VB, entry_vendor as _ev

    entry = any(_ev(e) == _VB for e in hass.config_entries.async_entries(DOMAIN))
    connection.send_result(msg["id"], {**ad.snapshot(full=msg["full"]), **out, "entry": entry, "is_admin": connection.user.is_admin})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/bsb_fw",
        vol.Required("op"): vol.In(["list", "put", "install", "delete"]),
        vol.Optional("fid"): str,
        vol.Optional("filename", default=""): str,
        vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("total", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("data", default=""): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
@_report_errors
async def ws_bsb_fw(hass: HomeAssistant, connection, msg) -> None:
    """ESP-Bus-Adapter: Firmware-Datei hochladen (in Stücken), aufspielen (OTA), löschen."""
    from . import bsb as _bsb, esp_ota as _ota

    ad = _bsb.get(hass)
    if ad is None:
        connection.send_error(msg["id"], "not_loaded", "ESP-Bus-Adapter nicht geladen")
        return
    op, out = msg["op"], {}
    try:
        if op in ("put", "install", "delete") and not msg.get("fid"):
            raise _ota.OtaError("Datei-Kennung fehlt")
        if op == "put":
            out["put"] = await ad.async_fw_put(msg["fid"], msg["filename"], msg["offset"], msg["total"], msg["data"])
        elif op == "install":
            await ad.async_fw_install(msg["fid"])
        elif op == "delete":
            await ad.async_fw_delete(msg["fid"])
        else:
            await ad.async_fw_reload()
    except _ota.OtaError as err:
        connection.send_error(msg["id"], "firmware", str(err))
        return
    connection.send_result(msg["id"], {**out, "fw": ad.fw_snapshot()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/bsb_log",
        vol.Optional("cat", default="all"): vol.In(["all", "rx", "tx", "inf", "err", "scan", "conn", "http"]),
        vol.Optional("since", default=0): int,
        vol.Optional("limit", default=500): vol.All(int, vol.Range(min=1, max=3000)),
        vol.Optional("action"): vol.In(["clear"]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
@_report_errors
async def ws_bsb_log(hass: HomeAssistant, connection, msg) -> None:
    """BSB-Log (Bus-Mitschnitt) lesen bzw. leeren."""
    from . import bsb as _bsb

    ad = _bsb.get(hass)
    if ad is None:
        connection.send_error(msg["id"], "not_loaded", "ESP-Bus-Adapter nicht geladen")
        return
    if msg.get("action") == "clear":
        ad.log.clear()
        ad.stats = {k: 0 for k in ad.stats}
        ad.participants.clear()
        ad._log("conn", "Log geleert", force=True)  # noqa: SLF001
    connection.send_result(msg["id"], {
        "enabled": bool(ad.cfg.get("log_enabled")),
        "entries": ad.log_entries(msg["cat"], msg["limit"], msg["since"]),
        "last": ad._log_seq,  # noqa: SLF001
        "stats": ad.stats,
        "stream": ad._log_unsub is not None,  # noqa: SLF001
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/save",
        vol.Optional("map"): {str: str},
        vol.Optional("hc_names"): {vol.Coerce(str): str},
        vol.Optional("show_cooling"): bool,
        vol.Optional("vendor", default=VENDOR_BOSCH): _VENDOR_SCHEMA,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
@_report_errors
async def ws_save(hass: HomeAssistant, connection, msg) -> None:
    """Save mapping and/or heating-circuit names (admin)."""
    if (mgr := _mgr_or_error(hass, connection, msg)) is None:
        return
    vd = mgr.vdata(msg["vendor"])
    if "map" in msg:
        bad = [
            f"{k} → {v}"
            for k, v in msg["map"].items()
            if not (_RE_ENTITY.match(k) and _RE_ENTITY.match(v))
        ]
        if bad:
            connection.send_error(msg["id"], "invalid_format", "Ungültig: " + ", ".join(bad[:3]))
            return
        vd["map"] = {k: v for k, v in msg["map"].items() if k != v}
    if "hc_names" in msg:
        vd["hc_names"] = {
            k: v.strip()[:20] for k, v in msg["hc_names"].items() if v.strip()
        }
    if "show_cooling" in msg:
        vd["show_cooling"] = msg["show_cooling"]
    await mgr.async_save()
    connection.send_result(msg["id"], {"map": vd["map"], "hc_names": vd["hc_names"]})


# ---------------------------------------------------------------- v1.22.4: laufende Aufzeichnungen
_LOG_KEYS = ("bus", "ehs", "heater", "bsb")


def logs_active(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Welche Log-Aufzeichnungen gerade laufen (für den Hinweis unter „System & Panel“)."""
    out: list[dict[str, Any]] = []
    try:
        from .buslog import _logs as _bus_logs

        if any(log.recording for log in _bus_logs(hass)):
            out.append({"key": "bus", "label": "EMS-ESP Bus-Log", "hash": "bosch/log"})
    except Exception:  # noqa: BLE001
        _LOGGER.debug("bus log state", exc_info=True)
    try:
        from .ehs import _buffer

        buf = _buffer(hass)
        if buf is not None and buf.enabled:
            out.append({"key": "ehs", "label": "EHS-Sentinel-Log (Samsung)", "hash": "verbindung"})
    except Exception:  # noqa: BLE001
        _LOGGER.debug("ehs log state", exc_info=True)
    from .heater import get as _get_heater

    h = _get_heater(hass)
    if h is not None and h.cfg.get("log_enabled"):
        out.append({"key": "heater", "label": "Heizstab-Log", "hash": "heizstablog"})
    try:
        from .bsb import get as _get_bsb

        ad = _get_bsb(hass)
        if ad is not None and ad.cfg.get("log_enabled"):
            out.append({"key": "bsb", "label": "ESP-Bus-Adapter: Bus-Mitschnitt", "hash": "bsb"})
    except Exception:  # noqa: BLE001
        _LOGGER.debug("bsb log state", exc_info=True)
    return out


async def async_log_off(hass: HomeAssistant, key: str) -> None:
    if key in ("bus", "all"):
        from .buslog import _logs as _bus_logs, async_set_log_flag

        await async_set_log_flag(hass, "bus", False)
        for log in _bus_logs(hass):
            log.set_recording(False)
    if key in ("ehs", "all"):
        from .buslog import async_set_log_flag
        from .ehs import _buffer

        await async_set_log_flag(hass, "ehs", False)
        buf = _buffer(hass)
        if buf is not None and buf.enabled:
            buf.add_marker("Aufzeichnung ausgeschaltet")
            buf.enabled = False
    if key in ("heater", "all"):
        from .heater import get as _get_heater

        h = _get_heater(hass)
        if h is not None and h.cfg.get("log_enabled"):
            await h.async_save_cfg({"log_enabled": False})
    if key in ("bsb", "all"):
        from .bsb import get as _get_bsb

        ad = _get_bsb(hass)
        if ad is not None and ad.cfg.get("log_enabled"):
            await ad.async_save_cfg({"log_enabled": False})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/logs",
        vol.Optional("off"): vol.In(_LOG_KEYS + ("all",)),
    }
)
@websocket_api.async_response
@_report_errors
async def ws_logs_active(hass: HomeAssistant, connection, msg) -> None:
    """Laufende Aufzeichnungen abfragen – optional eine (oder alle) ausschalten (nur Admin)."""
    if msg.get("off"):
        if not connection.user.is_admin:
            connection.send_error(msg["id"], "unauthorized", "Nur für Administratoren")
            return
        await async_log_off(hass, msg["off"])
    connection.send_result(msg["id"], {"active": logs_active(hass), "is_admin": connection.user.is_admin})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/reset",
        vol.Required("kind"): vol.In(["error", "maintenance"]),
    }
)
@websocket_api.async_response
@_report_errors
async def ws_reset(hass: HomeAssistant, connection, msg) -> None:
    """Reset fault / maintenance message via EMS-ESP command boiler/reset."""
    from homeassistant.config_entries import ConfigEntryState

    entries = [
        e for e in ems_entries(hass)
        if e.state is ConfigEntryState.LOADED
    ]
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "Kein EMS-ESP erreichbar")
        return
    _LOGGER.info(
        "Panel: boiler/reset %s by %s", msg["kind"], connection.user.name
    )
    await entries[0].runtime_data.api.command("boiler", "reset", msg["kind"])
    await entries[0].runtime_data.status.async_request_refresh()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/info",
        vol.Optional("vendor", default=VENDOR_BOSCH): _VENDOR_SCHEMA,
    }
)
@websocket_api.async_response
@_report_errors
async def ws_info(hass: HomeAssistant, connection, msg) -> None:
    """Version info; kiosk link only for admins."""
    if (mgr := _mgr_or_error(hass, connection, msg)) is None:
        return
    info = await hass.async_add_executor_job(mgr.info)
    info["is_admin"] = connection.user.is_admin
    info["mapped"] = len(mgr.vdata(msg["vendor"])["map"])
    info["vendors"] = mgr.vendors
    if connection.user.is_admin:
        info["token_set"] = bool(mgr.token)
        info["kiosk_url"] = mgr.kiosk_url(msg["vendor"]) if mgr.token else None
    connection.send_result(msg["id"], info)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/upload",
        vol.Required("content"): str,
        vol.Optional("filename", default="ems-panel.html"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
@_report_errors
async def ws_upload(hass: HomeAssistant, connection, msg) -> None:
    """Check and activate a new panel file (admin)."""
    if (mgr := _mgr_or_error(hass, connection, msg)) is None:
        return
    html: str = msg["content"]
    if problems := validate_panel(html):
        connection.send_error(msg["id"], "invalid_panel", "; ".join(problems))
        return
    html, had_token = strip_token(html)
    filename = Path(msg["filename"]).name[:80]
    info = await hass.async_add_executor_job(mgr.store_upload, html, filename)
    info["token_removed"] = had_token
    _LOGGER.info("Panel updated to %s (%s)", info.get("version"), filename)
    connection.send_result(msg["id"], info)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/panel/restore"})
@websocket_api.require_admin
@websocket_api.async_response
@_report_errors
async def ws_restore(hass: HomeAssistant, connection, msg) -> None:
    """Go back to the backed-up panel (admin)."""
    if (mgr := _mgr_or_error(hass, connection, msg)) is None:
        return
    try:
        info = await hass.async_add_executor_job(mgr.restore_backup)
    except FileNotFoundError as err:
        connection.send_error(msg["id"], "no_backup", str(err))
        return
    connection.send_result(msg["id"], info)


# =====================================================================
# setup / sidebar
# =====================================================================
async def async_setup_panel(
    hass: HomeAssistant, options: dict[str, Any], vendor: str = VENDOR_BOSCH
) -> PanelManager:
    """Register views, websocket commands and the sidebar entry (idempotent).

    One panel file serves every manufacturer: Bosch at /bosch-ehs, Samsung
    at /samsung-ehs (?vendor=samsung). Heizstab and storage are shared.
    """
    # v1.24.0: der Manager entsteht schon in async_setup der Integration (async_setup_core).
    # Die Sperre bleibt als Absicherung, falls ein Eintrag nach dem Entfernen aller
    # Geräte neu eingerichtet wird.
    mgr = await async_setup_core(hass)
    mgr.vendor_options[vendor] = dict(options)
    _register_sidebar(hass, options, vendor)
    if vendor == VENDOR_BOSCH:
        # Warmwasseroptimierung works on EMS-ESP values only
        from .dhw_optimizer import async_setup as _setup_dhw_opt

        _setup_dhw_opt(hass, mgr)
        from .power_limit import async_setup as _setup_plim

        _setup_plim(hass, mgr)
    from .heater import async_setup as _setup_heater

    _setup_heater(hass, mgr)
    try:
        from .bsb import async_setup as _setup_bsb

        _setup_bsb(hass, mgr)
    except Exception:  # noqa: BLE001 - BSB darf das Panel nie verhindern
        _LOGGER.exception("ESP-Bus-Adapter konnte nicht gestartet werden")
    return mgr


async def async_setup_core(hass: HomeAssistant) -> PanelManager:
    """Gemeinsamen Kern (PanelManager, Speicher, Views, Websocket) genau einmal anlegen.

    v1.23.1: Sperre – Samsung, Heizstab und ESP-Bus-Adapter werden beim HA-Start parallel
    eingerichtet; ohne Sperre legte jeder Eintrag während ``async_load`` einen eigenen
    PanelManager an und der letzte überschrieb die anderen.
    v1.24.0: wird bereits aus ``async_setup`` der Integration aufgerufen – vor allen Einträgen.
    """
    if (mgr := _get(hass)) is not None:
        return mgr
    lock: asyncio.Lock = hass.data.setdefault(f"{_DATA}_lock", asyncio.Lock())
    async with lock:
        return await _async_get_or_create(hass)


async def _async_get_or_create(hass: HomeAssistant) -> PanelManager:
    """Gemeinsamen PanelManager holen bzw. einmalig anlegen (nur unter der Sperre aufrufen)."""
    mgr = _get(hass)
    if mgr is not None:
        return mgr
    mgr = PanelManager(hass)
    await mgr.async_load()
    hass.data[_DATA] = mgr
    if not hass.data.get(f"{_DATA}_http"):
        hass.http.register_view(PanelView())
        hass.http.register_view(PanelAdminView())
        for cmd in (ws_get, ws_check, ws_create, ws_uncreate, ws_dhwopt, ws_plim, ws_heater, ws_heater_dev, ws_heater_log, ws_bsb, ws_bsb_log, ws_bsb_fw, ws_save, ws_info, ws_upload, ws_restore, ws_reset, ws_logs_active):
            websocket_api.async_register_command(hass, cmd)
        hass.data[f"{_DATA}_http"] = True
    return mgr


@callback
def _register_sidebar(
    hass: HomeAssistant, options: dict[str, Any], vendor: str = VENDOR_BOSCH
) -> None:
    title, icon, path, url = _SIDEBAR[vendor]
    show = options.get(CONF_PANEL_SHOW_IN_SIDEBAR, True)
    if vendor == VENDOR_BSB:
        # v1.15.4: kein eigener Eintrag „ESP-Bus-Adapter“ in der Seitenleiste – der Adapter wird im
        # EMS-ESP Manager eingerichtet; bedient wird über das Dashboard „<Hersteller> EHS“ (/ems-bsb)
        show = False
    if vendor == VENDOR_HEATER:
        # v1.13.24: kein eigener Eintrag „Heizstab“ in der Seitenleiste – Bedienung über
        # EMS-EHS → Heizstab bzw. das Wärmepumpen-Panel; /heizstab bleibt als Adresse erreichbar
        show = False
    try:
        frontend.async_register_built_in_panel(
            hass,
            "iframe",
            sidebar_title=title if show else None,
            sidebar_icon=icon if show else None,
            frontend_url_path=path,
            config={"url": url},
            require_admin=options.get(CONF_PANEL_ADMIN_ONLY, False),
            update=True,
        )
    except Exception:  # noqa: BLE001 - never break the integration because of the UI
        _LOGGER.exception("Could not register the %s panel", title)
    if vendor in _OLD_URLS:
        # frühere Adressen (/samsung-hmi, /bsb-adapter, /sar-adapter) – Lesezeichen laufen weiter
        for old in _OLD_URLS[vendor]:
            try:
                frontend.async_register_built_in_panel(
                    hass, "iframe", sidebar_title=None, sidebar_icon=None,
                    frontend_url_path=old, config={"url": url},
                    require_admin=options.get(CONF_PANEL_ADMIN_ONLY, False), update=True,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Alte Panel-Adresse %s nicht registriert", old, exc_info=True)


@callback
def _remove_sidebar(hass: HomeAssistant, vendor: str) -> None:
    if vendor in _OLD_URLS:
        for old in _OLD_URLS[vendor]:
            try:
                frontend.async_remove_panel(hass, old)
            except Exception:  # noqa: BLE001
                pass
    try:
        frontend.async_remove_panel(hass, _SIDEBAR[vendor][2])
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Removing panel failed", exc_info=True)


@callback
def async_remove_panel(hass: HomeAssistant, vendor: str | None = None) -> None:
    """Remove a sidebar entry (views stay registered, they answer 404).

    With ``vendor`` only that manufacturer's entry goes; the rest (Heizstab,
    storage) stops when no manufacturer is left.
    """
    mgr = _get(hass)
    if vendor is not None and mgr is not None:
        if vendor in mgr.vendor_options:
            mgr.vendor_options.pop(vendor, None)
            _remove_sidebar(hass, vendor)
        if vendor == VENDOR_BOSCH:
            from .dhw_optimizer import _DATA as _DHW

            if (opt := hass.data.pop(_DHW, None)) is not None:
                opt.stop()
            from .power_limit import _DATA as _PLIM

            if (pl := hass.data.pop(_PLIM, None)) is not None:
                pl.stop()
        if mgr.vendors:
            return
    from .dhw_optimizer import _DATA as _DHW

    if (opt := hass.data.pop(_DHW, None)) is not None:
        opt.stop()
    from .power_limit import _DATA as _PLIM

    if (pl := hass.data.pop(_PLIM, None)) is not None:
        pl.stop()
    from .heater import _DATA as _HEATER

    if (heater := hass.data.pop(_HEATER, None)) is not None:
        heater.stop()
    from .bsb import _DATA as _BSB

    if (bsb_ad := hass.data.pop(_BSB, None)) is not None:
        bsb_ad.stop()
    if (mgr := hass.data.pop(_DATA, None)) is None:
        return
    for v in (VENDORS if vendor is None else [*mgr.vendors, vendor]):
        _remove_sidebar(hass, v)


def get_manager(hass: HomeAssistant) -> PanelManager | None:
    return _get(hass)


# =====================================================================
# Admin card (iframe in the EMS-ESP dashboard)
# =====================================================================
ADMIN_PAGE = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panel – Version</title>
<style>
:root{color-scheme:light dark;--fg:#212121;--dim:#727272;--bg:transparent;--line:#e0e0e0;--acc:#03a9f4;--err:#db4437;--ok:#43a047}
@media (prefers-color-scheme:dark){:root{--fg:#e1e1e1;--dim:#9b9b9b;--line:#3a3a3a}}
html,body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 Roboto,"Segoe UI",Arial,sans-serif}
body{padding:2px 14px 8px}
.row{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid var(--line)}
.row span:first-child{color:var(--dim)}
.btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
button{font:inherit;font-weight:500;border:0;border-radius:16px;padding:6px 12px;font-size:13px;cursor:pointer;background:var(--acc);color:#fff}
button.sec{background:transparent;color:var(--acc);border:1px solid var(--acc)}
button:disabled{opacity:.5;cursor:default}
html{-webkit-text-size-adjust:100%}
@media (pointer:coarse){button{min-height:42px;padding:8px 16px;font-size:15px;border-radius:21px}input,select{font-size:16px}}
#msg{margin-top:10px;min-height:1.2em}
.err{color:var(--err)} .ok{color:var(--ok)}
code{font-size:12px;word-break:break-all;user-select:all}
.hint{color:var(--dim);font-size:13px;margin-top:6px}
a{color:var(--acc);text-decoration:none}
#kv{padding:6px 0;border-bottom:1px solid var(--line)}
</style></head><body>
<div id="info">Lade …</div>
<div class="btns" id="btns" hidden>
  <button id="up">Neue Version hochladen</button>
  <button id="rs" class="sec">Sicherung wiederherstellen</button>
  <button id="op" class="sec">Panel öffnen</button>
</div>
<div id="ask" hidden><div id="askt" style="margin-top:12px"></div><div class="btns"><button id="yes">Übernehmen</button><button id="no" class="sec">Abbrechen</button></div></div>
<input id="file" type="file" accept=".html,.htm,text/html" hidden>
<div id="msg"></div>
<script>
const $=s=>document.querySelector(s);
let hass=null;
function H(){ for(const w of [window.parent, window.parent&&window.parent.parent, window.top]){ try{ const ha=w&&w!==window&&w.document.querySelector('home-assistant'); if(ha&&ha.hass) return ha.hass; }catch(e){} } return null; }
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const msg=(t,c)=>{ $('#msg').className=c||''; $('#msg').textContent=t; };
const ws=m=>hass.connection.sendMessagePromise(m);
const dt=s=>s?new Date(s).toLocaleString('de-DE',{dateStyle:'short',timeStyle:'short'}):'';
function show(i){
  const src=i.source==='uploaded'?'hochgeladen'+(i.uploaded&&i.uploaded.date?' am '+dt(i.uploaded.date):''):'mitgeliefert';
  let h=`<div class="row"><span>Panel-Version</span><b>${esc(i.version||'ohne Nummer')}</b></div>
   <div class="row"><span>Quelle</span><span>${esc(src)}${i.uploaded&&i.uploaded.filename?' · '+esc(i.uploaded.filename):''}</span></div>
   <div class="row"><span>Mitgeliefert mit dem Manager</span><span>${esc(i.bundled_version||'–')}</span></div>
   <div class="row"><span>Sicherung</span><span>${i.backup?esc((i.backup.version||'ohne Nummer')+(i.backup.date?' · '+dt(i.backup.date):'')):'keine'}</span></div>
   <div class="row"><span>Eigene Zuordnungen</span><span>${i.mapped}</span></div>`;
  if(i.is_admin){
    h+= i.kiosk_url
      ? `<div class="row"><span>Zugriff von außen (Kiosk)</span><a href="#" id="ks">Adresse anzeigen</a></div>
         <div id="kv" hidden><code>${esc(i.kiosk_url)}</code><div class="hint">Enthält einen geheimen Schlüssel – nur auf eigenen Geräten verwenden. Neuer Schlüssel: EMS-ESP Manager → Konfigurieren.</div></div>`
      : `<div class="row"><span>Zugriff von außen (Kiosk)</span><span>kein Token hinterlegt</span></div><div class="hint">Token in HA unter Profil → Sicherheit → Langlebige Zugriffstoken erstellen und unter EMS-ESP Manager → Konfigurieren einfügen. Danach erscheint hier die Adresse.</div>`;
  }
  $('#info').innerHTML=h;
  const ks=$('#ks'); if(ks) ks.onclick=e=>{ e.preventDefault(); const v=$('#kv'); v.hidden=!v.hidden; ks.textContent=v.hidden?'Adresse anzeigen':'ausblenden'; };
  $('#btns').hidden=false;
  $('#up').disabled=$('#rs').disabled=!i.is_admin;
  $('#rs').hidden=!i.backup;
  if(!i.is_admin) msg('Hochladen nur für Administratoren.');
}
const VENDOR=new URLSearchParams(location.search).get('vendor')||'bosch';
const PANEL_PATH={bosch:'/bosch-ehs',samsung:'/samsung-ehs',heater:'/heizstab',bsb:'/esp-bus-adapter'}[VENDOR]||'/bosch-ehs';
async function load(){ try{ show(await ws(Object.assign({type:'ems_esp_manager/panel/info'}, VENDOR!=='bosch'?{vendor:VENDOR}:{}))); }catch(e){ $('#info').innerHTML='<span class="err">'+esc(e&&e.message||e)+'</span>'; } }
$('#up').onclick=()=>$('#file').click();
$('#op').onclick=()=>{ try{ window.parent.history.pushState(null,'',PANEL_PATH); window.parent.dispatchEvent(new CustomEvent('location-changed')); }catch(e){ window.open(PANEL_PATH,'_top'); } };
let pending=null;
function ask(text,yes){ pending=yes; $('#ask').hidden=false; $('#askt').textContent=text; $('#btns').hidden=true; }
$('#no').onclick=()=>{ pending=null; $('#ask').hidden=true; $('#btns').hidden=false; msg(''); };
$('#yes').onclick=async()=>{ const f=pending; pending=null; $('#ask').hidden=true; $('#btns').hidden=false; if(f) await f(); };
$('#file').onchange=async ev=>{
  const f=ev.target.files[0]; ev.target.value=''; if(!f) return;
  if(f.size>3*1024*1024){ msg('Datei ist größer als 3 MB.','err'); return; }
  const text=await f.text();
  const m=text.match(/PANEL_VERSION\\s*=\\s*["']([^"']+)["']/);
  ask(`„${f.name}“ (${m?'Version '+m[1]:'ohne Versionsnummer'}) als Panel übernehmen? Die bisherige Version wird als Sicherung abgelegt, Zuordnung und Token bleiben erhalten.`, async()=>{
    msg('Prüfe und übernehme …');
    try{
      const r=await ws({type:'ems_esp_manager/panel/upload',content:text,filename:f.name});
      await load(); msg('Neue Version aktiv: '+(r.version||'ohne Nummer')+'. Geöffnete Panels bitte neu laden.'+(r.token_removed?' Der in der Datei eingetragene Token wurde entfernt (gehört in die Manager-Optionen).':''),'ok');
    }catch(e){ msg('Nicht übernommen: '+(e&&e.message||e),'err'); }
  });
};
$('#rs').onclick=()=>ask('Gesicherte Version wiederherstellen? Die aktuelle wird dabei zur Sicherung.', async()=>{
  try{ const r=await ws({type:'ems_esp_manager/panel/restore'}); await load(); msg('Sicherung wiederhergestellt: '+(r.version||'ohne Nummer'),'ok'); }
  catch(e){ msg(e&&e.message||String(e),'err'); }
});
(function wait(n){ hass=H(); if(hass&&hass.connection) return load(); if(n>50){ $('#info').innerHTML='<span class="err">Nur innerhalb von Home Assistant nutzbar.</span>'; return; } setTimeout(()=>wait(n+1),100); })(0);
</script></body></html>
"""
