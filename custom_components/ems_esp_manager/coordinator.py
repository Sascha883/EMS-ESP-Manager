"""Data coordinators for EMS-ESP Manager."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    GITHUB_STATUS,
    EmsEspApi,
    Release,
    EmsEspAuthError,
    EmsEspError,
    fetch_release,
    fetch_stable_releases,
)
from .const import (
    CHANNEL_STABLE,
    DOMAIN,
    RELEASE_SCAN_INTERVAL,
    STATUS_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class EmsEspRuntime:
    """Everything one config entry needs at runtime."""

    api: EmsEspApi
    status: StatusCoordinator
    releases: ReleaseCoordinator
    channel: str = CHANNEL_STABLE
    installing: bool = False
    selected_version: str | None = None
    language: str = "de"
    log: Any = None          # EmsEspLog
    watch_mode: str = "off"  # off | on | raw
    log_level: int = 6
    bus_id: int | None = None   # EMS-ESP's own bus id (settings ems_bus_id), 0x49 since 3.8.2
    tx_mode: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


type EmsEspConfigEntry = ConfigEntry[EmsEspRuntime]


class StatusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls /rest/systemStatus."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: EmsEspApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} status",
            update_interval=STATUS_SCAN_INTERVAL,
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.api.get_status()
        except EmsEspAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EmsEspError as err:
            raise UpdateFailed(f"EMS-ESP nicht erreichbar: {err}") from err


def _rel_to_dict(r: Release) -> dict[str, Any]:
    return {
        "version": r.version, "notes": r.notes, "url": r.url,
        "platforms": sorted(r.platforms), "date": r.date, "assets": r.assets,
    }


def _rel_from_dict(d: dict[str, Any]) -> Release:
    return Release(
        version=d["version"], notes=d.get("notes", ""), url=d.get("url", ""),
        platforms=set(d.get("platforms", [])), date=d.get("date", ""),
        assets=dict(d.get("assets", {})),
    )


class ReleaseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Reads the stable releases from GitHub (development builds are not offered).

    data = {"stable": Release|None, "stable_list": [Release, ...]}  (only stable firmware)

    The list is kept in .storage, so a restart of Home Assistant neither
    empties the list nor asks GitHub again (GitHub allows only 60 requests
    per hour and internet connection without login).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} releases",
            update_interval=RELEASE_SCAN_INTERVAL,
        )
        self._session = async_get_clientsession(hass)
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.releases")
        self._fetched: float = 0.0          # timestamp of the last successful GitHub read
        self.force = False                  # set by "Updates prüfen"
        self.error: str | None = None
        self.retry_at: str | None = None

    async def async_load_cache(self) -> None:
        """Fill the list from the last successful GitHub read."""
        try:
            cached = await self._store.async_load() or {}
            lst = [_rel_from_dict(d) for d in cached.get("stable_list", [])]
        except Exception:  # noqa: BLE001 - a broken cache must not matter
            _LOGGER.debug("Release cache unreadable", exc_info=True)
            return
        if lst:
            self._fetched = float(cached.get("fetched", 0))
            self.data = {CHANNEL_STABLE: lst[0], "stable_list": lst}

    async def _async_update_data(self) -> dict[str, Any]:
        old = self.data or {}
        age = dt_util.utcnow().timestamp() - self._fetched
        if (
            not self.force
            and old.get("stable_list")
            and age < RELEASE_SCAN_INTERVAL.total_seconds() - 60
        ):
            return old  # still fresh (e.g. right after a restart)
        self.force = False
        stable_list = await fetch_stable_releases(self._session)
        if stable_list:
            self._fetched = dt_util.utcnow().timestamp()
            self.error = self.retry_at = None
            await self._store.async_save(
                {"fetched": self._fetched, "stable_list": [_rel_to_dict(r) for r in stable_list]}
            )
        else:  # GitHub temporarily unavailable -> keep last list
            self.error = GITHUB_STATUS.get("error") or "Keine Versionen von GitHub erhalten"
            reset = GITHUB_STATUS.get("reset")
            self.retry_at = (
                dt_util.as_local(dt_util.utc_from_timestamp(reset)).strftime("%H:%M")
                if reset else None
            )
            stable_list = old.get("stable_list") or []
        stable = stable_list[0] if stable_list else None
        if stable is None:
            stable = await fetch_release(self._session, CHANNEL_STABLE) or old.get(CHANNEL_STABLE)
        return {CHANNEL_STABLE: stable, "stable_list": stable_list}
