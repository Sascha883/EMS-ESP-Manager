"""Builds the EMS-ESP dashboard from the device and entity registry.

The dashboard is generated on every load, so new EMS devices (e.g. a mixer
module added later) show up automatically without any manual work.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .buslog import URL_LOG_CARD
from .mqtt import URL_MQTT_CARD
from .const import DOMAIN, URL_PANEL, URL_PANEL_ADMIN
from .names import STATES, lang

CONTROL_DOMAINS = ("climate", "water_heater", "number", "select", "switch", "text", "time", "button")
VALUE_DOMAINS = ("sensor", "binary_sensor")

TXT = {
    "de": {
        "overview": "System & Firmware",
        "settings": "Einstellungen",
        "values": "Messwerte",
        "webui": "Web-Oberfläche",
        "mqtt": "MQTT",
        "firmware": "Firmware",
        "system": "System",
        "heating": "Heizung & Warmwasser",
        "install": "Firmware installieren",
        "install_confirm": "EMS-ESP lädt die neue Firmware von GitHub, installiert sie und startet neu "
        "(ca. 1–3 Minuten). Die Heizung läuft dabei weiter. Fortfahren?",
        "restart_confirm": "EMS-ESP jetzt neu starten?",
        "versions": "Bestimmte Version installieren",
        "versions_list": "Verfügbare stabile Versionen",
        "col_version": "Version",
        "col_date": "Datum",
        "installed_mark": "installiert",
        "install_sel_confirm": "Die oben gewählte stabile Version wird installiert und der EMS-ESP startet neu "
        "(ca. 1–3 Minuten). Bei einer älteren Version (Downgrade) bitte danach die Einstellungen prüfen. Fortfahren?",
        "update_available": "Neue Firmware verfügbar",
        "installed": "Installiert",
        "latest": "Neu",
        "changes": "Änderungen ansehen",
        "no_devices": "Keine EMS-ESP-Geräte aus MQTT gefunden. Im EMS-ESP unter "
        "**Einstellungen → MQTT** die **Home Assistant Discovery** einschalten.",
        "gateway": "Gateway",
        "web_hint": "Wird Home Assistant über **https** aufgerufen (z. B. Nabu Casa), blockiert der "
        "Browser die eingebettete Seite. Dann hier öffnen:",
        "open": "EMS-ESP-Weboberfläche in neuem Tab öffnen",
        "log": "Bus-Log",
        "log_hint": "Mitschnitt der EMS-Bus-Kommunikation (gelesene und geschriebene Telegramme). "
        "Für Telegramme den **Bus-Mitschnitt** auf „Telegramme“ oder „Roh-Daten“ stellen. "
        "Im Dauerbetrieb wieder auf „Aus“ stellen. "
        "**Tx-Modus**: wie EMS-ESP sendet – eine Änderung startet EMS-ESP neu. "
        "Zeigt „EMS-Bus“ danach „Sendefehler“, anderen Modus probieren.",
        "log_empty": "Noch keine Zeilen. Bus-Mitschnitt einschalten und kurz warten.",
        "panel": "Bosch-Panel",
        "log_download": "Log herunterladen",
        "github_hint": "Die zuletzt geladene Liste bleibt erhalten; neu laden mit „Updates prüfen“.",
        "mapping": "Zuordnung",
        "mapping_hint": "Jede Panel-Funktion ist einer Entität zugeordnet. Die Standard-Namen von EMS-ESP "
        "werden automatisch erkannt, **fehlt** markiert nicht gefundene. Antippen zum Ändern – gespeichert "
        "wird in Home Assistant (Änderungen nur als Administrator).",
    },
    "en": {
        "overview": "System & firmware",
        "settings": "Settings",
        "values": "Values",
        "webui": "Web UI",
        "mqtt": "MQTT",
        "firmware": "Firmware",
        "system": "System",
        "heating": "Heating & hot water",
        "install": "Install firmware",
        "install_confirm": "EMS-ESP will download the new firmware from GitHub, flash it and restart "
        "(about 1–3 minutes). Heating keeps running. Continue?",
        "restart_confirm": "Restart EMS-ESP now?",
        "versions": "Install a specific version",
        "versions_list": "Available stable versions",
        "col_version": "Version",
        "col_date": "Date",
        "installed_mark": "installed",
        "install_sel_confirm": "The stable version selected above will be installed and EMS-ESP restarts "
        "(about 1–3 minutes). After a downgrade please check the settings. Continue?",
        "update_available": "New firmware available",
        "installed": "Installed",
        "latest": "New",
        "changes": "Release notes",
        "no_devices": "No EMS-ESP devices from MQTT found. Enable **Home Assistant discovery** "
        "in EMS-ESP under **Settings → MQTT**.",
        "gateway": "Gateway",
        "web_hint": "If Home Assistant is opened via **https**, the browser blocks the embedded page. Open it here:",
        "open": "Open the EMS-ESP web UI in a new tab",
        "log": "Bus log",
        "log_hint": "Live log of the EMS bus traffic (telegrams read and written). "
        "Set **Bus capture** to “Telegrams” or “Raw data” to see them. Switch it off again afterwards. "
        "**Tx mode**: how EMS-ESP sends – changing it restarts EMS-ESP.",
        "log_empty": "No lines yet. Switch on the bus capture and wait a moment.",
        "panel": "Bosch panel",
        "log_download": "Download log",
        "github_hint": "The last list is kept; reload with “Check for updates”.",
        "mapping": "Mapping",
        "mapping_hint": "Every panel function is mapped to an entity. EMS-ESP default names are detected "
        "automatically, **fehlt** marks missing ones. Tap to change – stored in Home Assistant (admins only).",
    },
}


def _dev_name(device: dr.DeviceEntry | None) -> str:
    if device is None:
        return ""
    return device.name_by_user or device.name or ""


def _friendly(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    if state is None:
        return entity_id
    return str(state.attributes.get("friendly_name") or entity_id)


def _short(hass: HomeAssistant, entity_id: str, device: dr.DeviceEntry | None) -> str:
    """Entity name without the device name in front."""
    full = _friendly(hass, entity_id)
    for prefix in (_dev_name(device), device.name if device else None):
        if prefix and full.lower().startswith(prefix.lower() + " "):
            rest = full[len(prefix) + 1 :].strip()
            return rest[:1].upper() + rest[1:]
    return full


def _press(entity_id: str, confirm: str | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {
        "action": "perform-action",
        "perform_action": "button.press",
        "target": {"entity_id": entity_id},
    }
    if confirm:
        action["confirmation"] = {"text": confirm}
    return action


def build_dashboard(hass: HomeAssistant) -> dict[str, Any]:
    """Return the complete Lovelace config."""
    lang_key = lang(hass.config.language)
    t = TXT[lang_key]
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    def visible(entry: er.RegistryEntry) -> bool:
        return (
            entry.disabled_by is None
            and entry.hidden_by is None
            and hass.states.get(entry.entity_id) is not None
        )

    entries = [e for e in ent_reg.entities.values() if visible(e)]

    # ------------------------------------------------ own entities
    own = [e for e in entries if e.platform == DOMAIN]
    own_by_key: dict[str, str] = {}
    for e in own:
        own_by_key.setdefault(e.translation_key or e.entity_id, e.entity_id)
    gateway = dev_reg.async_get(own[0].device_id) if own and own[0].device_id else None

    def own_name(entity_id: str) -> str:
        return _short(hass, entity_id, gateway)

    def tile_button(key: str, icon: str, confirm: str | None = None) -> dict[str, Any] | None:
        entity_id = own_by_key.get(key)
        if not entity_id:
            return None
        return {
            "type": "tile",
            "entity": entity_id,
            "name": own_name(entity_id),
            "icon": icon,
            "hide_state": True,
            "grid_options": {"columns": 12},
            "tap_action": _press(entity_id, confirm),
            "icon_tap_action": _press(entity_id, confirm),
        }

    # ------------------------------------------------ EMS devices from MQTT
    mqtt_device_ids = {e.device_id for e in entries if e.platform == "mqtt" and e.device_id}
    mains = [
        d
        for d in dev_reg.devices.values()
        if d.manufacturer == "EMS-ESP" and d.via_device_id is None and d.id in mqtt_device_ids
    ]
    main_ids = {d.id for d in mains}
    ems_devices = [
        d
        for d in dev_reg.devices.values()
        if d.id in mqtt_device_ids and (d.id in main_ids or d.via_device_id in main_ids)
    ]
    ems_devices.sort(key=lambda d: (d.id in main_ids, _dev_name(d).lower()))
    order = {d.id: i for i, d in enumerate(ems_devices)}

    ems_entries = [e for e in entries if e.platform == "mqtt" and e.device_id in order]
    if not ems_entries:  # fallback on EMS-ESP entity naming
        ems_entries = [e for e in entries if e.platform == "mqtt" and ".ems_esp_" in e.entity_id]

    main_name = _dev_name(mains[0]) if mains else ""

    def card_title(device: dr.DeviceEntry | None) -> str:
        if device is None:
            return "EMS-ESP"
        if device.id in main_ids:
            return f"{t['gateway']} ({_dev_name(device)})"
        name = _dev_name(device)
        if main_name and name.lower().startswith(main_name.lower() + " "):
            name = name[len(main_name) + 1 :]
        return name[:1].upper() + name[1:]

    groups: dict[str | None, list[str]] = {}
    for e in ems_entries:
        groups.setdefault(e.device_id, []).append(e.entity_id)

    def device_cards(domains: tuple[str, ...]) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for device_id in sorted(groups, key=lambda d: order.get(d, 999)):
            device = dev_reg.async_get(device_id) if device_id else None
            ids = sorted(
                (i for i in groups[device_id] if i.split(".")[0] in domains),
                key=lambda i: _friendly(hass, i).lower(),
            )
            if not ids:
                continue
            cards.append(
                {
                    "type": "entities",
                    "title": card_title(device),
                    "state_color": True,
                    "show_header_toggle": False,
                    "entities": [{"entity": i, "name": _short(hass, i, device)} for i in ids],
                }
            )
        return cards or [{"type": "markdown", "content": t["no_devices"]}]

    # ------------------------------------------------ view 1: system & firmware
    sections: list[dict[str, Any]] = []
    avail = own_by_key.get("update_available")
    progress = own_by_key.get("update_progress")
    state_ent = own_by_key.get("system_state")
    fw: list[dict[str, Any]] = [{"type": "heading", "heading": t["firmware"], "icon": "mdi:update"}]
    if avail:
        update_on = [{"condition": "state", "entity": avail, "state": "on"}]
        fw.append(
            {
                "type": "conditional",
                "conditions": update_on,
                "card": {
                    "type": "markdown",
                    "content": (
                        f"### {t['update_available']}\n"
                        f"{t['installed']}: **{{{{ state_attr('{avail}','installed_version') }}}}** → "
                        f"{t['latest']}: **{{{{ state_attr('{avail}','latest_version') }}}}**\n\n"
                        f"[{t['changes']}]({{{{ state_attr('{avail}','release_url') }}}})"
                    ),
                },
            }
        )
        fw.append({"type": "tile", "entity": avail, "name": own_name(avail), "grid_options": {"columns": 12}})
    if progress and state_ent:
        fw.append(
            {
                "type": "conditional",
                "conditions": [{"condition": "state", "entity": state_ent, "state_not": STATES[lang_key]["normal"]}],
                "card": {
                    "type": "tile",
                    "entity": progress,
                    "name": own_name(progress),
                    "grid_options": {"columns": 12},
                },
            }
        )
    if avail and (card := tile_button("install_latest", "mdi:cloud-download", t["install_confirm"])):
        card["name"] = t["install"]
        card["color"] = "orange"
        fw.append({"type": "conditional", "conditions": update_on, "card": card})
    if card := tile_button("check_updates", "mdi:cloud-search"):
        fw.append(card)
    # Bosch HMI 800 panel: version / upload – part of the firmware column
    fw.append({"type": "heading", "heading": t["panel"], "heading_style": "subtitle", "icon": "mdi:thermostat-box"})
    fw.append(
        {
            "type": "iframe",
            "url": URL_PANEL_ADMIN,
            "grid_options": {"columns": 12, "rows": 5},
        }
    )
    if own:
        sections.append({"type": "grid", "cards": fw})

        sel = own_by_key.get("stable_version")
        installed = own_by_key.get("firmware_installed")
        if sel:
            ver_cards: list[dict[str, Any]] = [
                {"type": "heading", "heading": t["versions"], "icon": "mdi:tag-multiple"},
                {
                    "type": "tile",
                    "entity": sel,
                    "name": own_name(sel),
                    "features": [{"type": "select-options"}],
                    "grid_options": {"columns": 12},
                },
            ]
            if card := tile_button("install_selected", "mdi:tag-arrow-down", t["install_sel_confirm"]):
                card["grid_options"] = {"columns": 12}
                card["color"] = "orange"
                ver_cards.append(card)
            ver_cards.append(
                {
                    "type": "markdown",
                    "content": (
                        f"{{% set ge = state_attr('{sel}','github_error') %}}"
                        f"{{% if ge %}}> ⚠️ **{{{{ ge }}}}**"
                        f"{{% if state_attr('{sel}','retry_at') %}} – wieder möglich ab {{{{ state_attr('{sel}','retry_at') }}}} Uhr{{% endif %}}. "
                        f"{t['github_hint']}\n\n{{% endif %}}"
                        f"**{t['versions_list']}**\n\n"
                        f"| {t['col_version']} | {t['col_date']} | |\n|:--|:--|:--|\n"
                        f"{{% for r in state_attr('{sel}','releases') or [] %}}"
                        f"{{% set d = r.date.split('-') %}}"
                        f"| **{{{{ r.version }}}}**"
                        f"{{% if r.version == states('{installed}') %}} ✔ {t['installed_mark']}{{% endif %}}"
                        f" | {{{{ d[2] ~ '.' ~ d[1] ~ '.' ~ d[0] if d | count == 3 else r.date }}}}"
                        f" | [Changelog]({{{{ r.url }}}}) |\n"
                        f"{{% endfor %}}"
                    ),
                }
            )
            sections.append({"type": "grid", "cards": ver_cards})

        rows = [
            own_by_key[k]
            for k in (
                "system_state",
                "firmware_installed",
                "firmware_latest",
                "manager_version",
                "hardware",
                "bus_status",
                "num_devices",
                "wifi_rssi",
                "uptime",
            )
            if k in own_by_key
        ]
        sys_cards: list[dict[str, Any]] = [
            {"type": "heading", "heading": t["system"], "icon": "mdi:information-outline"},
            {
                "type": "entities",
                "state_color": True,
                "entities": [{"entity": i, "name": own_name(i)} for i in rows],
            },
        ]
        for card in (
            tile_button("fetch_values", "mdi:database-refresh"),
            tile_button("restart", "mdi:restart", t["restart_confirm"]),
        ):
            if card:
                sys_cards.append(card)
        sections.append({"type": "grid", "cards": sys_cards})

    climate = sorted(
        (e.entity_id for e in ems_entries if e.domain in ("climate", "water_heater")),
        key=lambda i: _friendly(hass, i).lower(),
    )
    def climate_name(entity_id: str) -> str:
        name = _friendly(hass, entity_id)
        if main_name and name.lower().startswith(main_name.lower() + " "):
            name = name[len(main_name) + 1 :]
        return name[:1].upper() + name[1:]

    settings_cards: list[dict[str, Any]] = []
    control_domains = CONTROL_DOMAINS
    if climate:
        # thermostat / hot water tiles first in the 'Einstellungen' tab (no heading)
        settings_cards += [
            {
                "type": "tile",
                "entity": i,
                "name": climate_name(i),
                "features": (
                    [{"type": "target-temperature"}, {"type": "climate-hvac-modes"}]
                    if i.startswith("climate.")
                    else [{"type": "target-temperature"}]
                ),
            }
            for i in climate
        ]
        control_domains = tuple(d for d in CONTROL_DOMAINS if d not in ("climate", "water_heater"))
    other = device_cards(control_domains)
    if not (settings_cards and len(other) == 1 and other[0].get("type") == "markdown"):
        settings_cards += other

    views: list[dict[str, Any]] = [
        {"title": t["overview"], "path": "system", "type": "sections", "max_columns": 3, "sections": sections},
        {"title": t["settings"], "path": "einstellungen", "type": "masonry", "cards": settings_cards},
        {"title": t["values"], "path": "messwerte", "type": "masonry", "cards": device_cards(VALUE_DOMAINS)},
        {
            "title": t["mapping"],
            "path": "zuordnung",
            "type": "panel",
            # compact list with its own explanation – the iframe fills the whole view
            "cards": [{"type": "iframe", "url": f"{URL_PANEL}?page=map"}],
        },
    ]

    log_sensor = own_by_key.get("bus_log")
    if log_sensor:
        control_cards: list[dict[str, Any]] = [{"type": "markdown", "content": t["log_hint"]}]
        for key, icon in (
            ("watch_mode", "mdi:bus-alert"),
            ("log_level", "mdi:text-box-search-outline"),
            ("tx_mode", "mdi:transit-connection-variant"),
        ):
            if entity_id := own_by_key.get(key):
                control_cards.append(
                    {
                        "type": "tile",
                        "entity": entity_id,
                        "name": own_name(entity_id),
                        "icon": icon,
                        "features": [{"type": "select-options"}],
                        "grid_options": {"columns": 12},
                    }
                )
        if card := tile_button("clear_log", "mdi:notification-clear-all"):
            control_cards.append(card)
        download_cards = [
            {"type": "heading", "heading": t["log_download"], "icon": "mdi:download"},
            {"type": "iframe", "url": URL_LOG_CARD, "grid_options": {"columns": 12, "rows": 6}},
        ]
        log_card = {
            "type": "markdown",
            "text_only": True,
            "grid_options": {"columns": "full"},
            "content": (
                f"{{% set lines = state_attr('{log_sensor}','lines') or [] %}}"
                f"{{% if lines %}}"
                f"{{% for line in lines | reverse %}}`{{{{ line }}}}`  \n{{% endfor %}}"
                f"{{% else %}}{t['log_empty']}{{% endif %}}"
            ),
        }
        views.append(
            {
                "title": t["log"],
                "path": "log",
                "type": "sections",
                "max_columns": 3,
                "sections": [
                    {"type": "grid", "cards": control_cards + download_cards},
                    {"type": "grid", "column_span": 2, "cards": [log_card]},
                ],
            }
        )

    # MQTT settings of EMS-ESP (form page, admin only)
    views.append(
        {
            "title": t["mqtt"],
            "path": "mqtt",
            "type": "panel",
            "cards": [{"type": "iframe", "url": URL_MQTT_CARD}],
        }
    )

    web_url = (gateway.configuration_url if gateway else None) or (
        mains[0].configuration_url if mains else None
    )
    if web_url:
        views.append(
            {
                "title": t["webui"],
                "path": "webui",
                "type": "panel",
                "cards": [
                    {
                        "type": "vertical-stack",
                        "cards": [
                            {"type": "markdown", "content": f"{t['web_hint']} [{t['open']}]({web_url})"},
                            {"type": "iframe", "url": web_url, "aspect_ratio": "75%"},
                        ],
                    }
                ],
            }
        )

    return {"title": "EMS-ESP", "views": views}
