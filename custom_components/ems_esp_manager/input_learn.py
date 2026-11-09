"""Anlern-Modus für die Funktion der externen Eingänge (Bosch CS5800i / UI 800).

Ergebnis Bus-Log 04.11.2026: Das UI 800 schreibt je Menüpunkt genau 1 Byte in 0x486 (Offsets wie
EMS-ESP, siehe unten), die CS5800i bestätigt jedes Byte per Broadcast. Der Eingangstest im Manager
schreibt genauso und prüft die Bestätigung.
Um herauszufinden, wie das UI 800 die Funktion eines Eingangs schreibt, schneidet der
Anlern-Modus den Bus mit, während am UI 800 die Funktion geändert wird:

1. Ausgangszustand: hpinNopt-Werte und gezielte Lesezugriffe (0x486 + Kandidaten)
2. Ruhephase (30 s): was ändert sich ohnehin ständig?
3. Anlernfenster: jetzt am UI 800 die Funktion ändern – jeder Schreibzugriff des UI 800
   und jedes geänderte Byte wird festgehalten
4. Endzustand: dieselben Lesezugriffe noch einmal, Vergleich vorher/nachher

Dieses Modul enthält nur die reine Auswertung (ohne Home Assistant) – testbar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

# "thermostat(0x10) W boiler(0x08), HpInConfig(0x0486), data: 01 00 (offset 23)"
_RE_LINE = re.compile(
    r"^(?P<src>\S+)\(0x(?P<s>[0-9A-Fa-f]{2})\)\s+(?P<op>[WRB])\s+(?P<dst>\S+)\(0x(?P<d>[0-9A-Fa-f]{2})\),\s*"
    r"(?P<tname>\S*)\(0x(?P<t>[0-9A-Fa-f]{2,4})\),\s*"
    r"(?:data:\s*(?P<data>(?:[0-9A-Fa-f]{2}\s?)+|<empty>)|length:\s*(?P<len>\d+))"
    r"(?:\s*\(offset\s+(?P<off>\d+)\))?"
)

# gezielte Lesezugriffe vorher/nachher: (Gerät, Typ, Offset, Länge)
READS: tuple[tuple[int, int, int, int], ...] = (
    (0x08, 0x486, 0, 27), (0x08, 0x486, 20, 8), (0x08, 0x486, 27, 27), (0x08, 0x486, 42, 12),
    (0x08, 0x4A2, 0, 27),
    (0x08, 0x4A8, 0, 27), (0x08, 0x4A9, 0, 27), (0x08, 0x4AB, 0, 27), (0x08, 0x4CA, 0, 27),
)

THERMOSTAT_IDS = (0x10, 0x18, 0x17)

# Status-/Messwert-Telegramme der CS5800i, die sich laufend ändern (Bus-Log 25.10.2026 15:07–15:24):
# Temperaturen, Leistung, Kompressor, Eingangszustände – keine Einstellungen
MEASURE_TYPES = frozenset({0xE3, 0xE4, 0xE5, 0xE9, 0x48D, 0x48F, 0x494, 0x495, 0x4A2, 0x4AA, 0x4AF})

# EMS-ESP boiler.cpp process_HpInConfig: Offset je Funktion (Eingang 1..3: + Eingang-1)
_OFF_123 = {"inv": 0, "evu1": 3, "evu2": 6, "evu3": 9, "comp": 12, "aux": 15, "cool": 18,
            "heat": 21, "dhw": 24, "mod": 27, "pres": 30, "prot": 36, "pv": 39}
_OFF_4 = {"inv": 42, "comp": 43, "aux": 44, "cool": 45, "heat": 46, "dhw": 47, "mod": 48,
          "pres": 49, "prot": 50, "pv": 52}
FUNC_TEXT = {
    "inv": "Eingang invertiert", "evu1": "EVU-Sperrzeit 1", "evu2": "EVU-Sperrzeit 2", "evu3": "EVU-Sperrzeit 3",
    "comp": "Kompressor sperren", "aux": "Zuheizerbetr. sperren", "cool": "Kühlbetrieb sperren",
    "heat": "Heizbetrieb sperren", "dhw": "Warmwasser sperren", "mod": "Begrenzungswert/Modulation",
    "pres": "Druckschalter", "prot": "Überhitzungsschutz", "pv": "Photovoltaikanlage",
}


def hpin_offset_text(offset: int) -> str | None:
    """0x486-Offset -> „Eingang n: Funktion“ nach der EMS-ESP-Zuordnung."""
    for key, base in _OFF_123.items():
        if base <= offset <= base + 2:
            return f"Eingang {offset - base + 1}: {FUNC_TEXT[key]}"
    for key, off in _OFF_4.items():
        if off == offset:
            return f"Eingang 4: {FUNC_TEXT[key]}"
    return None


@dataclass
class Tel:
    src: int
    dst: int
    op: str
    type: int
    offset: int
    data: list[int] | None      # None = Leseanfrage
    src_name: str = ""
    dst_name: str = ""
    type_name: str = ""

    @property
    def label(self) -> str:
        n = self.type_name if self.type_name and self.type_name != "?" else ""
        return f"0x{self.type:04X}" + (f" {n}" if n else "")


def parse_line(line: str) -> Tel | None:
    m = _RE_LINE.match(line.strip())
    if not m:
        return None
    raw = m.group("data")
    data: list[int] | None
    if raw is None:
        data = None
    elif raw.strip() == "<empty>":
        data = []
    else:
        data = [int(x, 16) for x in raw.split()]
    return Tel(
        src=int(m.group("s"), 16) & 0x7F, dst=int(m.group("d"), 16) & 0x7F, op=m.group("op"),
        type=int(m.group("t"), 16), offset=int(m.group("off") or 0), data=data,
        src_name=m.group("src"), dst_name=m.group("dst"), type_name=m.group("tname"),
    )


def hx(b: int | None) -> str:
    return "––" if b is None else f"{b:02X}"


@dataclass
class Recorder:
    """Sammelt Telegramme in Phasen und wertet Byte-Änderungen aus."""

    me: int = 0x0B
    phase: str = "vorher"
    events: list[tuple[float, str, Tel]] = field(default_factory=list)
    # Bytewerte je (Absender, Typ, Offset) – zuletzt gesehen
    state: dict[tuple[int, int, int], int] = field(default_factory=dict)
    # (Absender, Typ, Offset) die sich in der Ruhephase geändert haben = „ändern sich ständig“
    noisy: set[tuple[int, int, int]] = field(default_factory=set)
    # Änderungen im Anlernfenster: (Zeit, Absender, Typ, Offset, alt, neu, Tel)
    changes: list[tuple[float, int, int, int, int | None, int, Tel]] = field(default_factory=list)
    # Schreibkombinationen (Absender, Ziel, Typ, Offset, Daten) aus der Ruhephase
    baseline_writes: set[tuple] = field(default_factory=set)
    # Antworten auf eigene Lesezugriffe je Phase: phase -> {(Typ, Offset): Byte}
    snapshots: dict[str, dict[tuple[int, int], int]] = field(default_factory=dict)
    max_events: int = 6000

    def feed(self, t: float, line: str) -> Tel | None:
        tel = parse_line(line)
        if tel is None:
            return None
        if len(self.events) < self.max_events:
            self.events.append((t, self.phase, tel))
        if not tel.data:
            return tel
        # Antwort auf eigene Leseanfrage -> Schnappschuss der Phase
        if tel.dst == self.me and tel.src != self.me:
            snap = self.snapshots.setdefault(self.phase, {})
            for i, b in enumerate(tel.data):
                snap[(tel.type, tel.offset + i)] = b
        if tel.src == self.me:
            return tel
        if self.phase == "ruhe" and tel.op == "W":
            self.baseline_writes.add((tel.src, tel.dst, tel.type, tel.offset, tuple(tel.data)))
        for i, b in enumerate(tel.data):
            key = (tel.src, tel.type, tel.offset + i)
            old = self.state.get(key)
            if old is not None and old != b:
                if self.phase == "ruhe":
                    self.noisy.add(key)
                elif self.phase == "fenster" and key not in self.noisy:
                    self.changes.append((t, tel.src, tel.type, tel.offset + i, old, b, tel))
            self.state[key] = b
        return tel

    # ------------------------------------------------------------ auswertung
    def ui_writes(self) -> list[tuple[float, Tel, bool]]:
        """Schreibzugriffe eines Bediengeräts (UI 800) an andere Geräte im Fenster; bool = neu."""
        out: list[tuple[float, Tel, bool]] = []
        seen: set[tuple] = set()
        for t, phase, tel in self.events:
            if phase != "fenster" or tel.op != "W" or not tel.data or tel.src == self.me:
                continue
            if tel.src not in THERMOSTAT_IDS or tel.dst in (0x00, self.me):
                continue
            key = (tel.src, tel.dst, tel.type, tel.offset, tuple(tel.data))
            if key in seen:
                continue
            seen.add(key)
            out.append((t, tel, key not in self.baseline_writes))
        return out

    def setting_changes(self) -> list[tuple[float, int, int, int, int | None, int, Tel]]:
        """Byteänderungen, die nach einer Einstellung aussehen: kein Messwert-Telegramm und
        im Fenster genau einmal geändert (Messwerte schwanken mehrfach)."""
        count: dict[tuple[int, int, int], int] = {}
        for c in self.changes:
            count[(c[1], c[2], c[3])] = count.get((c[1], c[2], c[3]), 0) + 1
        out = [c for c in self.changes if c[2] not in MEASURE_TYPES and count[(c[1], c[2], c[3])] == 1]
        out.sort(key=lambda c: (c[2] != 0x486, c[0]))
        return out

    def snapshot_diff(self) -> list[tuple[int, int, int | None, int | None]]:
        a, b = self.snapshots.get("vorher", {}), self.snapshots.get("nachher", {})
        out = []
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                out.append((key[0], key[1], a.get(key), b.get(key)))
        return out

    def findings(self) -> list[str]:
        """Kurze Zusammenfassung der wahrscheinlichen Kandidaten."""
        out: list[str] = []
        for t, tel, new in self.ui_writes():
            if not new:
                continue
            extra = ""
            if tel.type == 0x486:
                names = [hpin_offset_text(tel.offset + i) for i in range(len(tel.data or []))]
                extra = " → " + ", ".join(n for n in names if n) if any(names) else ""
            out.append(f"UI schreibt {tel.label} an 0x{tel.dst:02X}, Offset {tel.offset}: "
                       f"{' '.join(hx(x) for x in tel.data or [])}{extra}")
        for t, src, typ, off, old, new, tel in self.setting_changes():
            if typ == 0x486:
                out.append(f"Wärmepumpe meldet 0x0486 Offset {off}: {hx(old)} → {hx(new)} ({hpin_offset_text(off) or '?'})")
        for typ, off, old, new in self.snapshot_diff():
            extra = hpin_offset_text(off) if typ == 0x486 else None
            out.append(f"Gelesen 0x{typ:04X} Offset {off}: {hx(old)} → {hx(new)}" + (f" ({extra})" if extra else ""))
        return out


def opt_diff(before: dict[int, Any], after: dict[int, Any]) -> list[str]:
    """hpinNopt vorher/nachher -> Textzeilen."""
    out = []
    for n in range(1, 5):
        a, b = before.get(n), after.get(n)
        if a != b:
            out.append(f"hpin{n}opt: {a} → {b}")
    return out
