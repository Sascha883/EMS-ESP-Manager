"""BSB-Bus (Siemens Albatros: BSB/LPB) – Telegramme, eigene Parametertabelle, Dekodierung.

Reiner Python-Code ohne Home-Assistant-Abhängigkeit (auch einzeln testbar).
Eigenentwicklung Sascha Matheis Haustechnik – Protokollwissen aus eigenen Bus-Mitschnitten.

Telegrammaufbau BSB (4800 Baud, 8O1, Bytes auf dem Bus invertiert):
    DC  SRC|0x80  DST  LEN  TYP  ID1 ID2 ID3 ID4  [Nutzdaten]  CRC-H CRC-L
    TYP: 2 = INF (Rundsendung), 3 = SET, 4 = ACK, 5 = NACK, 6 = GET (Abfrage), 7 = RET (Antwort), 8 = ERR
    Bei GET/SET/INF stehen ID1/ID2 vertauscht auf dem Bus, bei RET nicht.
    Nutzdaten einer Antwort: 1 Status-Byte (00 = gültig, 01/05 = „kein Wert“) + Wert (big endian).
    Prüfsumme: CRC-16, Polynom 0x1021, Startwert 0 (über alle Bytes vor der Prüfsumme).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .bsb_params import CODECS, ENUMS, GROUPS, PARAMS, PROBE_PRESETS, VERIFIED_DEVICES  # noqa: F401

# Bus-Adressen
ADDRESSES: dict[int, str] = {
    0x00: "Regler / Kessel (Hauptgerät)",
    0x01: "Regler 2 / Kaskade",
    0x03: "Erweiterungsmodul 1",
    0x04: "Erweiterungsmodul 2",
    0x06: "Raumgerät 1",
    0x07: "Raumgerät 2",
    0x08: "Raumgerät 3",
    0x0A: "Bedienteil (Display)",
    0x0B: "Service-Bedienteil",
    0x0F: "Funk-Raumgerät",
    0x31: "Web-Server",
    0x32: "Funkempfänger",
    0x36: "Fernbedienung",
    0x42: "ESP-Bus-Adapter (dieser Adapter)",
    0x7F: "Rundsendung an alle",
}
CMD_NAMES = {2: "INF", 3: "SET", 4: "ACK", 5: "NACK", 6: "GET", 7: "RET", 8: "ERR"}
CMD_FROM_TEXT = {"Inf": 2, "Set": 3, "Ack": 4, "Nack": 5, "Get": 6, "Ret": 7}

# Regler-Baureihen (Anfang der Geräte-Identifikation, Parameter 6224) – eigene Kurzbeschreibung
SERIES: list[tuple[str, str]] = [
    ("LMS1", "Siemens LMS – Feuerungsautomat mit eingebautem Regler (Gas-Brennwert)"),
    ("LMU", "Siemens LMU – Feuerungsautomat (Gas-Brennwert, ältere Reihe)"),
    ("RVS21", "Siemens RVS21 – Regler für Wärmepumpen"),
    ("RVS61", "Siemens RVS61 – Regler für Wärmepumpen"),
    ("RVS4", "Siemens RVS4x – Kessel- und Anlagenregler"),
    ("RVS5", "Siemens RVS5x – Kesselregler (Öl, Holz, Pellets)"),
    ("RVS6", "Siemens RVS6x – Zentral- und Anlagenregler"),
    ("RVS", "Siemens RVS – Albatros-2-Regler"),
    ("RVA", "Siemens RVA – ältere Albatros-Regler (meist LPB/PPS)"),
    ("RVC", "Siemens RVC – Kompaktregler"),
    ("RVD", "Siemens RVD – Fernwärme-/Übergaberegler"),
    ("RVL", "Siemens RVL – Heizungsregler"),
    ("RVP", "Siemens RVP – Heizungsregler"),
    ("AVS7", "Siemens AVS7x – Bedienteil, Funkempfänger oder Erweiterungsmodul"),
    ("AVS3", "Siemens AVS3x – Bedienteil"),
    ("QAA", "Siemens QAA – Raumgerät"),
    ("OCI", "Siemens OCI – LPB-Schnittstelle"),
    ("OZW", "Siemens OZW – Web-Server"),
    ("WRI", "Weishaupt-Regler (Siemens-Basis)"),
    ("RWI", "Weishaupt-Regler (Siemens-Basis)"),
]

# Hersteller, die Siemens-Albatros-Regler mit BSB/LPB einsetzen (öffentlich bekannte Gerätezuordnung)
MANUFACTURERS = [
    "Brötje", "Elco", "Atlantic", "Alarko", "ATAG", "Austria Email", "Baxi", "Bösch", "Chappée", "CTA", "CTC",
    "De Dietrich", "Ferroli", "Fröling", "Fujitsu Waterstage", "Geminox", "Gorenje", "Grünenwald", "Hansa", "Herz",
    "Ideal", "MHG", "Oilon", "Olymp", "Potterton", "Sieger", "Strebel", "Thermital", "Viadrus", "Weishaupt",
    "Viessmann (nur Vitotwin 300-W)",
]
# Geräte mit Fremdreglern – dort gibt es keinen BSB/LPB
NOT_SUPPORTED = [
    "neuere Brötje-Wärmepumpen (BLW Eco/Neo/Split ab 2020)", "Brötje-Wandgeräte der Reihe WBC/WGB x.1",
    "Brötje BOK und WLC/WLS", "Elco Aerotop EVO", "Geräte mit Siemens Climatix oder LMU34",
]


# --------------------------------------------------------------------- Tabelle
def _p(line: float, cmd: str, codec: str, name: str, enum: str | None, flags: str, alts: tuple, user: bool = False) -> dict[str, Any]:
    return {"line": line, "cmd": int(cmd, 16), "ids": [int(cmd, 16), *(int(a, 16) for a in alts)], "codec": codec,
            "name": name, "enum": enum, "ro": "r" in flags, "user": user}


class Catalog:
    """Eigene Parametertabelle + an der Anlage angelernte Werte."""

    def __init__(self, user: list[dict[str, Any]] | None = None) -> None:
        self.params: list[dict[str, Any]] = [_p(*row) for row in PARAMS]
        self.user_enums: dict[str, dict[int, str]] = {}
        for u in user or []:
            try:
                self.params.append(_p(float(u["line"]), u["cmd"], u.get("codec", "u16"), u["name"], u.get("enum"),
                                      "r" if u.get("ro", True) else "", tuple(), True))
            except (KeyError, ValueError):
                continue
        self.by_line: dict[float, dict[str, Any]] = {}
        self.by_cmd: dict[int, list[dict[str, Any]]] = {}
        for p in self.params:
            self.by_line[float(p["line"])] = p
            for i in p["ids"]:
                self.by_cmd.setdefault(i, []).append(p)

    @classmethod
    def load(cls, user: list[dict[str, Any]] | None = None) -> "Catalog":
        return cls(user)

    # Schnittstelle wie bisher (Gerätefamilie spielt keine Rolle mehr – Ausweich-IDs übernehmen das)
    def line(self, line: float, fam: int | None = None, var: int | None = None) -> dict[str, Any] | None:
        return self.by_line.get(float(line))

    def for_cmd(self, cmd: int, fam: int | None = None, var: int | None = None) -> list[dict[str, Any]]:
        return list(self.by_cmd.get(cmd, []))

    def category(self, line: float) -> str:
        for name, lo, hi in GROUPS:
            if lo <= line <= hi:
                return name
        return "Sonstige"

    def device_name(self, fam: int | None, var: int | None) -> str | None:
        return VERIFIED_DEVICES.get(f"{fam}/{var}") if fam is not None else None

    def scan_list(self, fam: int | None = None, var: int | None = None, scope: str = "quick") -> list[dict[str, Any]]:
        return [{"cmd": p["cmd"], "lines": [p]} for p in sorted(self.params, key=lambda x: x["line"])]

    def info(self, p: dict[str, Any]) -> dict[str, Any]:
        c = CODECS.get(p["codec"], CODECS["u16"])
        en = ENUMS.get(p["enum"]) if p.get("enum") else None
        return {"line": _num(p["line"]), "cmd": f"{p['cmd']:08X}", "type": p["codec"], "name": p["name"], "unit": c[4],
                "enum": [[k, v] for k, v in en.items()] if en else None, "ro": p["ro"], "cat": self.category(float(p["line"])),
                "user": p.get("user", False)}


def _num(v: float) -> int | float:
    return int(v) if float(v) == int(v) else v


# --------------------------------------------------------------------- Werte dekodieren
def decode(cat: Catalog | None, p: dict[str, Any], payload: bytes) -> dict[str, Any]:
    """Nutzdaten einer RET/INF-Antwort → {value, text, raw, valid}."""
    return decode_codec(p["codec"], p.get("enum"), payload)


def decode_codec(codec: str, enum: str | None, payload: bytes) -> dict[str, Any]:
    n, signed, div, prec, unit, kind = CODECS.get(codec, CODECS["u16"])
    raw = payload.hex(" ").upper()
    res: dict[str, Any] = {"raw": raw, "valid": True, "value": None, "text": "", "unit": unit}
    if kind == "str":
        s = payload[1:] if payload[:1] == b"\x00" and len(payload) > 1 else payload
        txt = s.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
        res.update(value=txt, text=txt)
        return res
    if kind == "dt":
        if len(payload) < 8 or payload[0] & 1:
            res.update(valid=False, text="—")
            return res
        y, mo, d, h, mi, s = payload[1], payload[2], payload[3], payload[5], payload[6], payload[7]
        txt = f"{d:02d}.{mo:02d}.{1900 + y} {h:02d}:{mi:02d}:{s:02d}"
        res.update(value=txt, text=txt)
        return res
    if len(payload) == n + 1:
        status, data = payload[0], payload[1:]
    elif len(payload) == n:
        status, data = 0, payload
    else:
        res.update(valid=False, text=f"Länge passt nicht ({raw})")
        return res
    if status in (0x01, 0x05):
        res.update(valid=False, text="—")
        return res
    val = int.from_bytes(data, "big", signed=signed)
    if kind in ("enum", "err"):
        names = ENUMS.get(enum or "", {}) if kind == "enum" else {}
        res.update(value=val, text=names.get(val, f"Code {val}"))
        return res
    num = val / div if div != 1 else val
    num = round(num, prec) if prec else int(round(num))
    txt = (f"{num:.{prec}f}" if prec else str(num)).replace(".", ",") + (f" {unit}" if unit else "")
    res.update(value=num, text=txt)
    return res


def guess(payload: bytes) -> list[dict[str, str]]:
    """Unbekannter Wert: mögliche Bedeutungen nach Länge der Nutzdaten (zum Anlernen)."""
    n = len(payload) - 1 if payload[:1] in (b"\x00", b"\x01", b"\x05") else len(payload)
    cands = {1: ["u8", "pct", "onoff", "enum"], 2: ["temp", "u16", "steil", "bar10", "kwh10"], 4: ["u32", "hours", "kwh"]}.get(n, [])
    out = []
    if len(payload) > 4 and all(32 <= b < 127 for b in payload.rstrip(b"\x00")[1:] or b"\x00"):
        cands = ["str", *cands]
    for c in cands:
        d = decode_codec(c, "onoff" if c == "onoff" else None, payload)
        out.append({"codec": c, "text": d["text"]})
    return out


# --------------------------------------------------------------------- Telegramme
@dataclass
class Telegram:
    direction: str          # "rx" (vom Bus) | "tx" (vom Adapter gesendet)
    cmd: int
    src: int
    dst: int
    field: int
    payload: bytes = b""
    raw: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def cmd_name(self) -> str:
        return CMD_NAMES.get(self.cmd, f"0x{self.cmd:02X}")

    def canonical_field(self) -> int:
        """Befehls-ID in Tabellenform.

        Auf dem Bus sind bei GET/SET/INF die ersten zwei Bytes vertauscht. Die ESPHome-Firmware
        gibt empfangene Telegramme so aus, wie sie auf dem Bus stehen, eigene gesendete dagegen
        schon in Tabellenform.
        """
        if self.extra.get("canonical"):
            return self.field
        if self.direction == "rx" and self.cmd in (2, 3, 6):
            return swap_id(self.field)
        return self.field


def swap_id(v: int) -> int:
    b = v.to_bytes(4, "big")
    return int.from_bytes(bytes([b[1], b[0], b[2], b[3]]), "big")


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Ausgabe der ESPHome-Firmware (Tag bsb.component, Stufe DEBUG):
#   <<< BSB Packet: Ret 00->42, len: 14, field: 053D0521, CRC: 1A2B, payload: 00.01.8C (3) (DC.80.42....)
_RE_PKT = re.compile(
    r"(?P<dir><<<|>>>)\s*BSB Packet:\s*(?:(?P<cmd>Inf|Set|Ack|Nack|Get|Ret)|UNK \((?P<unk>[0-9A-Fa-f]{2})\))\s+"
    r"(?P<src>[0-9A-Fa-f]{2})->(?P<dst>[0-9A-Fa-f]{2}),\s*len:\s*(?P<len>\d+),\s*field:\s*(?P<field>[0-9A-Fa-f]{8})"
    r"(?:,\s*CRC:\s*(?P<crc>[0-9A-Fa-f]{1,4}))?(?:,\s*payload:\s*(?P<pl>[0-9A-Fa-f]{2}(?:\.[0-9A-Fa-f]{2})*))?"
)


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


# Eigene Firmware „espbus“ (ESP-Bus-Adapter ab Firmware 2.0), Kennung immer in Tabellenform:
#   RX RET 00>42 id=053D0521 d=00.01.A0
#   TX GET 42>00 id=053D0521
_RE_ESPBUS = re.compile(
    r"\b(?P<dir>RX|TX) (?P<cmd>INF|SET|ACK|NACK|GET|RET|ERR|T[0-9A-F]{2}) (?P<src>[0-9A-F]{2})>(?P<dst>[0-9A-F]{2}) "
    r"id=(?P<field>[0-9A-F]{8})(?: d=(?P<pl>[0-9A-F]{2}(?:\.[0-9A-F]{2})*))?"
)
_ESPBUS_CMD = {"INF": 2, "SET": 3, "ACK": 4, "NACK": 5, "GET": 6, "RET": 7, "ERR": 8}


def parse_esphome_log(line: str) -> Telegram | None:
    """Eine Log-Zeile der ESPHome-Firmware → Telegram (sonst None)."""
    clean = strip_ansi(line)
    e = _RE_ESPBUS.search(clean) if "espbus" in clean else None
    if e:
        c = e["cmd"]
        cmd = _ESPBUS_CMD.get(c) or int(c[1:], 16)
        pl = bytes(int(x, 16) for x in e["pl"].split(".")) if e["pl"] else b""
        return Telegram(direction="rx" if e["dir"] == "RX" else "tx", cmd=cmd, src=int(e["src"], 16) & 0x7F,
                        dst=int(e["dst"], 16), field=int(e["field"], 16), payload=pl, raw=e.group(0),
                        extra={"canonical": True})
    m = _RE_PKT.search(clean)
    if not m:
        return None
    cmd = CMD_FROM_TEXT[m["cmd"]] if m["cmd"] else int(m["unk"], 16)
    pl = bytes(int(x, 16) for x in m["pl"].split(".")) if m["pl"] else b""
    return Telegram(direction="rx" if m["dir"] == "<<<" else "tx", cmd=cmd, src=int(m["src"], 16) & 0x7F,
                    dst=int(m["dst"], 16), field=int(m["field"], 16), payload=pl, raw=m.group(0))


def crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def build_get(field_id: int, dst: int = 0, src: int = 0x42) -> bytes:
    """GET-Telegramm (nicht invertiert) – Referenz für die Firmware-Aktion ``bsb_get``."""
    b = field_id.to_bytes(4, "big")
    body = bytes([0xDC, src | 0x80, dst, 11, 6, b[1], b[0], b[2], b[3]])
    c = crc16(body)
    return body + bytes([c >> 8, c & 0xFF])


# --------------------------------------------------------------------- Regler-Erkennung
def identify(cat: Catalog, ident: str | None, fam: int | None, var: int | None) -> dict[str, Any]:
    ident = (ident or "").strip()
    series = next((d for pre, d in SERIES if ident.upper().startswith(pre)), None)
    known = cat.device_name(fam, var)
    manu = next((m for m in MANUFACTURERS if known and m.split(" ")[0].lower() in known.lower()), None)
    return {
        "ident": ident or None, "family": fam, "variant": var, "series": series, "known_device": known,
        "manufacturer": manu, "lines": len(cat.params),
        "title": " · ".join(x for x in (ident or None, f"Familie {fam}" if fam is not None else None,
                                         f"Variante {var}" if var is not None else None) if x) or "unbekannt",
    }


def addr_name(addr: int) -> str:
    return ADDRESSES.get(addr, f"Gerät 0x{addr:02X}")
