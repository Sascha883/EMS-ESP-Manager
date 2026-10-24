"""Bosch HMI 800 panel: served by Home Assistant, mapping/token kept in HA.

- The panel HTML is shipped with the integration (panel/bosch-ui800.html).
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
    PANEL_ICON,
    PANEL_SIDEBAR_URL,
    PANEL_TITLE,
    SAMSUNG_PANEL_ICON,
    SAMSUNG_PANEL_TITLE,
    SAMSUNG_SIDEBAR_URL,
    URL_PANEL,
    URL_PANEL_ADMIN,
    VENDOR_BOSCH,
    VENDOR_SAMSUNG,
    VENDORS,
    ems_entries,
)

# sidebar entry per manufacturer – one panel file, the manager tells it the vendor
_SIDEBAR = {
    VENDOR_BOSCH: (PANEL_TITLE, PANEL_ICON, PANEL_SIDEBAR_URL, URL_PANEL),
    VENDOR_SAMSUNG: (
        SAMSUNG_PANEL_TITLE,
        SAMSUNG_PANEL_ICON,
        SAMSUNG_SIDEBAR_URL,
        f"{URL_PANEL}?vendor={VENDOR_SAMSUNG}",
    ),
}
_VENDOR_SCHEMA = vol.In(VENDORS)

_LOGGER = logging.getLogger(__name__)

_DATA = f"{DOMAIN}_panel"
_STORE_KEY = f"{DOMAIN}.panel"
_STORE_VERSION = 1

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
    """Return a list of problems (empty = file looks like the Bosch panel)."""
    problems: list[str] = []
    if len(html.encode("utf-8")) > MAX_SIZE:
        problems.append("Datei ist größer als 3 MB")
    low = html[:4000].lower()
    if "<html" not in low:
        problems.append("keine HTML-Datei")
    if "hmi 800" not in html.lower():
        problems.append("kein Bosch-HMI-800-Panel (Kennung fehlt)")
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
        self.data.setdefault("map", {})
        self.data.setdefault("hc_names", {})
        self.data.setdefault("vendors", {})
        if not self.data.get("kiosk_key"):
            self.data["kiosk_key"] = secrets.token_urlsafe(24)
            await self.store.async_save(self.data)

    async def async_save(self) -> None:
        await self.store.async_save(self.data)

    def vdata(self, vendor: str = VENDOR_BOSCH) -> dict[str, Any]:
        """Mapping, heating-circuit names … of one manufacturer.

        Bosch keeps the keys at the top level (as before v1.13), every other
        manufacturer has its own block under "vendors".
        """
        if vendor == VENDOR_BOSCH:
            return self.data
        d = self.data["vendors"].setdefault(vendor, {})
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
        extra = "" if vendor == VENDOR_BOSCH else f"&vendor={vendor}"
        return f"{base}{URL_PANEL}?key={self.kiosk_key}{extra}"

    def key_valid(self, key: str | None) -> bool:
        return bool(key) and hmac.compare_digest(key, self.kiosk_key)

    # ---------- files ----------
    @property
    def uploaded_path(self) -> Path:
        return self.dir / PANEL_FILE

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
    """GET /api/ems_esp_manager/panel – the Bosch panel itself.

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
            _LOGGER.exception("Bosch panel could not be read")
            return web.Response(status=500, text=f"Bosch-Panel: {type(err).__name__}: {err}")
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
            _LOGGER.exception("Bosch panel: %s failed", msg.get("type"))
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
            "map": mgr.data["map"],
            "auto": build_auto_map(hass),
            "ems_ids": await _ems_ids(hass),
            "devices": (scan or {}).get("devices") or await _ems_devices(hass),
            "check": {"entities": scan.get("entities"), "t": scan.get("t")} if scan and scan.get("entities") else None,
            "ha_ems": ems_ha_entities(hass),
            "ha_ems_off": ems_ha_entities(hass, disabled=True),
            "created": sorted((mgr.data.get("created") or {}).keys()),
            "dhw_opt": _dhw_opt_snapshot(hass),
            "heater": _heater_snapshot(hass),
            "show_cooling": mgr.data.get("show_cooling", True),
            "hc_names": mgr.data["hc_names"],
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
        vol.Required("type"): f"{DOMAIN}/panel/heater",
        vol.Optional("config"): dict,
        vol.Optional("run", default=False): bool,
        vol.Optional("settings", default=False): bool,
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
    connection.send_result(msg["id"], h.snapshot())


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
        "Bosch panel: boiler/reset %s by %s", msg["kind"], connection.user.name
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
        vol.Optional("filename", default="bosch-ui800.html"): str,
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
    _LOGGER.info("Bosch panel updated to %s (%s)", info.get("version"), filename)
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

    One panel file serves every manufacturer: Bosch at /bosch-hmi800, Samsung
    at /samsung-ehs (?vendor=samsung). Heizstab and storage are shared.
    """
    mgr = _get(hass)
    if mgr is None:
        mgr = PanelManager(hass)
        await mgr.async_load()
        hass.data[_DATA] = mgr
        if not hass.data.get(f"{_DATA}_http"):
            hass.http.register_view(PanelView())
            hass.http.register_view(PanelAdminView())
            for cmd in (ws_get, ws_check, ws_create, ws_uncreate, ws_dhwopt, ws_heater, ws_save, ws_info, ws_upload, ws_restore, ws_reset):
                websocket_api.async_register_command(hass, cmd)
            hass.data[f"{_DATA}_http"] = True
    mgr.vendor_options[vendor] = dict(options)
    _register_sidebar(hass, options, vendor)
    if vendor == VENDOR_BOSCH:
        # Warmwasseroptimierung works on EMS-ESP values only
        from .dhw_optimizer import async_setup as _setup_dhw_opt

        _setup_dhw_opt(hass, mgr)
    from .heater import async_setup as _setup_heater

    _setup_heater(hass, mgr)
    return mgr


@callback
def _register_sidebar(
    hass: HomeAssistant, options: dict[str, Any], vendor: str = VENDOR_BOSCH
) -> None:
    title, icon, path, url = _SIDEBAR[vendor]
    show = options.get(CONF_PANEL_SHOW_IN_SIDEBAR, True)
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


@callback
def _remove_sidebar(hass: HomeAssistant, vendor: str) -> None:
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
        if mgr.vendors:
            return
    from .dhw_optimizer import _DATA as _DHW

    if (opt := hass.data.pop(_DHW, None)) is not None:
        opt.stop()
    from .heater import _DATA as _HEATER

    if (heater := hass.data.pop(_HEATER, None)) is not None:
        heater.stop()
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
<title>Bosch-Panel – Version</title>
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
function H(){ try{ const ha=window.parent.document.querySelector('home-assistant'); return ha&&ha.hass; }catch(e){ return null; } }
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
async function load(){ try{ show(await ws({type:'ems_esp_manager/panel/info'})); }catch(e){ $('#info').innerHTML='<span class="err">'+esc(e&&e.message||e)+'</span>'; } }
$('#up').onclick=()=>$('#file').click();
$('#op').onclick=()=>{ try{ window.parent.history.pushState(null,'','/bosch-hmi800'); window.parent.dispatchEvent(new CustomEvent('location-changed')); }catch(e){ window.open('/bosch-hmi800','_top'); } };
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
