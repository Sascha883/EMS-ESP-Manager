"""Config flow: choose the manufacturer, then set up EMS-ESP (Bosch) or Samsung EHS.

Bosch / Buderus: IP, user and password of the EMS-ESP gateway.
Samsung EHS: no connection data – the values come from the HACS integration
"EHS Sentinel", the manager only adds the panel (and the Heizstab control).
ESP-Bus-Adapter: ESPHome adapter (BSB-Atom) or BSB-LAN, found in Home Assistant / home network.
Heizstab (novolto): searched on the MQTT broker of Home Assistant (the rod is
connected by WLAN/LAN), also found automatically via MQTT/DHCP discovery.
"""

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
    CONF_VENDOR,
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    DOMAIN,
    EHS_DOMAIN,
    SAMSUNG_TITLE,
    SAMSUNG_UNIQUE_ID,
    CONF_HEATER_HOST,
    CONF_HEATER_TOPIC,
    HEATER_TITLE,
    VENDOR_BOSCH,
    VENDOR_HEATER,
    VENDOR_SAMSUNG,
    VENDOR_BSB,
    BSB_TITLE,
    BSB_UNIQUE_ID,
    entry_vendor,
)
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
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
        self._heaters: dict[str, dict[str, Any]] = {}
        self._found: dict[str, Any] | None = None
        self._dhcp_ip: str | None = None
        self._lan: list[dict[str, Any]] | None = None
        self._mqtt_ok: bool = False
        self._scanned: bool = False
        self._scan_lan: bool = False
        self._scan_host: str | None = None    # nur diese Adresse prüfen (Feld „Web-Adresse“)
        self._web_host: str = ""              # erkannte/eingegebene Startseite des Heizstabs
        self._scan_task: Any = None
        self._scan_finished: bool = False
        self._scan_log: list[str] = []
        self._scan_t0: float = 0.0
        self._notify_at: float = 0.0
        self._bsb_found: dict[str, dict[str, Any]] = {}
        self._bsb_scanned: bool = False
        self._bsb_lan: bool = False
        self._bsb_host: str | None = None
        self._bsb_finished: bool = False

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
        """First step: which heat pump / heating system?"""
        return self.async_show_menu(
            step_id="user",
            # Beschriftung direkt mitgeben – sichtbar auch ohne geladene Übersetzungen
            menu_options={
                VENDOR_BOSCH: "Bosch / Buderus (EMS-ESP)",
                VENDOR_SAMSUNG: "Samsung EHS (EHS Sentinel)",
                VENDOR_HEATER: "Heizstab (novolto) – im Netzwerk suchen",
                VENDOR_BSB: "ESP-Bus-Adapter (ESP/BSB-Atom oder BSB-LAN) – Brötje, Elco, Baxi …",
            },
        )

    # ------------------------------------------------------------- ESP-Bus-Adapter
    def _bsb_configured(self) -> bool:
        return any(entry_vendor(e) == VENDOR_BSB for e in self._async_current_entries())

    def _create_bsb(self, f: dict[str, Any]) -> ConfigFlowResult:
        from .bsb import config_from_found

        return self.async_create_entry(
            title=f"{BSB_TITLE} ({f.get('title') or f.get('host') or f.get('node')})",
            data={CONF_VENDOR: VENDOR_BSB, **config_from_found(f)},
        )

    async def _async_run_bsb_scan(self) -> None:
        import time

        from .bsb import find_adapters

        self._scan_t0 = time.monotonic()
        self._scan_log = []
        try:
            res = await find_adapters(self.hass, lan=self._bsb_lan, host=self._bsb_host, log=self._slog)
            self._bsb_found = {f["id"]: f for f in res}
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("ESP-Bus-Adapter-Suche fehlgeschlagen")
            self._slog(f"FEHLER: {type(err).__name__}: {err}")
        self._bsb_scanned = True

    async def async_step_bsb(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """ESP-Bus-Adapter suchen: ESPHome-Geräte, HA-Integration BSB-LAN, optional Heimnetz."""
        if self._bsb_configured():
            return self.async_abort(reason="bsb_configured")
        errors: dict[str, str] = {}
        if self._bsb_finished:
            self._bsb_finished = False
            user_input = None
        if user_input is not None:
            host = (user_input.get(FIELD_HOST) or "").strip()
            host = host.split("://")[-1].strip("/")
            if user_input.get(FIELD_RESCAN) or user_input.get(FIELD_LAN) or (host and not self._bsb_found.get(f"bsblan_{host}")):
                self._bsb_lan = bool(user_input.get(FIELD_LAN))
                self._bsb_host = host or None
                self._scan_task = None
                return await self.async_step_bsb_scan()
            sel = user_input.get(FIELD_DEVICE)
            if host:
                sel = f"bsblan_{host}"
            f = self._bsb_found.get(sel or "")
            if f is not None:
                await self.async_set_unique_id(BSB_UNIQUE_ID)
                self._abort_if_unique_id_configured()
                return self._create_bsb(f)
            errors["base"] = "bsb_none"
        elif not self._bsb_scanned:
            self._bsb_lan = False
            self._bsb_host = None
            self._scan_task = None
            return await self.async_step_bsb_scan()

        options = [
            SelectOptionDict(
                value=i,
                label=(f"ESPHome: {f['title']}" + (" · Aktion bsb_get ✓" if f.get("has_get") else " · Firmware ohne bsb_get") +
                       (" · online" if f.get("online") else " · offline"))
                if f["kind"] == "esphome" else f"BSB-LAN: {f.get('host')}" + (f" · {f['source']}" if f.get("source") else ""),
            )
            for i, f in self._bsb_found.items()
            if not f.get("protected") and not f.get("pair")
        ] or [SelectOptionDict(value=_NONE, label="Kein ESP-Bus-Adapter gefunden")]
        status = (
            f"✅ Gefunden: {len(options)} Adapter." if self._bsb_found
            else "Kein ESP-Bus-Adapter gefunden. ESPHome-Adapter zuerst in Home Assistant einbinden "
                 "(Einstellungen → Geräte & Dienste → ESPHome) oder BSB-LAN-Adresse eintragen."
        )
        if self._scan_log:
            status += (
                "\n\n<details><summary>Protokoll der Suche (" + str(len(self._scan_log)) + " Zeilen)</summary>\n\n"
                + self._log_text() + "\n\n</details>"
            )
        schema = vol.Schema(
            {
                vol.Optional(FIELD_DEVICE, default=options[0]["value"]): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                ),
                vol.Optional(FIELD_HOST): str,
                vol.Optional(FIELD_RESCAN, default=False): bool,
                vol.Optional(FIELD_LAN, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="bsb", data_schema=schema, errors=errors, description_placeholders={"status": status}
        )

    async def async_step_bsb_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._scan_task is None:
            self._bsb_scanned = False
            self._scan_log = []
            self._scan_task = self.hass.async_create_task(
                self._async_run_bsb_scan(), "ems_esp_manager bsb scan"
            )
        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="bsb_scan",
                progress_action="bsb_scan",
                description_placeholders={"log": self._log_text(last=25)},
                progress_task=self._scan_task,
            )
        self._scan_task = None
        self._bsb_finished = True
        return self.async_show_progress_done(next_step_id="bsb")

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Automatisch erkannter ESP-Bus-Adapter (ESPHome mit BSB-Werten oder BSB-LAN)."""
        if self._bsb_configured():
            return self.async_abort(reason="bsb_configured")
        await self.async_set_unique_id(BSB_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        f = dict(discovery_info)
        self._bsb_found = {f.get("id") or "found": f}
        self._found = f
        self.context["title_placeholders"] = {"host": f"ESP-Bus-Adapter {f.get('title') or f.get('host') or ''}".strip()}
        return await self.async_step_bsb_confirm()

    async def async_step_bsb_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        f = self._found or {}
        if user_input is not None:
            return self._create_bsb(f)
        return self.async_show_form(
            step_id="bsb_confirm",
            description_placeholders={
                "name": str(f.get("title") or f.get("host") or "?"),
                "kind": "ESPHome" if f.get("kind") == "esphome" else "BSB-LAN",
                "get": "ja" if f.get("has_get") else "nein – Firmware 1.2 für Regler-Erkennung und Scan aufspielen",
            },
        )

    # ------------------------------------------------------------- Heizstab
    def _heater_configured(self) -> bool:
        return any(entry_vendor(e) == VENDOR_HEATER for e in self._async_current_entries())

    def _create_heater(self, topic: str, serial: str | None = None, host: str = "") -> ConfigFlowResult:
        name = f"{HEATER_TITLE} novolto ({serial or topic or host})"
        return self.async_create_entry(
            title=name,
            data={CONF_VENDOR: VENDOR_HEATER, CONF_HEATER_TOPIC: topic, CONF_HEATER_HOST: host},
        )

    # Suche läuft im Hintergrund; der Dialog zeigt währenddessen das Protokoll (Fortschritt).
    def _slog(self, msg: str) -> None:
        import time

        t = time.monotonic() - self._scan_t0
        self._scan_log.append(f"{t:5.1f}s  {msg}")
        del self._scan_log[:-150]
        _LOGGER.info("Heizstab-Suche: %s", msg)
        self._notify()

    def _notify(self, force: bool = False) -> None:
        """Dialog neu laden lassen (höchstens 2× pro Sekunde)."""
        import time

        now = time.monotonic()
        if not force and now - self._notify_at < 0.5:
            return
        self._notify_at = now
        if (fn := getattr(self, "async_notify_flow_changed", None)) is not None:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def _sprogress(self, value: float) -> None:
        try:
            self.async_update_progress(max(0.0, min(1.0, value)))
        except Exception:  # noqa: BLE001
            pass

    async def _async_run_scan(self) -> None:
        import time

        from .heater import async_lan_scan, async_scan

        self._scan_t0 = time.monotonic()
        self._scan_log = []
        target = f"Adresse {self._scan_host}" if self._scan_host else "Heimnetz"
        self._slog("Suche gestartet" + (f" (MQTT + {target})" if self._scan_lan else " (MQTT)"))
        try:
            self._mqtt_ok, self._heaters = await async_scan(self.hass, log=self._slog)
            self._sprogress(0.2 if self._scan_lan else 1.0)
            if self._scan_lan:
                self._lan = await async_lan_scan(
                    self.hass,
                    hosts=[self._scan_host] if self._scan_host else None,
                    timeout=1.5 if self._scan_host else 0.7,
                    log=self._slog,
                    progress=lambda f: self._sprogress(0.2 + f),
                )
                novs = [h for h in self._lan if h.get("novolto")]
                if not novs and self._scan_host and self._lan and 80 in self._lan[0].get("ports", []):
                    novs = self._lan[:1]     # von Hand eingegebene Adresse mit Webseite übernehmen
                if novs:
                    self._web_host = novs[0]["ip"]
                self._slog(f"Netz: fertig – {len(self._lan)} Geräte, davon {len(novs)} novolto")
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Heizstab-Suche fehlgeschlagen")
            self._slog(f"FEHLER: {type(err).__name__}: {err}")
        self._slog("Suche beendet")
        self._scanned = True

    def _log_text(self, last: int | None = None) -> str:
        lines = self._scan_log[-last:] if last else self._scan_log
        return "```\n" + ("\n".join(lines) or "…") + "\n```"

    async def async_step_heater(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Heizstab im Netz suchen (MQTT-Broker von Home Assistant, optional Heimnetz)."""
        if self._heater_configured():
            return self.async_abort(reason="heater_configured")
        errors: dict[str, str] = {}
        if self._scan_finished:
            # Rückkehr aus der Suche: HA reicht die alte Eingabe weiter – nur das Ergebnis zeigen
            self._scan_finished = False
            user_input = None
        host_in = None
        if user_input is not None:
            from .heater import clean_host

            host_in = clean_host(user_input.get(FIELD_HOST))
            if host_in is None:
                errors[FIELD_HOST] = "invalid_host"
            elif host_in:
                self._web_host = host_in
        if user_input is not None and not errors and (user_input.get(FIELD_RESCAN) or user_input.get(FIELD_LAN)):
            self._scan_host = host_in or None
            self._scan_lan = bool(user_input.get(FIELD_LAN)) or bool(host_in)
            self._scan_task = None
            return await self.async_step_heater_scan()
        if user_input is not None and not errors:
            topic = (user_input.get(FIELD_TOPIC) or "").strip().strip("/")
            if not topic and user_input.get(FIELD_DEVICE) not in (None, "", _NONE):
                topic = user_input[FIELD_DEVICE]
            if topic:
                await self.async_set_unique_id(f"novolto_{topic.lower()}")
                self._abort_if_unique_id_configured()
                dev = (self._heaters or {}).get(topic) or {}
                return self._create_heater(topic, dev.get("serial"), self._web_host)
            if self._web_host:
                # noch kein MQTT – Gerät trotzdem anlegen; Base-Topic später im Panel wählen
                await self.async_set_unique_id(f"novolto_host_{self._web_host.lower()}")
                self._abort_if_unique_id_configured()
                return self._create_heater("", None, self._web_host)
            errors["base"] = "heater_none"
        elif not self._scanned:
            self._scan_lan = False
            self._scan_host = None
            self._scan_task = None
            return await self.async_step_heater_scan()

        options = [
            SelectOptionDict(
                value=t,
                label=f"{d.get('serial') or t}"
                + (f" · FW {d['fw']}" if d.get("fw") else "")
                + ("" if d.get("serial") == t else f" · Topic {t}"),
            )
            for t, d in sorted(self._heaters.items())
        ] or [SelectOptionDict(value=_NONE, label="Kein Heizstab gefunden")]
        if not self._mqtt_ok:
            status = (
                "⚠️ MQTT ist in Home Assistant nicht eingerichtet oder nicht verbunden – "
                "über MQTT kann der Heizstab nicht gesucht werden."
            )
        elif self._heaters:
            status = f"✅ Gefunden: {len(self._heaters)} Heizstab/Heizstäbe."
        else:
            status = "Kein Heizstab am MQTT-Broker gefunden."
        if self._dhcp_ip:
            status = f"Im Netzwerk gefunden: {self._dhcp_ip}. " + status
        if self._lan is not None:
            status += "\n\n" + _lan_summary(self._lan)
        if self._web_host:
            status += (
                f"\n\n🌐 Startseite des Heizstabs: [http://{self._web_host}/](http://{self._web_host}/) · "
                f"Experten-Seite: [http://{self._web_host}/expert](http://{self._web_host}/expert)"
            )
        if self._scan_log:
            status += (
                "\n\n<details><summary>Protokoll der Suche (" + str(len(self._scan_log)) + " Zeilen)</summary>\n\n"
                + self._log_text()
                + "\n\n</details>"
            )
        schema = vol.Schema(
            {
                vol.Optional(FIELD_DEVICE, default=options[0]["value"]): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                ),
                vol.Optional(FIELD_TOPIC): str,
                vol.Optional(FIELD_HOST, description={"suggested_value": self._web_host or None}): str,
                vol.Optional(FIELD_RESCAN, default=False): bool,
                vol.Optional(FIELD_LAN, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="heater",
            data_schema=schema,
            errors=errors,
            description_placeholders={"status": status},
        )

    async def async_step_heater_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fortschritt mit Live-Protokoll, bis die Suche fertig ist."""
        if self._scan_task is None:
            self._scanned = False
            self._scan_log = []
            self._scan_task = self.hass.async_create_task(
                self._async_run_scan(), "ems_esp_manager heater scan"
            )
        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="heater_scan",
                progress_action="heater_scan",
                description_placeholders={"log": self._log_text(last=25)},
                progress_task=self._scan_task,
            )
        self._scan_task = None
        self._scan_finished = True
        return self.async_show_progress_done(next_step_id="heater")

    async def async_step_mqtt(self, discovery_info) -> ConfigFlowResult:
        """novolto meldet sich am Broker: <Seriennummer>/device/info."""
        from .heater import parse_device_info

        dev = parse_device_info(discovery_info.topic, discovery_info.payload)
        if dev is None:
            return self.async_abort(reason="not_novolto")
        if self._heater_configured():
            return self.async_abort(reason="heater_configured")
        await self.async_set_unique_id(f"novolto_{dev['topic'].lower()}")
        self._abort_if_unique_id_configured()
        self._heaters = {dev["topic"]: dev}
        self._found = dev
        self.context["title_placeholders"] = {"host": f"Heizstab {dev['serial']}"}
        return await self.async_step_heater_confirm()

    async def async_step_heater_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        dev = self._found or {}
        if user_input is not None:
            return self._create_heater(dev["topic"], dev.get("serial"))
        return self.async_show_form(
            step_id="heater_confirm",
            description_placeholders={
                "serial": str(dev.get("serial") or "?"),
                "fw": str(dev.get("fw") or "?"),
                "topic": dev.get("topic", ""),
            },
        )

    async def async_step_dhcp(self, discovery_info) -> ConfigFlowResult:
        """Heizstab im WLAN/LAN (Hostname novolto-…) – Anbindung läuft über MQTT."""
        if self._heater_configured():
            return self.async_abort(reason="heater_configured")
        mac = str(getattr(discovery_info, "macaddress", "") or "").replace(":", "").lower()
        await self.async_set_unique_id(f"novolto_mac_{mac}" if mac else None)
        self._abort_if_unique_id_configured()
        self._dhcp_ip = getattr(discovery_info, "ip", None)
        self.context["title_placeholders"] = {"host": f"Heizstab {self._dhcp_ip or ''}".strip()}
        return await self.async_step_heater()

    async def async_step_samsung(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Samsung EHS: the panel uses the entities of EHS Sentinel."""
        await self.async_set_unique_id(SAMSUNG_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(
                title=SAMSUNG_TITLE, data={CONF_VENDOR: VENDOR_SAMSUNG}
            )
        ehs = self.hass.config_entries.async_entries(EHS_DOMAIN)
        if ehs:
            found = "Gefunden: " + ", ".join(e.title for e in ehs) + "."
        else:
            found = (
                "EHS Sentinel ist noch nicht eingerichtet. Du kannst trotzdem fortfahren – "
                "das Panel zeigt Werte, sobald EHS Sentinel (HACS) läuft."
            )
        return self.async_show_form(
            step_id="samsung",
            data_schema=vol.Schema({}),
            description_placeholders={"found": found},
        )

    async def async_step_bosch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bosch / Buderus: EMS-ESP gateway (IP, user, password)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = user_input[CONF_HOST].strip().rstrip("/")
            self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
            errors, status = await self._validate(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_HOST].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"EMS-ESP ({user_input[CONF_HOST]})",
                    data={**user_input, CONF_VENDOR: VENDOR_BOSCH},
                )
        defaults = user_input or ({CONF_HOST: self._host} if self._host else {})
        return self.async_show_form(
            step_id="bosch", data_schema=_schema(defaults), errors=errors
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
        self.context["title_placeholders"] = {"host": f"EMS-ESP ({host})"}
        return await self.async_step_bosch()

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
        if entry_vendor(entry) != VENDOR_BOSCH:
            return self.async_abort(reason="no_reconfigure")
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = user_input[CONF_HOST].strip().rstrip("/")
            errors, _ = await self._validate(user_input)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data={**user_input, CONF_VENDOR: VENDOR_BOSCH},
                    title=f"EMS-ESP ({user_input[CONF_HOST]})",
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or entry.data),
            errors=errors,
        )


FIELD_DEVICE = "device"
FIELD_TOPIC = "topic"
FIELD_RESCAN = "rescan"
FIELD_LAN = "lan"
FIELD_HOST = "host"


def _lan_summary(hosts: list[dict[str, Any]]) -> str:
    """Ergebnis der Heimnetz-Suche als Text für den Dialog."""
    nov = [h for h in hosts if h.get("novolto")]
    lines: list[str] = []
    if nov:
        for h in nov:
            name = f" ({h['hostname']})" if h.get("hostname") else ""
            web = (
                f"Web-Konfiguration im Heimnetz erreichbar: [http://{h['ip']}/expert](http://{h['ip']}/expert) – dort unter „mqtt“ den Broker eintragen (kein Hotspot nötig)"
                if h.get("expert")
                else ("Webseite erreichbar: http://" + h["ip"] if 80 in h["ports"] else "keine Webseite im Heimnetz")
            )
            mb = " · Modbus (Port 502) offen" if h.get("modbus") else ""
            lines.append(f"🔎 **novolto gefunden: {h['ip']}{name}** – {web}{mb}")
    elif not hosts:
        lines.append("🔎 Heimnetz durchsucht: kein Gerät mit Webseite oder Modbus gefunden (oder Suche nach 25 s abgebrochen).")
    else:
        lines.append(
            f"🔎 Heimnetz durchsucht: {len(hosts)} Geräte mit Webseite/Modbus, aber kein novolto erkannt. "
            "Adresse des Heizstabs aus der Geräteliste des Routers unter „Web-Adresse“ eintragen und „Erneut suchen“ anhaken."
        )
        mb = [h for h in hosts if h.get("modbus")]
        if mb:
            lines.append("Geräte mit Modbus (Port 502): " + ", ".join(
                h["ip"] + (f" ({h['hostname']})" if h.get("hostname") else "") for h in mb[:8]))
    return "\n\n".join(lines)
_NONE = "__none__"


# The field names are the visible labels, so the dialog is readable even if
# Home Assistant does not load the translation files of this integration.
TOKEN_HELP = (
    "Token in Home Assistant erstellen: Profil (unten links) → Sicherheit → "
    "Langlebige Zugriffstoken → Token erstellen. Den angezeigten langen Text "
    "(beginnt mit „eyJ…“) hier einfügen – nicht den Namen."
)

# Sichtbarkeit: eine Auswahl statt zwei Haken (Seitenleiste + nur Admin).
VIS_ALL = "all"
VIS_ADMIN = "admin"
VIS_HIDDEN = "hidden"
_VIS_OPTIONS = [
    SelectOptionDict(value=VIS_ALL, label="Für alle Benutzer in der Seitenleiste"),
    SelectOptionDict(value=VIS_ADMIN, label="Nur für Administratoren"),
    SelectOptionDict(value=VIS_HIDDEN, label="Nicht in der Seitenleiste (nur über die Adresse erreichbar)"),
]
_VIS_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=_VIS_OPTIONS, mode=SelectSelectorMode.LIST)
)

SEC_INSTALLER = "Fachbetrieb – Kontakt auf der Info-Seite (gilt für alle Geräte)"
SEC_KIOSK = "Wandtablet / Zugriff von außen (gilt für alle Geräte)"
FIELD_KIOSK_HELP = "So geht’s"
KIOSK_HELP = (
    "Nur nötig, wenn ein Panel ohne Anmeldung auf einem Tablet laufen soll.\n"
    "1. Home Assistant: Profil (unten links) → Sicherheit → Langlebige Zugriffstoken → Token erstellen.\n"
    "2. Den langen Text (beginnt mit eyJ…) unten einfügen und OK.\n"
    "3. Zahnrad erneut öffnen und die Kiosk-Adresse des Geräts kopieren."
)
FIELD_INST_NAME = "Firmenname"
FIELD_INST_ADDR = "Adresse"
FIELD_INST_TEL = "Telefon"
FIELD_TOKEN = "Zugriffstoken aus Home Assistant"
FIELD_KIOSK = "Kiosk-Adresse (zum Kopieren)"
FIELD_NEW_KEY = "Neuen Kiosk-Schlüssel erzeugen (alte Adresse wird ungültig)"

# Name der Seite (Dashboard) und des Panels je Hersteller
# Eigene Seite nur noch beim ESP-Bus-Adapter („Brötje EHS“). Bosch, Samsung und Heizstab
# stecken als Reiter in „EMS-EHS“ – deren Sichtbarkeit ist eine gemeinsame Einstellung (v1.22.0).
_DASH_NAME = {VENDOR_BSB: "Brötje EHS"}
FIELD_EHS = "Seite „EMS-EHS“ anzeigen (Übersicht mit Bosch, Samsung, Heizstab)"
_EHS_VENDORS = (VENDOR_BOSCH, VENDOR_SAMSUNG, VENDOR_HEATER)
_PANEL_NAME = {
    VENDOR_BOSCH: "Bosch EHS",
    VENDOR_SAMSUNG: "Samsung EHS",
    VENDOR_BSB: "ESP-Bus-Adapter",
    VENDOR_HEATER: "Heizstab",
}
_VENDOR_ORDER = (VENDOR_BOSCH, VENDOR_SAMSUNG, VENDOR_BSB, VENDOR_HEATER)
# Abschnitts-Namen = sichtbare Überschrift (HA zeigt sie auch ohne Übersetzungsdatei)
_SECTION_NAME = {
    VENDOR_BOSCH: "Bosch / Buderus (EMS-ESP)",
    VENDOR_SAMSUNG: "Samsung EHS",
    VENDOR_BSB: "ESP-Bus-Adapter (Brötje)",
    VENDOR_HEATER: "Heizstab (novolto)",
}
FIELD_ALL_KIOSK = "Kiosk-Adresse {name} (zum Kopieren)"


def _vis_get(show: bool, admin: bool) -> str:
    if not show:
        return VIS_HIDDEN
    return VIS_ADMIN if admin else VIS_ALL


def _vis_set(value: str, admin_before: bool) -> tuple[bool, bool]:
    """Auswahl -> (in Seitenleiste, nur Admin)."""
    if value == VIS_ADMIN:
        return True, True
    if value == VIS_HIDDEN:
        return False, admin_before
    return True, False


def _dash_field(vendor: str) -> str:
    return f"Seite „{_DASH_NAME.get(vendor, 'EMS-ESP')}“ anzeigen"


def _panel_field(vendor: str) -> str:
    return f"Panel „{_PANEL_NAME.get(vendor, 'Heizstab')}“ anzeigen"


def _devices(hass, current: ConfigEntry) -> list[tuple[str, ConfigEntry]]:
    """Alle Geräte des Managers als (Abschnitts-Schlüssel, Eintrag), feste Reihenfolge.

    Abschnitts-Schlüssel = sichtbarer Name, ab dem zweiten Gerät desselben Herstellers
    mit Nummer („Samsung EHS 2“ …)."""
    entries = list(hass.config_entries.async_entries(DOMAIN))
    if not any(e.entry_id == current.entry_id for e in entries):
        entries.append(current)
    out: list[tuple[str, ConfigEntry]] = []
    for v in _VENDOR_ORDER:
        same = [e for e in entries if entry_vendor(e) == v]
        for i, e in enumerate(same[:3]):
            name = _SECTION_NAME.get(v, v)
            out.append((name if i == 0 else f"{name} {i + 1}", e))
    return out


class EmsEspOptionsFlow(OptionsFlow):
    """'Konfigurieren' – ein gemeinsames Menü für alle Geräte des Managers.

    Egal an welchem Gerät das Zahnrad geklickt wird: es erscheinen alle Geräte
    (je ein aufklappbarer Abschnitt mit der Sichtbarkeit von Seite/Panel) sowie
    „Fachbetrieb“ und „Wandtablet“, die für alle Geräte gemeinsam gelten.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from homeassistant.data_entry_flow import section

        from .panel import get_manager

        mgr = get_manager(self.hass)
        current = self.config_entry
        devices = _devices(self.hass, current)
        all_entries = [e for _k, e in devices]

        def common(key: str) -> str:
            """Gemeinsamer Wert: zuerst dieses Gerät, sonst das erste mit Inhalt."""
            for e in [current, *all_entries]:
                val = (e.options.get(key) or "").strip()
                if val:
                    return val
            return ""

        ehs_first = next(
            (e for v in (VENDOR_SAMSUNG, VENDOR_BOSCH, VENDOR_HEATER)
             for _k, e in devices if entry_vendor(e) == v),
            None,
        )
        ehs_default = _vis_get(
            ehs_first.options.get(CONF_SHOW_IN_SIDEBAR, True), ehs_first.options.get(CONF_ADMIN_ONLY, False)
        ) if ehs_first else VIS_ALL

        errors: dict[str, str] = {}
        ui = user_input or {}
        inst = dict(ui.get(SEC_INSTALLER) or {})
        kiosk = dict(ui.get(SEC_KIOSK) or {})
        if user_input is not None:
            tok = (kiosk.get(FIELD_TOKEN) or "").strip()
            if tok:
                rt = self.hass.auth.async_validate_access_token(tok)
                if rt is None:
                    errors["base"] = "panel_token_invalid"
                elif rt.token_type != "long_lived_access_token":
                    errors["base"] = "panel_token_not_long_lived"

        if user_input is not None and not errors:
            if mgr is not None and kiosk.get(FIELD_NEW_KEY):
                await mgr.async_new_kiosk_key()
            shared = {
                CONF_PANEL_TOKEN: (kiosk.get(FIELD_TOKEN) or "").strip(),
                CONF_INSTALLER_NAME: (inst.get(FIELD_INST_NAME) or "").strip(),
                CONF_INSTALLER_ADDR: (inst.get(FIELD_INST_ADDR) or "").strip(),
                CONF_INSTALLER_TEL: (inst.get(FIELD_INST_TEL) or "").strip(),
            }
            ehs_entries = [e for _k, e in devices if entry_vendor(e) in _EHS_VENDORS]
            own: dict[str, Any] | None = None
            for key, e in devices:
                v = entry_vendor(e)
                vals = dict(ui.get(key) or {})
                opts = {**e.options, **shared}
                p_show, p_admin = _vis_set(
                    vals.get(_panel_field(v), _vis_get(
                        e.options.get(CONF_PANEL_SHOW_IN_SIDEBAR, True),
                        e.options.get(CONF_PANEL_ADMIN_ONLY, False))),
                    e.options.get(CONF_PANEL_ADMIN_ONLY, False),
                )
                opts[CONF_PANEL_SHOW_IN_SIDEBAR] = p_show
                opts[CONF_PANEL_ADMIN_ONLY] = p_admin
                if v in _DASH_NAME:
                    d_show, d_admin = _vis_set(
                        vals.get(_dash_field(v), _vis_get(
                            e.options.get(CONF_SHOW_IN_SIDEBAR, True),
                            e.options.get(CONF_ADMIN_ONLY, False))),
                        e.options.get(CONF_ADMIN_ONLY, False),
                    )
                    opts[CONF_SHOW_IN_SIDEBAR] = d_show
                    opts[CONF_ADMIN_ONLY] = d_admin
                if v in _EHS_VENDORS and ehs_entries:
                    e_show, e_admin = _vis_set(
                        ui.get(FIELD_EHS, ehs_default), bool(ehs_entries[0].options.get(CONF_ADMIN_ONLY, False))
                    )
                    opts[CONF_SHOW_IN_SIDEBAR] = e_show
                    opts[CONF_ADMIN_ONLY] = e_admin
                if e.entry_id == current.entry_id:
                    own = opts
                elif opts != dict(e.options):
                    # löst beim anderen Gerät dessen Update-Listener aus (Seitenleiste neu)
                    self.hass.config_entries.async_update_entry(e, options=opts)
            return self.async_create_entry(data=own or {**current.options, **shared})

        def sv(values: dict, key: str, default: str) -> dict:
            return {"suggested_value": values.get(key, default)}

        schema: dict[Any, Any] = {}
        if ehs_first is not None:
            schema[vol.Required(FIELD_EHS, default=ui.get(FIELD_EHS) or ehs_default)] = _VIS_SELECTOR
        for key, e in devices:
            v = entry_vendor(e)
            vals = dict(ui.get(key) or {})
            dev: dict[Any, Any] = {}
            if v in _DASH_NAME:
                dev[vol.Required(
                    _dash_field(v),
                    default=vals.get(_dash_field(v)) or _vis_get(
                        e.options.get(CONF_SHOW_IN_SIDEBAR, True), e.options.get(CONF_ADMIN_ONLY, False)
                    ),
                )] = _VIS_SELECTOR
            dev[vol.Required(
                _panel_field(v),
                default=vals.get(_panel_field(v)) or _vis_get(
                    e.options.get(CONF_PANEL_SHOW_IN_SIDEBAR, True), e.options.get(CONF_PANEL_ADMIN_ONLY, False)
                ),
            )] = _VIS_SELECTOR
            schema[vol.Required(key)] = section(
                vol.Schema(dev), {"collapsed": e.entry_id != current.entry_id}
            )

        schema[vol.Required(SEC_INSTALLER)] = section(
            vol.Schema({
                vol.Optional(FIELD_INST_NAME, description=sv(inst, FIELD_INST_NAME, common(CONF_INSTALLER_NAME))): str,
                vol.Optional(FIELD_INST_ADDR, description=sv(inst, FIELD_INST_ADDR, common(CONF_INSTALLER_ADDR))): str,
                vol.Optional(FIELD_INST_TEL, description=sv(inst, FIELD_INST_TEL, common(CONF_INSTALLER_TEL))): str,
            }),
            {"collapsed": True},
        )

        token_now = common(CONF_PANEL_TOKEN)
        kiosk_fields: dict[Any, Any] = {
            vol.Optional(FIELD_KIOSK_HELP, description={"suggested_value": KIOSK_HELP}):
                TextSelector(TextSelectorConfig(multiline=True, read_only=True)),
            vol.Optional(FIELD_TOKEN, description=sv(kiosk, FIELD_TOKEN, token_now)):
                TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        }
        if mgr is not None and token_now:
            seen: set[str] = set()
            for _key, e in devices:
                v = entry_vendor(e)
                if v in seen:
                    continue
                seen.add(v)
                kiosk_fields[vol.Optional(
                    FIELD_ALL_KIOSK.format(name=_PANEL_NAME.get(v, v)),
                    description={"suggested_value": mgr.kiosk_url(v)},
                )] = TextSelector(TextSelectorConfig(read_only=True))
            kiosk_fields[vol.Optional(FIELD_NEW_KEY, default=False)] = bool
        schema[vol.Required(SEC_KIOSK)] = section(
            vol.Schema(kiosk_fields), {"collapsed": not errors}
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_samsung(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_init(user_input)

    async def async_step_heater(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_init(user_input)

    async def async_step_bsb(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_init(user_input)
