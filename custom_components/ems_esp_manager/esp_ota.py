"""ESP-Bus-Adapter: Firmware über WLAN aufspielen (natives ESPHome-OTA, TCP-Port 3232).

Eigenentwicklung Sascha Matheis Haustechnik. Spricht das OTA-Protokoll von ESPHome
(wie ``esphome upload``) im einfachen Modus: ohne Kompression, ohne Verschlüsselung,
Anmeldung per SHA256 bzw. MD5, wenn im Adapter ``ota: password`` gesetzt ist.

Ablauf: Kennung → Version → Merkmale → Anmeldung → Größe → MD5 → Daten in 8-KB-Blöcken
(ab OTA-Version 2 mit Quittung je Block) → Empfang OK → Update OK → Adapter startet neu.

``inspect()`` prüft vorher, ob die Datei eine ESP32-App-Firmware im OTA-Format ist
(keine Factory-Datei) und liest Version, Gerätename und Projekt aus.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import hashlib
import re
import secrets
import socket
import struct
from typing import Any

OTA_PORT = 3232
MAX_SIZE = 4 * 1024 * 1024
MAGIC = bytes([0x6C, 0x26, 0xF7, 0x5C, 0x45])
BLOCK = 8192

R_OK = 0x00
R_AUTH_MD5 = 0x01
R_AUTH_SHA256 = 0x02
R_HEADER_OK = 0x40
R_AUTH_OK = 0x41
R_PREPARE_OK = 0x42
R_MD5_OK = 0x43
R_RECEIVE_OK = 0x44
R_END_OK = 0x45
R_SUPPORTS_COMPRESSION = 0x46
R_CHUNK_OK = 0x47
R_FEATURE_FLAGS = 0x48
CLIENT_SHA256 = 0x02           # nur SHA256-Anmeldung anbieten → einfacher Ablauf

ERRORS: dict[int, str] = {
    0x80: "Ungültige Kennung – unter dieser Adresse antwortet kein ESPHome-OTA.",
    0x81: "Der Adapter konnte den Speicher nicht vorbereiten (Datei zu groß?). Adapter neu starten und erneut versuchen.",
    0x82: "OTA-Passwort falsch.",
    0x83: "Schreiben in den Flash des Adapters fehlgeschlagen.",
    0x84: "Abschluss des Updates im Adapter fehlgeschlagen.",
    0x85: "Der Adapter muss einmal von Hand neu gestartet werden (erstes Update nach dem Flashen per USB).",
    0x86: "Der Adapter wurde mit falscher Flash-Größe geflasht – einmal per USB flashen.",
    0x87: "Die Firmware passt nicht zur Flash-Größe des Adapters (falsches Board).",
    0x88: "Zu wenig Speicher im Adapter.",
    0x89: "Die OTA-Partition des Adapters ist zu klein – einmal per USB flashen.",
    0x8A: "Der Adapter hat keine OTA-Partition – einmal per USB flashen.",
    0x8B: "Prüfsumme falsch – bitte erneut versuchen (WLAN-Empfang prüfen).",
    0x8C: "Zu wenig Speicher im Adapter.",
    0x8D: "Signatur der Firmware ungültig.",
    0x8E: "Diese Update-Art unterstützt der Adapter nicht.",
    0x93: "Der Adapter lehnt eine ältere Version ab (Downgrade-Schutz).",
    0x94: "Der Adapter verlangt verschlüsseltes OTA („ota: encryption“). Das kann der Manager noch nicht – "
          "Block in der YAML entfernen oder dieses Mal per ESPHome Builder installieren.",
    0xFF: "Unbekannter Fehler im Adapter.",
}

CHIPS = {0: "ESP32", 2: "ESP32-S2", 5: "ESP32-C3", 9: "ESP32-S3", 12: "ESP32-C2", 13: "ESP32-C6", 16: "ESP32-H2", 18: "ESP32-P4"}
_APP_DESC_MAGIC = 0xABCD5432
_RE_VER = re.compile(rb"^\d{1,3}\.\d{1,3}(?:\.\d{1,4})?(?:[-+.][0-9A-Za-z.]{1,12})?$")
_RE_ESPHOME_VER = re.compile(rb"\x00(20\d\d\.\d{1,2}\.\d{1,2}(?:-dev|b\d)?)\x00")
_RE_FILE_VER = re.compile(r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,4})(?![\d])")


class OtaError(Exception):
    """Fehler beim Prüfen oder Aufspielen (Text für das Panel)."""


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("ascii", "replace")


def _strings_near(data: bytes, pos: int, span: int = 192) -> list[tuple[int, bytes]]:
    """Null-terminierte Zeichenketten um ``pos`` (Abstand, Text)."""
    lo, hi = max(0, pos - span), min(len(data), pos + span)
    out = []
    for m in re.finditer(rb"[\x20-\x7e]{1,40}", data[lo:hi]):
        s = m.start() + lo
        if (s == 0 or data[s - 1] == 0) and m.end() + lo < len(data) and data[m.end() + lo] == 0:
            out.append((abs(s - pos), m.group()))
    return sorted(out)


def inspect(data: bytes, filename: str = "", node: str | None = None, project: str | None = None,
            psk: str | None = None) -> dict[str, Any]:
    """Firmware-Datei prüfen. Wirft OtaError bei falschem Format."""
    n = len(data)
    if n < 4096:
        raise OtaError("Datei zu klein – keine Firmware.")
    if n > MAX_SIZE:
        raise OtaError("Datei größer als 4 MB – keine OTA-Firmware.")
    if data[:2] == b"\x1f\x8b":
        raise OtaError("Gepackte Datei (.gz) – bitte die ungepackte .bin im OTA-Format verwenden.")
    if data[0] == 0xFF or data[:4] == b"\xff\xff\xff\xff":
        raise OtaError("Das ist eine Factory-Datei (mit Bootloader). Für das Update bitte das OTA-Format "
                       "nehmen: ESPHome Builder → ⋮ → Herunterladen → „OTA format“ (firmware.ota.bin).")
    if data[0] != 0xE9:
        raise OtaError("Keine ESP32-Firmware (.bin) – Kennung 0xE9 fehlt.")
    chip_id = struct.unpack_from("<H", data, 12)[0]
    desc = data[32:32 + 256]
    if struct.unpack_from("<I", desc, 0)[0] != _APP_DESC_MAGIC:
        raise OtaError("Keine App-Firmware (Bootloader- oder Factory-Datei?). Bitte das OTA-Format verwenden "
                       "(firmware.ota.bin).")
    info: dict[str, Any] = {
        "size": n,
        "md5": hashlib.md5(data).hexdigest(),
        "sha": hashlib.sha256(data).hexdigest()[:12],
        "chip": CHIPS.get(chip_id, f"0x{chip_id:04X}"),
        "build": f"{_cstr(desc[96:112])} {_cstr(desc[80:96])}".strip(),
        "idf": _cstr(desc[112:144]),
        "app": _cstr(desc[48:80]),
    }
    m = _RE_ESPHOME_VER.search(data)
    info["esphome"] = m.group(1).decode() if m else None
    # Projekt-Version (esphome: project: version) steht neben dem Projektnamen
    version = None
    if project:
        pos = data.find(b"\0" + project.encode() + b"\0")
        if pos >= 0:
            info["project"] = project
            for _d, s in _strings_near(data, pos + 1):
                if _RE_VER.match(s) and s.decode() != info["esphome"]:
                    version = s.decode()
                    break
        else:
            info["project"] = None
    if not version:
        fm = _RE_FILE_VER.search(filename or "")
        if fm:
            version = fm.group(1)
            info["version_from"] = "Dateiname"
    info["version"] = version
    # Passt die Datei zu diesem Adapter? (Gerätename und API-Schlüssel stehen in der Firmware)
    if node:
        info["node_ok"] = (b"\0" + node.encode() + b"\0") in data or node.encode() in data
    if b"ESPBUS-API-SCHLUESSEL-IM-ADAPTER" in data:
        # ab Adapter-Firmware 2.4: Schlüssel liegt im Adapter, nicht in der Datei – bleibt beim Update erhalten
        info["psk_runtime"] = True
        info["psk_ok"] = None
    elif psk:
        try:
            key = base64.b64decode(psk)
            info["psk_ok"] = len(key) == 32 and key in data
        except (ValueError, TypeError):
            info["psk_ok"] = None
    return info


async def flash(host: str, data: bytes, port: int = OTA_PORT, password: str | None = None,
                progress: Callable[[str, float], None] | None = None) -> dict[str, Any]:
    """Firmware an den Adapter senden. ``progress(phase, anteil 0..1)``."""
    def prog(phase: str, pct: float = 0.0) -> None:
        if progress:
            progress(phase, pct)

    prog("connect")
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 10)
    except (OSError, asyncio.TimeoutError) as err:
        raise OtaError(f"Keine Verbindung zu {host}:{port} – Adapter eingeschaltet? OTA in der Firmware aktiv? ({err})") from err
    sock = writer.get_extra_info("socket")
    try:
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass

    async def rx(n: int, what: str, expect: tuple[int, ...] | None = None, timeout: float = 20) -> bytes:
        try:
            b = await asyncio.wait_for(reader.readexactly(1), timeout)
        except asyncio.IncompleteReadError as err:
            raise OtaError(f"Der Adapter hat die Verbindung beendet ({what}).") from err
        except asyncio.TimeoutError as err:
            raise OtaError(f"Keine Antwort vom Adapter ({what}).") from err
        code = b[0]
        if code in ERRORS:
            raise OtaError(ERRORS[code])
        if expect is not None and code not in expect:
            raise OtaError(f"Unerwartete Antwort 0x{code:02X} vom Adapter ({what}).")
        if n > 1:
            try:
                b += await asyncio.wait_for(reader.readexactly(n - 1), timeout)
            except (asyncio.IncompleteReadError, asyncio.TimeoutError) as err:
                raise OtaError(f"Antwort unvollständig ({what}).") from err
        return b

    async def tx(b: bytes) -> None:
        try:
            writer.write(b)
            await asyncio.wait_for(writer.drain(), 30)
        except asyncio.TimeoutError as err:
            raise OtaError("Senden hängt – Adapter antwortet nicht (Zeitlimit 30 s).") from err
        except OSError as err:
            raise OtaError(f"Senden fehlgeschlagen: {err}") from err

    result: dict[str, Any] = {}
    try:
        await tx(MAGIC)
        ver = (await rx(2, "Version", (R_OK,)))[1]
        if ver not in (1, 2):
            raise OtaError(f"OTA-Version {ver} des Adapters wird nicht unterstützt.")
        result["ota_version"] = ver
        await tx(bytes([CLIENT_SHA256]))
        feat = (await rx(1, "Merkmale"))[0]
        if feat != R_HEADER_OK:
            raise OtaError(f"Unerwartete Merkmale 0x{feat:02X} vom Adapter.")
        auth = (await rx(1, "Anmeldung", (R_AUTH_MD5, R_AUTH_SHA256, R_AUTH_OK)))[0]
        if auth != R_AUTH_OK:
            if not password:
                raise OtaError("Der Adapter verlangt ein OTA-Passwort – bitte unter „OTA-Passwort“ eintragen.")
            hf, size = (hashlib.sha256, 64) if auth == R_AUTH_SHA256 else (hashlib.md5, 32)
            nonce = (await rx(size, "Anmeldung")).decode("ascii", "replace")
            cnonce = secrets.token_hex(size // 2)
            await tx(cnonce.encode())
            h = hf()
            h.update(password.encode("utf-8"))
            h.update(nonce.encode())
            h.update(cnonce.encode())
            await tx(h.hexdigest().encode())
            await rx(1, "Passwort", (R_AUTH_OK,))
            result["auth"] = "SHA256" if auth == R_AUTH_SHA256 else "MD5"
        prog("prepare")
        await tx(len(data).to_bytes(4, "big"))
        await rx(1, "Speicher vorbereiten", (R_PREPARE_OK,), timeout=60)
        await tx(hashlib.md5(data).hexdigest().encode())
        await rx(1, "Prüfsumme", (R_MD5_OK,))
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, BLOCK * 8)
            except OSError:
                pass
        total = len(data)
        for off in range(0, total, BLOCK):
            await tx(data[off:off + BLOCK])
            if ver >= 2:
                await rx(1, "Datenblock", (R_CHUNK_OK,), timeout=120)
            prog("upload", min(1.0, (off + BLOCK) / total))
        prog("finish", 1.0)
        await rx(1, "Empfang", (R_RECEIVE_OK,), timeout=120)
        await rx(1, "Abschluss", (R_END_OK,), timeout=120)
        try:
            await tx(bytes([R_OK]))
        except OtaError:
            pass          # Adapter startet ohnehin neu
        return result
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 3)
        except Exception:  # noqa: BLE001
            pass
