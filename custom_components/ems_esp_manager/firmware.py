"""Firmware installation (shared by the update entity and the 'install selected version' button)."""

from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.exceptions import HomeAssistantError

from .api import EmsEspAuthError, EmsEspError, Release, platform_string
from .const import (
    STATUS_ERROR_UPLOAD,
    STATUS_NORMAL,
    STATUS_PENDING_RESTART,
    STATUS_RESTART_REQUESTED,
)
from .coordinator import EmsEspRuntime

_LOGGER = logging.getLogger(__name__)

POLL_SECONDS = 2
UPLOAD_TIMEOUT = 20 * 60  # seconds


def find_release(rt: EmsEspRuntime, version: str) -> tuple[Release, str] | None:
    """Look up a version in the list of stable releases."""
    data = rt.releases.data or {}
    for release in data.get("stable_list") or []:
        if release.version == version:
            return release, "stable"
    return None


async def async_install(rt: EmsEspRuntime, release: Release, channel: str) -> None:
    """Let EMS-ESP download `release` from GitHub, flash it and reboot."""
    if rt.installing:
        raise HomeAssistantError("Es läuft bereits ein Firmware-Update.")
    status = rt.status.data or {}
    platform = platform_string(status)
    if not platform:
        raise HomeAssistantError("Hardware-Plattform des EMS-ESP unbekannt.")
    if release.platforms and platform not in release.platforms:
        raise HomeAssistantError(
            f"Für {platform} gibt es in Version {release.version} keine Firmware-Datei."
        )
    url = release.download_url(channel, platform)
    _LOGGER.info("Installing EMS-ESP %s from %s", release.version, url)

    rt.installing = True
    rt.status.async_update_listeners()
    try:
        await rt.api.action("uploadURL", url)
        await _follow_progress(rt, release.version, int(status.get("uptime") or 0))
    except EmsEspAuthError as err:
        raise HomeAssistantError(
            "EMS-ESP hat den Auftrag abgelehnt – Benutzer braucht Admin-Rechte."
        ) from err
    except EmsEspError as err:
        raise HomeAssistantError(f"EMS-ESP Update fehlgeschlagen: {err}") from err
    finally:
        rt.installing = False
        rt.status.async_update_listeners()
        await rt.status.async_request_refresh()


async def _follow_progress(rt: EmsEspRuntime, target: str, last_uptime: int) -> None:
    """Poll the gateway until the new firmware is running.

    Reading /rest/systemStatus while the status is 'pending restart' is what
    makes EMS-ESP reboot into the new firmware (same as the web UI does).
    """
    deadline = time.monotonic() + UPLOAD_TIMEOUT
    restarted = False
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        try:
            data = await rt.api.get_status()
        except EmsEspAuthError:
            raise
        except EmsEspError:
            continue  # busy flashing or rebooting
        rt.status.async_set_updated_data(data)
        code = int(data.get("status") or 0)
        uptime = int(data.get("uptime") or 0)
        if uptime < last_uptime:
            restarted = True  # rebooted between two polls
        last_uptime = uptime
        if code == STATUS_ERROR_UPLOAD:
            raise HomeAssistantError(
                "Firmware-Download/Flash fehlgeschlagen. Details im EMS-ESP System-Log."
            )
        if code in (STATUS_PENDING_RESTART, STATUS_RESTART_REQUESTED):
            restarted = True
            continue
        if code == STATUS_NORMAL:
            if data.get("emsesp_version") == target:
                _LOGGER.info("EMS-ESP now runs %s", target)
                return
            if restarted:
                raise HomeAssistantError(
                    f"EMS-ESP wurde neu gestartet, meldet aber Version "
                    f"{data.get('emsesp_version')} statt {target}."
                )
    raise HomeAssistantError("Zeitüberschreitung beim Firmware-Update.")
