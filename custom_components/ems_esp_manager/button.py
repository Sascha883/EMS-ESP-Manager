"""Buttons: restart, refresh values, check for updates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import EmsEspAuthError, EmsEspError
from .binary_sensor import update_available
from .const import CHANNEL_STABLE
from .coordinator import EmsEspConfigEntry, EmsEspRuntime
from .entity import EmsEspEntity
from .firmware import async_install, find_release


async def _restart(rt: EmsEspRuntime) -> None:
    try:
        await rt.api.command("system", "restart")
    except EmsEspAuthError:
        raise
    except EmsEspError:
        # the gateway often drops the connection while rebooting – that is fine
        pass


async def _fetch(rt: EmsEspRuntime) -> None:
    await rt.api.command("system", "fetch", "all")


async def _install_latest(rt: EmsEspRuntime) -> None:
    release = (rt.releases.data or {}).get(CHANNEL_STABLE)
    if release is None:
        raise HomeAssistantError("Keine Versionsinformation von GitHub. Bitte 'Nach Updates suchen' drücken.")
    if not update_available(rt):
        raise HomeAssistantError(f"Die neueste Version ({release.version}) ist bereits installiert.")
    await async_install(rt, release, CHANNEL_STABLE)


async def _install_selected(rt: EmsEspRuntime) -> None:
    stable = (rt.releases.data or {}).get("stable_list") or []
    version = rt.selected_version or (stable[0].version if stable else None)
    if not version:
        raise HomeAssistantError("Keine Versionsliste von GitHub. Bitte 'Nach Updates suchen' drücken.")
    found = find_release(rt, version)
    if found is None:
        raise HomeAssistantError(f"Version {version} ist nicht verfügbar.")
    if (rt.status.data or {}).get("emsesp_version") == version:
        raise HomeAssistantError(f"Version {version} ist bereits installiert.")
    await async_install(rt, *found)


async def _gateway_sensors(rt: EmsEspRuntime) -> None:
    from homeassistant.components import persistent_notification

    from .gateway_sensors import async_create_gateway_sensors

    lines = await async_create_gateway_sensors(rt)
    persistent_notification.async_create(
        rt.status.hass,
        "\n".join(f"- {line}" for line in lines),
        title="EMS-ESP: Gateway-Sensoren",
        notification_id=f"ems_esp_manager_gateway_sensors_{rt.status.config_entry.entry_id}",
    )
    await rt.status.async_request_refresh()


async def _clear_log(rt: EmsEspRuntime) -> None:
    if rt.log:
        rt.log.clear()


async def _check(rt: EmsEspRuntime) -> None:
    rt.releases.force = True
    await rt.releases.async_refresh()
    await rt.status.async_request_refresh()


@dataclass(frozen=True, kw_only=True)
class EmsEspButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[EmsEspRuntime], Awaitable[None]]


BUTTONS = (
    EmsEspButtonDescription(
        key="restart",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=_restart,
    ),
    EmsEspButtonDescription(
        key="fetch_values",
        icon="mdi:database-refresh",
        press_fn=_fetch,
    ),
    EmsEspButtonDescription(
        key="install_latest",
        icon="mdi:cloud-download",
        press_fn=_install_latest,
    ),
    EmsEspButtonDescription(
        key="install_selected",
        icon="mdi:tag-arrow-down",
        press_fn=_install_selected,
    ),
    EmsEspButtonDescription(
        key="clear_log",
        icon="mdi:notification-clear-all",
        press_fn=_clear_log,
    ),
    EmsEspButtonDescription(
        key="gateway_sensors",
        icon="mdi:chip",
        entity_category=EntityCategory.CONFIG,
        press_fn=_gateway_sensors,
    ),
    EmsEspButtonDescription(
        key="check_updates",
        icon="mdi:cloud-search",
        press_fn=_check,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EmsEspConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(EmsEspButton(entry, d) for d in BUTTONS)


class EmsEspButton(EmsEspEntity, ButtonEntity):
    entity_description: EmsEspButtonDescription

    def __init__(self, entry: EmsEspConfigEntry, description: EmsEspButtonDescription) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        try:
            await self.entity_description.press_fn(self._runtime)
        except EmsEspError as err:
            raise HomeAssistantError(f"EMS-ESP: {err}") from err
