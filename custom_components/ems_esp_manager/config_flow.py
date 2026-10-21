"""Config flow: set up EMS-ESP by entering IP, user and password in the UI."""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import EmsEspApi, EmsEspAuthError, EmsEspError, token_is_admin
from .const import (
    CONF_ADMIN_ONLY,
    CONF_PANEL_ADMIN_ONLY,
    CONF_PANEL_SHOW_IN_SIDEBAR,
    CONF_PANEL_TOKEN,
    CONF_INSTALLER_ADDR,
    CONF_INSTALLER_NAME,
    CONF_INSTALLER_TEL,
    CONF_SHOW_IN_SIDEBAR,
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _norm_host(value: Any) -> str:
    """'http://EMS-ESP.local:80/' -> 'ems-esp.local' (for comparing hosts)."""
    h = str(value or "").strip().lower()
    if "://" in h:
        h = h.split("://", 1)[1]
    h = h.split("/", 1)[0].rstrip(".")
    if h.startswith("["):                      # [IPv6]:port
        return h[1:].split("]", 1)[0]
    if h.count(":") == 1:                      # host:port
        h = h.split(":", 1)[0]
    return h


def _resolve(host: str) -> set[str]:
    """All IPv4 addresses of a host name (blocking, run in executor)."""
    try:
        return {i[4][0] for i in socket.getaddrinfo(host, None, socket.AF_INET)}
    except OSError:
        return set()


def _schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)
            ): str,
            vol.Required(
                CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, DEFAULT_PASSWORD)
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        }
    )


class EmsEspConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup dialog."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EmsEspOptionsFlow()

    def __init__(self) -> None:
        self._host: str | None = None

    async def _validate(self, data: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
        """Try to sign in and read the status. Returns (errors, status)."""
        errors: dict[str, str] = {}
        api = EmsEspApi(
            async_get_clientsession(self.hass),
            data[CONF_HOST],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
        )
        try:
            token = await api.sign_in()
            if not token_is_admin(token):
                errors["base"] = "not_admin"
                return errors, {}
            status = await api.get_status()
        except EmsEspAuthError:
            errors["base"] = "invalid_auth"
            return errors, {}
        except EmsEspError:
            errors["base"] = "cannot_connect"
            return errors, {}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error")
            errors["base"] = "unknown"
            return errors, {}
        return errors, status

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = user_input[CONF_HOST].strip().rstrip("/")
            self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
            errors, status = await self._validate(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_HOST].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"EMS-ESP ({user_input[CONF_HOST]})", data=user_input
                )
        defaults = user_input or ({CONF_HOST: self._host} if self._host else {})
        return self.async_show_form(
            step_id="user", data_schema=_schema(defaults), errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """EMS-ESP announces itself as _http._tcp with hostname 'ems-esp'."""
        ip = discovery_info.ip_address
        if ip is None or ip.version != 4:
            # EMS-ESP meldet sich auch per IPv6 – nur IPv4 anbieten (sonst doppelt)
            return self.async_abort(reason="not_ipv4_address")
        host = str(ip)
        name = _norm_host(discovery_info.hostname)          # z. B. ems-esp.local
        known = {host, name, name.removesuffix(".local")} - {""}

        # Schon eingerichtet? Vergleich über IP, Hostname (mit/ohne .local,
        # http://, Port) und – falls ein Name eingetragen ist – dessen IP.
        for entry in self._async_current_entries(include_ignore=True):
            if entry.unique_id and _norm_host(entry.unique_id) in known:
                return self.async_abort(reason="already_configured")
            entry_host = _norm_host(entry.data.get(CONF_HOST))
            if not entry_host:
                continue
            if entry_host in known:
                return self.async_abort(reason="already_configured")
            try:
                ipaddress.ip_address(entry_host)
                continue                                 # andere IP
            except ValueError:
                pass
            if host in await self.hass.async_add_executor_job(_resolve, entry_host):
                return self.async_abort(reason="already_configured")

        # Last check: ask every configured gateway for its real IP address
        # (/rest/networkStatus local_ip). Catches entries set up with a host
        # name HA cannot resolve, a different spelling or a changed DHCP address.
        for entry in self._async_current_entries(include_ignore=False):
            rt = getattr(entry, "runtime_data", None)
            api = getattr(rt, "api", None)
            if api is None:
                continue
            try:
                net = await api.get_network_status()
            except Exception:  # noqa: BLE001 – gateway offline: keep checking the others
                continue
            if str(net.get("local_ip") or "") == host:
                return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(host)
        self._abort_if_unique_id_configured()
        self._host = host
        self.context["title_placeholders"] = {"host": host}
        return await self.async_step_user()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            errors, _ = await self._validate(data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
                    ): str,
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = user_input[CONF_HOST].strip().rstrip("/")
            errors, _ = await self._validate(user_input)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data=user_input,
                    title=f"EMS-ESP ({user_input[CONF_HOST]})",
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or entry.data),
            errors=errors,
        )


# The field names are the visible labels, so the dialog is readable even if
# Home Assistant does not load the translation files of this integration.
FIELD_SIDEBAR = "In Sidebar Benutzer anzeigen"
FIELD_ADMIN = "In Sidebar Admin anzeigen"
FIELD_PANEL_SIDEBAR = "Bosch HMI 800: in Sidebar Benutzer anzeigen"
FIELD_PANEL_ADMIN = "Bosch HMI 800: in Sidebar Admin anzeigen"
FIELD_TOKEN = "Bosch HMI 800: Token für Zugriff von außen (optional)"
FIELD_KIOSK = "Bosch HMI 800: Kiosk-Adresse (nur zum Kopieren)"
FIELD_INST_NAME = "Bosch HMI 800: Fachbetrieb – Name (Info-Seite)"
FIELD_INST_ADDR = "Bosch HMI 800: Fachbetrieb – Adresse"
FIELD_INST_TEL = "Bosch HMI 800: Fachbetrieb – Telefon"
FIELD_NEW_KEY = "Bosch HMI 800: neuen Kiosk-Schlüssel erzeugen (alte Adresse wird ungültig)"


class EmsEspOptionsFlow(OptionsFlow):
    """'Konfigurieren': sidebar entries, admin restriction, panel token."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from .panel import get_manager

        mgr = get_manager(self.hass)
        options = self.config_entry.options
        if user_input is not None:
            if mgr is not None and user_input.get(FIELD_NEW_KEY):
                await mgr.async_new_kiosk_key()
            return self.async_create_entry(
                data={
                    CONF_SHOW_IN_SIDEBAR: bool(user_input.get(FIELD_SIDEBAR, True)),
                    CONF_ADMIN_ONLY: bool(user_input.get(FIELD_ADMIN, False)),
                    CONF_PANEL_SHOW_IN_SIDEBAR: bool(user_input.get(FIELD_PANEL_SIDEBAR, True)),
                    CONF_PANEL_ADMIN_ONLY: bool(user_input.get(FIELD_PANEL_ADMIN, False)),
                    CONF_PANEL_TOKEN: (user_input.get(FIELD_TOKEN) or "").strip(),
                    CONF_INSTALLER_NAME: (user_input.get(FIELD_INST_NAME) or "").strip(),
                    CONF_INSTALLER_ADDR: (user_input.get(FIELD_INST_ADDR) or "").strip(),
                    CONF_INSTALLER_TEL: (user_input.get(FIELD_INST_TEL) or "").strip(),
                }
            )
        schema: dict[Any, Any] = {
            vol.Required(FIELD_SIDEBAR, default=options.get(CONF_SHOW_IN_SIDEBAR, True)): bool,
            vol.Required(FIELD_ADMIN, default=options.get(CONF_ADMIN_ONLY, False)): bool,
            vol.Required(
                FIELD_PANEL_SIDEBAR, default=options.get(CONF_PANEL_SHOW_IN_SIDEBAR, True)
            ): bool,
            vol.Required(
                FIELD_PANEL_ADMIN, default=options.get(CONF_PANEL_ADMIN_ONLY, False)
            ): bool,
            vol.Optional(
                FIELD_TOKEN,
                description={"suggested_value": options.get(CONF_PANEL_TOKEN, "")},
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Optional(
                FIELD_INST_NAME, description={"suggested_value": options.get(CONF_INSTALLER_NAME, "")}
            ): str,
            vol.Optional(
                FIELD_INST_ADDR, description={"suggested_value": options.get(CONF_INSTALLER_ADDR, "")}
            ): str,
            vol.Optional(
                FIELD_INST_TEL, description={"suggested_value": options.get(CONF_INSTALLER_TEL, "")}
            ): str,
        }
        if mgr is not None and options.get(CONF_PANEL_TOKEN):
            schema[
                vol.Optional(FIELD_KIOSK, description={"suggested_value": mgr.kiosk_url()})
            ] = TextSelector(TextSelectorConfig(read_only=True))
            schema[vol.Optional(FIELD_NEW_KEY, default=False)] = bool
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
