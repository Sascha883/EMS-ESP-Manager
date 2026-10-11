"""Small async client for the EMS-ESP web API and GitHub releases."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import CHANNEL_DEV, GITHUB_API, GITHUB_DOWNLOAD, MIN_SELECTABLE_VERSION

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
_ASSET_RE = re.compile(r"^EMS-ESP-(?P<ver>.+?)-(?P<platform>ESP32.*)\.bin$")


class EmsEspError(Exception):
    """Generic communication error."""


class EmsEspAuthError(EmsEspError):
    """Wrong credentials or missing admin rights."""


def token_is_admin(token: str) -> bool:
    """Read the 'admin' flag from the JWT payload (no signature check needed)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return bool(data.get("admin"))
    except (IndexError, ValueError):
        return False


def platform_string(status: dict[str, Any]) -> str | None:
    """Build the firmware platform suffix exactly like the EMS-ESP web UI does."""
    platform = status.get("esp_platform")
    if not platform:
        return None
    flash = int(status.get("flash_chip_size") or 0)
    size = "16MB" if flash >= 16384 else "4MB"
    return f"{platform}-{size}{'+' if status.get('psram') else ''}"


def firmware_url(version: str, channel: str, platform: str) -> str:
    """Download URL of a firmware binary on GitHub."""
    filename = f"EMS-ESP-{version.replace('.', '_')}-{platform}.bin"
    tag = "latest" if channel == CHANNEL_DEV else f"v{version}"
    return f"{GITHUB_DOWNLOAD}/{tag}/{filename}"


@dataclass
class Release:
    """A firmware release on GitHub."""

    version: str
    notes: str
    url: str
    platforms: set[str]
    date: str = ""
    assets: dict[str, str] = field(default_factory=dict)  # platform -> download url

    def download_url(self, channel: str, platform: str) -> str:
        """Asset URL from GitHub, or the URL built like the EMS-ESP web UI does."""
        return self.assets.get(platform) or firmware_url(self.version, channel, platform)


def parse_release(data: dict[str, Any]) -> Release | None:
    """Turn one GitHub release JSON object into a Release."""
    version: str | None = None
    platforms: set[str] = set()
    assets: dict[str, str] = {}
    for asset in data.get("assets", []):
        match = _ASSET_RE.match(asset.get("name", ""))
        if match:
            version = match.group("ver").replace("_", ".")
            platforms.add(match.group("platform"))
            if asset.get("browser_download_url"):
                assets[match.group("platform")] = asset["browser_download_url"]
    if version is None:
        tag = str(data.get("tag_name", ""))
        if not tag or tag == "latest":
            return None
        version = tag.removeprefix("v")
    return Release(
        version=version,
        notes=data.get("body") or "",
        url=data.get("html_url") or "",
        platforms=platforms,
        date=str(data.get("published_at") or "")[:10],
        assets=assets,
    )


class EmsEspApi:
    """Talks to one EMS-ESP gateway."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        token: str | None = None,
    ) -> None:
        host = host.strip().rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        self.base = host
        self._session = session
        self._username = username
        self._password = password
        self.token = token

    # ---------------------------------------------------------------- auth
    async def sign_in(self) -> str:
        """Get a fresh JWT from /rest/signIn."""
        try:
            async with self._session.post(
                f"{self.base}/rest/signIn",
                json={"username": self._username, "password": self._password},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise EmsEspAuthError("invalid_auth")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise EmsEspError(str(err)) from err
        token = data.get("access_token")
        if not token:
            raise EmsEspAuthError("invalid_auth")
        self.token = token
        return token

    async def _request(
        self, method: str, path: str, payload: Any = None, retry: bool = True
    ) -> tuple[int, Any]:
        if not self.token:
            await self.sign_in()
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with self._session.request(
                method,
                f"{self.base}{path}",
                json=payload,
                headers=headers,
                timeout=TIMEOUT,
            ) as resp:
                if resp.status in (401, 403) and retry:
                    # token no longer valid (e.g. after factory reset) -> sign in again
                    await self.sign_in()
                    return await self._request(method, path, payload, retry=False)
                if resp.status in (401, 403):
                    raise EmsEspAuthError(f"HTTP {resp.status}")
                text = await resp.text()
                try:
                    data = json.loads(text) if text else None
                except ValueError:
                    data = text
                return resp.status, data
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise EmsEspError(str(err)) from err

    # ------------------------------------------------------------- queries
    async def get_status(self) -> dict[str, Any]:
        """GET /rest/systemStatus (also triggers a pending restart after an upload)."""
        status, data = await self._request("GET", "/rest/systemStatus")
        if status != 200 or not isinstance(data, dict):
            raise EmsEspError(f"systemStatus returned HTTP {status}")
        return data

    async def action(self, action: str, param: str | None = None) -> Any:
        """POST /rest/action."""
        payload: dict[str, Any] = {"action": action}
        if param is not None:
            payload["param"] = param
        status, data = await self._request("POST", "/rest/action", payload)
        if status != 200:
            raise EmsEspError(f"action {action} returned HTTP {status}")
        return data

    async def get_settings(self) -> dict[str, Any]:
        """GET /rest/settings (contains ems_bus_id and tx_mode)."""
        status, data = await self._request("GET", "/rest/settings")
        if status != 200 or not isinstance(data, dict):
            raise EmsEspError(f"settings returned HTTP {status}")
        return data

    async def set_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Change single EMS-ESP settings.

        EMS-ESP fills every field missing in a POST with its default, so the
        complete settings object is read first and sent back with the changes.
        """
        current = await self.get_settings()
        current.update(changes)
        status, data = await self._request("POST", "/rest/settings", current)
        if status not in (200, 202):
            raise EmsEspError(f"settings returned HTTP {status}")
        return data if isinstance(data, dict) else current

    async def get_mqtt_settings(self) -> dict[str, Any]:
        """GET /rest/mqttSettings."""
        status, data = await self._request("GET", "/rest/mqttSettings")
        if status != 200 or not isinstance(data, dict):
            raise EmsEspError(f"mqttSettings returned HTTP {status}")
        return data

    async def set_mqtt_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Change MQTT settings – complete object back, EMS-ESP defaults missing fields."""
        current = await self.get_mqtt_settings()
        current.update(changes)
        status, data = await self._request("POST", "/rest/mqttSettings", current)
        if status not in (200, 202):
            raise EmsEspError(f"mqttSettings returned HTTP {status}")
        return data if isinstance(data, dict) else current

    async def get_log_settings(self) -> dict[str, Any]:
        """GET /rest/logSettings -> {'level': 6, 'max_messages': 50, 'compact': True}."""
        status, data = await self._request("GET", "/rest/logSettings")
        if status != 200 or not isinstance(data, dict):
            raise EmsEspError(f"logSettings returned HTTP {status}")
        return data

    async def set_log_level(self, level: int) -> None:
        """POST /rest/logSettings - keeps the other values as they are."""
        current = await self.get_log_settings()
        payload = {
            "level": int(level),
            "max_messages": current.get("max_messages", 50),
            "compact": current.get("compact", True),
        }
        status, _ = await self._request("POST", "/rest/logSettings", payload)
        if status != 200:
            raise EmsEspError(f"logSettings returned HTTP {status}")

    def log_url(self) -> str:
        """Server-sent-events stream with every log line."""
        return f"{self.base}/es/log"

    @property
    def session(self) -> aiohttp.ClientSession:
        return self._session

    async def command(self, device: str, cmd: str, value: Any = "") -> Any:
        """POST /api/<device>/<cmd>."""
        status, data = await self._request(
            "POST", f"/api/{device}/{cmd}", {"value": value}
        )
        if status != 200:
            raise EmsEspError(f"{device}/{cmd} returned HTTP {status}")
        return data


# last problem talking to GitHub (shown in the dashboard), None = OK
GITHUB_STATUS: dict[str, Any] = {"error": None, "reset": None}


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    try:
        async with session.get(
            url,
            headers={"Accept": "application/vnd.github+json"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                _LOGGER.warning("GitHub %s returned HTTP %s", url, resp.status)
                reset = resp.headers.get("X-RateLimit-Reset")
                remaining = resp.headers.get("X-RateLimit-Remaining")
                if resp.status in (403, 429) and remaining == "0":
                    GITHUB_STATUS["error"] = "GitHub-Abfragelimit erreicht (60 pro Stunde je Internetanschluss)"
                else:
                    GITHUB_STATUS["error"] = f"GitHub antwortet mit HTTP {resp.status}"
                GITHUB_STATUS["reset"] = int(reset) if reset and reset.isdigit() else None
                return None
            GITHUB_STATUS["error"] = None
            GITHUB_STATUS["reset"] = None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        _LOGGER.warning("GitHub request failed: %s", err)
        GITHUB_STATUS["error"] = f"GitHub nicht erreichbar ({type(err).__name__})"
        GITHUB_STATUS["reset"] = None
        return None


async def fetch_release(
    session: aiohttp.ClientSession, channel: str
) -> Release | None:
    """Read the newest development release ('latest' tag) from GitHub."""
    url = f"{GITHUB_API}/tags/latest" if channel == CHANNEL_DEV else f"{GITHUB_API}/latest"
    data = await _get_json(session, url)
    return parse_release(data) if isinstance(data, dict) else None


def _version_key(version: str) -> tuple[int, ...]:
    parts = []
    for p in version.split("-")[0].split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


async def fetch_stable_releases(
    session: aiohttp.ClientSession, minimum: str = MIN_SELECTABLE_VERSION
) -> list[Release] | None:
    """All stable releases (newest first) from `minimum` on."""
    data = await _get_json(session, f"{GITHUB_API}?per_page=50")
    if not isinstance(data, list):
        return None
    result: list[Release] = []
    for item in data:
        if item.get("draft") or item.get("prerelease") or item.get("tag_name") == "latest":
            continue
        release = parse_release(item)
        if release is None or "dev" in release.version:
            continue
        if _version_key(release.version) < _version_key(minimum):
            continue
        result.append(release)
    result.sort(key=lambda r: _version_key(r.version), reverse=True)
    return result


# ---------------------------------------------------------------- log
LOG_LEVELS: dict[str, int] = {
    "error": 3,
    "warning": 4,
    "notice": 5,
    "info": 6,
    "debug": 7,
    "trace": 8,
    "all": 9,
}
LEVEL_CHAR = {3: "E", 4: "W", 5: "N", 6: "I", 7: "D", 8: "T", 9: "A"}
