"""Zeitprogramme und Regler-Uhr des Siemens-BSB-Reglers (Dashboard „<Hersteller> EHS“).

Eigenentwicklung Sascha Matheis Haustechnik – Lizenz wie der EMS-ESP Manager (PolyForm Noncommercial 1.0.0).

Zwei Wege zum Regler:

* **ESP-Bus-Adapter ab Firmware 2.6.0** – liest die Zeitprogramme selbst (Text-Entitäten
  „Zeitprogramm Heizkreis 1/2/3“, „Zeitprogramm Trinkwasser“, Format ``Mo|Di|…|So``, Phasen mit Komma,
  „?“ = noch nicht gelesen) und die Uhr („Regler Datum und Uhrzeit“, „Regler-Uhr Abweichung“).
  Geschrieben wird über die API-Aktionen ``<node>_bsb_zeitprogramm`` und ``<node>_bsb_uhrzeit``.
* **BSB-LAN** – Parameter 501–507 / 521–527 / 541–547 / 561–567 (Zeitprogramme) und 0 (Uhr) über /JQ und /JS.

Nach außen (Panel) einheitlich: ``snapshot()`` liefert je Programm den Text im Format der Adapter-Firmware.
"""

from __future__ import annotations

import re
import time
from typing import Any

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

ESPHOME = "esphome"
BSBLAN = "bsblan"

# (Schlüssel, Name, erste BSB-LAN-Zeile Montag, Endung der Entität im ESP-Bus-Adapter)
PROGS: list[tuple[str, str, int, str]] = [
    ("hk1", "Heizkreis 1", 501, "zeitprogramm_heizkreis_1"),
    ("hk2", "Heizkreis 2", 521, "zeitprogramm_heizkreis_2"),
    ("hk3", "Heizkreis 3", 541, "zeitprogramm_heizkreis_3"),
    ("tww", "Trinkwasser", 561, "zeitprogramm_trinkwasser"),
]
SUF_UHR = "regler_datum_und_uhrzeit"
SUF_ABW = "regler_uhr_abweichung"
SUF_AUTO = "regler_uhr_automatisch_stellen"
SUF_LESEN = "zeitprogramme_neu_lesen"
NICHT_DA = "nicht vorhanden"

_RE_PHASE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


# --------------------------------------------------------------------------- Prüfen
def phasen_pruefen(phasen: str) -> list[tuple[int, int]]:
    """„06:00-08:00,16:00-22:00“ → [(360, 480), (960, 1320)]; leer/„-“ = ganzer Tag aus. ValueError bei Fehler."""
    s = (phasen or "").strip()
    if s in ("", "-", "x", "X"):
        return []
    teile = [t.strip() for t in re.split(r"[,;_]", s) if t.strip()]
    if len(teile) > 3:
        raise ValueError("höchstens 3 Phasen je Tag")
    out: list[tuple[int, int]] = []
    for t in teile:
        m = _RE_PHASE.fullmatch(t)
        if not m:
            raise ValueError(f"„{t}“ – Format hh:mm-hh:mm")
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        if h1 > 24 or h2 > 24 or m1 > 59 or m2 > 59 or (h1 == 24 and m1) or (h2 == 24 and m2):
            raise ValueError(f"„{t}“ – ungültige Uhrzeit")
        a, b = h1 * 60 + m1, h2 * 60 + m2
        if b <= a:
            raise ValueError(f"„{t}“ – Ende muss nach dem Beginn liegen")
        if out and a < out[-1][1]:
            raise ValueError("Phasen aufsteigend und ohne Überschneidung angeben")
        out.append((a, b))
    return out


def phasen_text(ph: list[tuple[int, int]]) -> str:
    return ",".join(f"{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}" for a, b in ph)


def tage_pruefen(tage: str) -> list[int]:
    t = (tage or "").replace(" ", "").replace(",", "")
    if t == "*":
        return list(range(7))
    if not t or any(c not in "0123456" for c in t):
        raise ValueError("Tage als Ziffern 0–6 (0 = Montag) oder *")
    return sorted({int(c) for c in t})


# --------------------------------------------------------------------------- ESPHome
def _entities(ad) -> dict[str, str]:
    """Endung → entity_id der Zeit-Entitäten des ESP-Bus-Adapters."""
    e = ad._esphome_entry()
    if e is None:
        return {}
    wanted = [p[3] for p in PROGS] + [SUF_UHR, SUF_ABW, SUF_AUTO, SUF_LESEN]
    out: dict[str, str] = {}
    for ent in er.async_entries_for_config_entry(er.async_get(ad.hass), e.entry_id):
        if ent.disabled_by:
            continue
        obj = ent.entity_id.split(".", 1)[1]
        for suf in wanted:
            if (obj == suf or obj.endswith("_" + suf)) and suf not in out:
                out[suf] = ent.entity_id
    return out


def _svc(ad, name: str) -> str | None:
    node = ad._node()
    s = f"{node}_{name}"
    return s if node and ad.hass.services.has_service(ESPHOME, s) else None


def _state(ad, eid: str | None) -> tuple[str | None, float | None]:
    if not eid:
        return None, None
    s = ad.hass.states.get(eid)
    if s is None or s.state in ("unknown", "unavailable"):
        return None, None
    return s.state, s.last_updated.timestamp()


# --------------------------------------------------------------------------- Zustand fürs Panel
def snapshot(ad) -> dict[str, Any]:
    c = ad.cfg
    kind = c.get("kind")
    out: dict[str, Any] = {"kind": kind, "programs": {}, "uhr": {}, "caps": {"zp": False, "uhr": False}}
    if kind == ESPHOME:
        ents = _entities(ad)
        out["caps"] = {"zp": bool(_svc(ad, "bsb_zeitprogramm")), "uhr": bool(_svc(ad, "bsb_uhrzeit")),
                       "lesen": SUF_LESEN in ents, "auto": SUF_AUTO in ents}
        for k, name, _ln, suf in PROGS:
            eid = ents.get(suf)
            st, ts = _state(ad, eid)
            out["programs"][k] = {"name": name, "entity": eid, "state": st, "ts": ts}
        st, ts = _state(ad, ents.get(SUF_UHR))
        abw, _ = _state(ad, ents.get(SUF_ABW))
        auto, _ = _state(ad, ents.get(SUF_AUTO))
        out["uhr"] = {"entity": ents.get(SUF_UHR), "state": st, "ts": ts, "abw_entity": ents.get(SUF_ABW),
                      "abw": _num(abw), "auto_entity": ents.get(SUF_AUTO), "auto": auto == "on" if auto else None}
        if not out["caps"]["zp"]:
            out["hint"] = "Zeitprogramme und Regler-Uhr brauchen die Adapter-Firmware ab 2.6.0."
    elif kind == BSBLAN:
        z = ad.st.get("zeit") or {}
        out["caps"] = {"zp": True, "uhr": True, "lesen": True, "auto": False}
        for k, name, _ln, _suf in PROGS:
            p = (z.get("programs") or {}).get(k) or {}
            out["programs"][k] = {"name": name, "entity": None, "state": p.get("state"), "ts": p.get("ts")}
        u = z.get("uhr") or {}
        out["uhr"] = {"entity": None, "state": u.get("state"), "ts": u.get("ts"), "abw": u.get("abw"), "auto": None}
        out["read_at"] = z.get("t")
    return out


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _jetzt_text() -> str:
    return dt_util.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- Aktionen
async def set_zp(ad, prog: str, tage: str, phasen: str) -> dict[str, Any]:
    keys = [p[0] for p in PROGS]
    if prog not in keys:
        raise ValueError(f"Zeitprogramm „{prog}“ unbekannt")
    ph = phasen_pruefen(phasen)
    td = tage_pruefen(tage)
    txt = phasen_text(ph)
    c = ad.cfg
    if c.get("kind") == ESPHOME:
        svc = _svc(ad, "bsb_zeitprogramm")
        if not svc:
            raise RuntimeError("Adapter-Firmware ab 2.6.0 nötig (Aktion bsb_zeitprogramm fehlt)")
        await ad.hass.services.async_call(ESPHOME, svc, {"programm": prog, "tage": "".join(str(d) for d in td),
                                                         "schaltzeiten": txt}, blocking=True)
    elif c.get("kind") == BSBLAN:
        first = next(p[2] for p in PROGS if p[0] == prog)
        # BSB-LAN: Phasen mit „_“, freie Phasen weglassen
        val = "_".join(t for t in txt.split(",") if t) if txt else "--:---"
        for d in td:
            data = await ad.bsblan("JS", {"Parameter": str(first + d), "Value": val, "Type": "1",
                                          "Destination": str(c.get("dest", 0))})
            r = (data or {}).get(str(first + d)) or {}
            if int(r.get("status", 0) or 0) != 1:
                ad._log("tx", f"Zeitprogramm {prog} Zeile {first + d} = {val} → abgelehnt", "WARNING", force=True)
        await read_bsblan(ad, [prog])
    else:
        raise RuntimeError("Kein Adapter gewählt")
    ad._log("tx", f"Zeitprogramm {prog} Tage {''.join(str(d) for d in td)} → {txt or 'aus'}", "INFO", force=True)
    return {"ok": True, "prog": prog, "tage": td, "phasen": txt}


async def set_uhr(ad, zeit: str | None = None) -> dict[str, Any]:
    """Regler-Uhr stellen – ohne Angabe auf die Zeit von Home Assistant."""
    z = (zeit or "").strip() or _jetzt_text()
    c = ad.cfg
    if c.get("kind") == ESPHOME:
        svc = _svc(ad, "bsb_uhrzeit")
        if not svc:
            raise RuntimeError("Adapter-Firmware ab 2.6.0 nötig (Aktion bsb_uhrzeit fehlt)")
        await ad.hass.services.async_call(ESPHOME, svc, {"datum_zeit": z}, blocking=True)
    elif c.get("kind") == BSBLAN:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", z)
        if not m:
            raise ValueError("Zeit als JJJJ-MM-TT hh:mm:ss")
        y, mo, d, h, mi, s = m.groups()
        val = f"{d}.{mo}.{y}_{h}:{mi}:{s or '00'}"
        await ad.bsblan("JS", {"Parameter": "0", "Value": val, "Type": "1", "Destination": str(c.get("dest", 0))})
        await read_bsblan(ad, [])
    else:
        raise RuntimeError("Kein Adapter gewählt")
    ad._log("tx", f"Regler-Uhr stellen auf {z}", "INFO", force=True)
    return {"ok": True, "zeit": z}


async def set_auto(ad, on: bool) -> None:
    eid = _entities(ad).get(SUF_AUTO)
    if not eid:
        raise RuntimeError("Schalter „Regler-Uhr automatisch stellen“ nicht gefunden (Adapter-Firmware ab 2.6.0)")
    await ad.hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": eid}, blocking=True)


async def lesen(ad) -> None:
    c = ad.cfg
    if c.get("kind") == ESPHOME:
        eid = _entities(ad).get(SUF_LESEN)
        if not eid:
            raise RuntimeError("Adapter-Firmware ab 2.6.0 nötig")
        await ad.hass.services.async_call("button", "press", {"entity_id": eid}, blocking=True)
    elif c.get("kind") == BSBLAN:
        await read_bsblan(ad, [p[0] for p in PROGS])


# --------------------------------------------------------------------------- BSB-LAN
def _bsblan_tag(d: dict[str, Any]) -> str | None:
    if not isinstance(d, dict) or int(d.get("error") or 0):
        return None
    v = str(d.get("value") or "")
    ph = [(int(a) * 60 + int(b), int(c) * 60 + int(e)) for a, b, c, e in _RE_PHASE.findall(v)]
    return phasen_text([p for p in ph if p[1] > p[0]])


async def read_bsblan(ad, progs: list[str]) -> None:
    z = ad.st.setdefault("zeit", {})
    pr = z.setdefault("programs", {})
    lines = ["0"]
    for k, _n, first, _s in PROGS:
        if k in progs:
            lines += [str(first + i) for i in range(7)]
    data = await ad.bsblan("JQ=" + ",".join(lines)) or {}
    now = time.time()
    for k, _n, first, _s in PROGS:
        if k not in progs:
            continue
        tage = [_bsblan_tag(data.get(str(first + i))) for i in range(7)]
        if all(t is None for t in tage):
            pr[k] = {"state": NICHT_DA, "ts": now}
        else:
            pr[k] = {"state": "|".join("?" if t is None else t for t in tage), "ts": now}
    u = data.get("0")
    if isinstance(u, dict) and not int(u.get("error") or 0):
        m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2}):(\d{2})", str(u.get("value") or ""))
        if m:
            d, mo, y, h, mi, s = (int(x) for x in m.groups())
            st = f"{d:02d}.{mo:02d}.{y:04d} {h:02d}:{mi:02d}:{s:02d}"
            try:
                reg = dt_util.as_local(dt_util.now()).replace(year=y, month=mo, day=d, hour=h, minute=mi, second=s,
                                                              microsecond=0)
                abw = round((reg - dt_util.now()).total_seconds())
            except ValueError:
                abw = None
            z["uhr"] = {"state": st, "ts": now, "abw": abw}
    z["t"] = now
    await ad.mgr.async_save()
