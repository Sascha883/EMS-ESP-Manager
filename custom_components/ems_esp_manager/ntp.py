"""Clock / NTP settings of EMS-ESP (/rest/ntpSettings, /rest/ntpStatus, /rest/time).

Without a working time server the EMS-ESP clock drifts or keeps an old date
(bus log 19.10.2026: EMS-ESP was 2 days 9 h behind) – log times can then not
be matched with Home Assistant. This page shows the clock of EMS-ESP next to
the Home Assistant time and lets an admin change the NTP settings or set the
EMS-ESP clock from Home Assistant once.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import EmsEspError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

URL_NTP_CARD = "/api/ems_esp_manager/ntp/card"
_DATA = f"{DOMAIN}_ntp_http"

# field -> type, as EMS-ESP stores it (src/ESP32React/NTPSettingsService.cpp).
# thermostat_sync / tz_*_t only exist in newer firmware – only fields EMS-ESP
# itself sends are shown and written back.
FIELDS: dict[str, type] = {
    "enabled": bool, "server": str, "tz_label": str, "tz_format": str,
    "thermostat_sync": int, "tz_label_t": str, "tz_format_t": str,
}

# time zone label -> POSIX TZ string (same strings as the EMS-ESP web UI)
TIME_ZONES: dict[str, str] = {
    "Europe/Berlin": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Vienna": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Zurich": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Amsterdam": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Brussels": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Luxembourg": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Paris": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Rome": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Madrid": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Copenhagen": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Oslo": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Stockholm": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Warsaw": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Prague": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/Budapest": "CET-1CEST,M3.5.0,M10.5.0/3",
    "Europe/London": "GMT0BST,M3.5.0/1,M10.5.0",
    "Europe/Dublin": "IST-1GMT0,M10.5.0,M3.5.0/1",
    "Europe/Lisbon": "WET0WEST,M3.5.0/1,M10.5.0",
    "Europe/Helsinki": "EET-2EEST,M3.5.0/3,M10.5.0/4",
    "Europe/Athens": "EET-2EEST,M3.5.0/3,M10.5.0/4",
    "Etc/UTC": "UTC0",
}
NTP_STATES = {0: "aus", 1: "aktiv, noch nicht synchronisiert", 2: "synchronisiert"}


def _runtime(hass: HomeAssistant):
    for e in hass.config_entries.async_entries(DOMAIN):
        if e.state is ConfigEntryState.LOADED:
            return e.runtime_data
    return None


def _clean(changes: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in changes.items():
        typ = FIELDS.get(key)
        if typ is None or key not in current:
            raise ValueError(f"Unbekanntes Feld: {key}")
        if typ is bool:
            out[key] = bool(value)
        elif typ is int:
            out[key] = int(value)
            if not 0 <= out[key] <= 2:
                raise ValueError(f"{key}: Wert außerhalb 0…2")
        else:
            out[key] = str(value).strip()[:64]
    # the POSIX string always follows the chosen zone
    for label, fmt in (("tz_label", "tz_format"), ("tz_label_t", "tz_format_t")):
        if label in out and out[label] in TIME_ZONES and fmt in current:
            out[fmt] = TIME_ZONES[out[label]]
    if out.get("enabled", current.get("enabled")) and not out.get("server", current.get("server")):
        raise ValueError("Bitte einen Zeitserver eintragen (z. B. de.pool.ntp.org)")
    return out


async def _get_settings(api) -> dict[str, Any]:
    status, data = await api._request("GET", "/rest/ntpSettings")  # noqa: SLF001
    if status != 200 or not isinstance(data, dict):
        raise EmsEspError(f"ntpSettings returned HTTP {status}")
    return data


async def _status(api) -> dict[str, Any] | None:
    """NTP state and EMS-ESP clock, with the difference to Home Assistant."""
    try:
        status, data = await api._request("GET", "/rest/ntpStatus")  # noqa: SLF001
    except EmsEspError:
        return None
    if status != 200 or not isinstance(data, dict):
        return None
    out = dict(data)
    out["state_text"] = NTP_STATES.get(data.get("status"), str(data.get("status")))
    now = dt_util.utcnow()
    out["ha_time"] = dt_util.as_local(now).strftime("%Y-%m-%dT%H:%M:%S")
    esp = dt_util.parse_datetime(str(data.get("utc_time") or ""))
    if esp is not None and esp.tzinfo is not None:
        out["offset_s"] = round((esp - now).total_seconds())
    return out


def _payload(settings: dict[str, Any], status: dict[str, Any] | None) -> dict[str, Any]:
    return {"settings": settings, "status": status, "zones": TIME_ZONES}


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/ntp/get"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_ntp_get(hass: HomeAssistant, connection, msg) -> None:
    rt = _runtime(hass)
    if rt is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    try:
        settings = await _get_settings(rt.api)
    except EmsEspError as err:
        connection.send_error(msg["id"], "ems_esp", str(err))
        return
    connection.send_result(msg["id"], _payload(settings, await _status(rt.api)))


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/ntp/set", vol.Required("changes"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_ntp_set(hass: HomeAssistant, connection, msg) -> None:
    rt = _runtime(hass)
    if rt is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    try:
        current = await _get_settings(rt.api)
        changes = _clean(msg["changes"], current)
        # EMS-ESP fills missing fields with defaults -> always send the complete object
        status, data = await rt.api._request("POST", "/rest/ntpSettings", {**current, **changes})  # noqa: SLF001
        if status not in (200, 202):
            raise EmsEspError(f"ntpSettings returned HTTP {status}")
    except (ValueError, EmsEspError) as err:
        connection.send_error(msg["id"], "ems_esp", str(err))
        return
    settings = data if isinstance(data, dict) else {**current, **changes}
    _LOGGER.info("EMS-ESP Uhrzeit-Einstellungen geändert: %s", changes)
    if rt.log is not None:
        rt.log.note("Uhrzeit-Einstellungen geändert: " + ", ".join(f"{k}={v}" for k, v in changes.items()))
    connection.send_result(msg["id"], _payload(settings, await _status(rt.api)))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/ntp/set_time"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_ntp_set_time(hass: HomeAssistant, connection, msg) -> None:
    """Set the EMS-ESP clock to the current Home Assistant time (local time)."""
    rt = _runtime(hass)
    if rt is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    local = dt_util.now().strftime("%Y-%m-%dT%H:%M:%S")
    try:
        status, _ = await rt.api._request("POST", "/rest/time", {"local_time": local})  # noqa: SLF001
        if status != 200:
            raise EmsEspError(f"time returned HTTP {status}")
        settings = await _get_settings(rt.api)
    except EmsEspError as err:
        connection.send_error(msg["id"], "ems_esp", str(err))
        return
    if rt.log is not None:
        rt.log.note(f"Uhrzeit des EMS-ESP auf Home-Assistant-Zeit gesetzt ({local})")
    connection.send_result(msg["id"], _payload(settings, await _status(rt.api)))


class NtpCardView(HomeAssistantView):
    """Form page for an iframe card – data only via the HA websocket (admin)."""

    url = URL_NTP_CARD
    name = "api:ems_esp_manager:ntp_card"
    requires_auth = False  # no data in the page itself

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(text=PAGE, content_type="text/html", charset="utf-8",
                            headers={"Cache-Control": "no-store"})


def async_setup_ntp(hass: HomeAssistant) -> None:
    if hass.data.get(_DATA):
        return
    hass.http.register_view(NtpCardView())
    for cmd in (ws_ntp_get, ws_ntp_set, ws_ntp_set_time):
        websocket_api.async_register_command(hass, cmd)
    hass.data[_DATA] = True


PAGE = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EMS-ESP Uhrzeit</title>
<style>
:root{color-scheme:light dark;--fg:#212121;--dim:#727272;--line:#e0e0e0;--acc:#03a9f4;--err:#db4437;--ok:#0f9d58;--warn:#f4b400;--card:#fff;--in:#f5f5f5}
@media (prefers-color-scheme:dark){:root{--fg:#e1e1e1;--dim:#9b9b9b;--line:#3a3a3a;--card:#1c1c1c;--in:#2a2a2a}}
html,body{margin:0;background:transparent;color:var(--fg);font:14px/1.45 Roboto,"Segoe UI",Arial,sans-serif}
body{padding:8px 16px 80px;max-width:760px;margin:0 auto}
h2{font-size:16px;font-weight:500;margin:22px 0 6px;padding-bottom:4px;border-bottom:1px solid var(--line)}
.st{display:grid;grid-template-columns:auto 1fr;gap:6px 18px;padding:10px 12px;border:1px solid var(--line);border-radius:8px}
.st span:nth-child(odd){color:var(--dim)}
b.ok{color:var(--ok)} b.bad{color:var(--err)} b.warn{color:var(--warn)}
label.f{display:flex;flex-direction:column;gap:3px;padding:6px 0;font-size:13px;color:var(--dim)}
label.c{display:flex;align-items:center;gap:8px;padding:8px 0;cursor:pointer}
input[type=text],select{font:inherit;color:var(--fg);background:var(--in);border:1px solid var(--line);border-radius:6px;padding:7px 9px}
input[type=checkbox]{width:18px;height:18px;accent-color:var(--acc)}
.dirty{outline:2px solid var(--acc)}
.hint{font-size:12px;color:var(--dim);margin:2px 0 6px}
.bar{position:fixed;left:0;right:0;bottom:0;display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;align-items:center;padding:10px 16px;background:var(--card);border-top:1px solid var(--line)}
.bar #msg{flex:1;min-width:160px;font-size:13px;color:var(--dim)}
button{font:inherit;font-size:13px;font-weight:500;border:0;border-radius:16px;padding:7px 14px;cursor:pointer;background:var(--acc);color:#fff}
button.sec{background:transparent;color:var(--acc);border:1px solid var(--acc)}
button:disabled{opacity:.45;cursor:default}
.err{color:var(--err)!important}
.hide{display:none}
</style></head><body>
<h2 style="margin-top:6px">Uhrzeit</h2>
<div id="status" class="st">Lade …</div>
<h2>Zeitserver (NTP)</h2>
<form id="f" autocomplete="off"></form>
<div class="bar"><span id="msg"></span>
  <button id="sync" class="sec" type="button">Uhrzeit von Home Assistant übernehmen</button>
  <button id="undo" class="sec" type="button" disabled>Verwerfen</button>
  <button id="save" type="button" disabled>Übernehmen</button></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let hass=null, orig={}, cur={}, zones={};
function H(){ try{ const ha=window.parent.document.querySelector('home-assistant'); return ha&&ha.hass; }catch(e){ return null; } }
const fmt=s=>{ if(!s) return '–'; const d=new Date(String(s)); return isNaN(d)?esc(s):d.toLocaleString('de-DE',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}); };
function off(s){ if(s==null) return ''; const a=Math.abs(s); const t=a<120?a+' s':a<7200?Math.round(a/60)+' min':a<172800?(a/3600).toFixed(1).replace('.',',')+' h':(a/86400).toFixed(1).replace('.',',')+' Tage';
  return a<=5?'<b class="ok">stimmt</b>':`<b class="${a<=120?'warn':'bad'}">${s<0?'geht '+t+' nach':'geht '+t+' vor'}</b>`; }
function showStatus(s){
  if(!s){ $('#status').innerHTML='<span>Status</span><span class="err">nicht abrufbar</span>'; return; }
  const cls=s.status===2?'ok':s.status===1?'warn':'bad';
  $('#status').innerHTML=`<span>EMS-ESP-Zeit</span><span>${fmt(s.local_time)}</span>
    <span>Home Assistant</span><span>${fmt(s.ha_time)}</span>
    <span>Abweichung</span><span>${off(s.offset_s)||'–'}</span>
    <span>Zeitserver</span><span><b class="${cls}">${esc(s.state_text)}</b>${s.server&&s.status?' · '+esc(s.server):''}</span>`;
}
function render(){
  const z=Object.keys(zones); if(cur.tz_label&&!z.includes(cur.tz_label)) z.unshift(cur.tz_label);
  const zsel=(name,v)=>`<select name="${name}">${z.map(x=>`<option ${x===v?'selected':''}>${esc(x)}</option>`).join('')}</select>`;
  let h=`<label class="c" data-k="enabled"><input type="checkbox" name="enabled" ${cur.enabled?'checked':''}> Uhrzeit über Zeitserver (NTP) stellen</label>
    <div class="hint">Empfohlen. Der EMS-ESP braucht dafür Internetzugang (oder einen Zeitserver im Heimnetz, z. B. die Fritz!Box: fritz.box).</div>
    <label class="f" data-k="server">Zeitserver<input type="text" name="server" value="${esc(cur.server)}" placeholder="de.pool.ntp.org"></label>
    <label class="f" data-k="tz_label">Zeitzone${zsel('tz_label',cur.tz_label)}</label>`;
  if('thermostat_sync' in orig) h+=`<label class="f" data-k="thermostat_sync">Uhrzeit des Reglers mit EMS-ESP abgleichen<select name="thermostat_sync">
      <option value="0" ${+cur.thermostat_sync===0?'selected':''}>Aus</option><option value="1" ${+cur.thermostat_sync===1?'selected':''}>Ein</option><option value="2" ${+cur.thermostat_sync===2?'selected':''}>Ein, mit eigener Zeitzone</option></select></label>
    <div class="hint">Stellt die Uhr des EMS-Reglers nach der EMS-ESP-Zeit – nur einschalten, wenn der EMS-ESP selbst die richtige Zeit hat.</div>`;
  if('tz_label_t' in orig && +cur.thermostat_sync===2) h+=`<label class="f" data-k="tz_label_t">Zeitzone Regler${zsel('tz_label_t',cur.tz_label_t)}</label>`;
  $('#f').innerHTML=h;
  $('#f').querySelectorAll('input,select').forEach(el=>{ el.oninput=el.onchange=()=>change(el); });
  mark();
}
function val(el){ if(el.type==='checkbox') return el.checked; if(el.name==='thermostat_sync') return Number(el.value); return el.value; }
function change(el){ cur[el.name]=val(el); if(el.name==='thermostat_sync') render(); else mark(); }
function diff(){ const d={}; for(const k in cur) if(cur[k]!==orig[k] && !k.startsWith('tz_format')) d[k]=cur[k]; return d; }
function mark(){ const d=diff(), n=Object.keys(d).length;
  document.querySelectorAll('[data-k]').forEach(e=>e.classList.toggle('dirty',e.dataset.k in d));
  $('#save').disabled=$('#undo').disabled=!n; $('#save').textContent=n?`Übernehmen (${n})`:'Übernehmen'; }
function msg(t,err){ $('#msg').className=err?'err':''; $('#msg').textContent=t; }
function apply(r){ orig={...r.settings}; cur={...r.settings}; zones=r.zones||{}; render(); showStatus(r.status); }
async function load(){
  try{ apply(await hass.connection.sendMessagePromise({type:'ems_esp_manager/ntp/get'})); msg(''); }
  catch(e){ $('#status').innerHTML='<span class="err">'+esc(e&&e.message||e)+'</span>'; }
}
$('#undo').onclick=()=>{ cur={...orig}; render(); msg('Änderungen verworfen.'); };
$('#save').onclick=async()=>{ const d=diff(); if(!Object.keys(d).length) return;
  $('#save').disabled=true; msg('Speichere …');
  try{ apply(await hass.connection.sendMessagePromise({type:'ems_esp_manager/ntp/set',changes:d})); msg('Übernommen. Die Synchronisation kann bis zu einer Minute dauern.'); setTimeout(refresh,15000); }
  catch(e){ msg('Fehler: '+(e&&e.message||e),true); mark(); } };
$('#sync').onclick=async()=>{
  if(cur.enabled && !confirm('Der Zeitserver ist eingeschaltet und stellt die Uhr bei der nächsten Synchronisation wieder nach seiner Zeit.\n\nTrotzdem jetzt die Home-Assistant-Zeit übernehmen?')) return;
  try{ const r=await hass.connection.sendMessagePromise({type:'ems_esp_manager/ntp/set_time'}); showStatus(r.status); msg('Uhrzeit von Home Assistant übernommen.'); }
  catch(e){ msg('Fehler: '+(e&&e.message||e),true); } };
function refresh(){ if(Object.keys(diff()).length) return; hass.connection.sendMessagePromise({type:'ems_esp_manager/ntp/get'}).then(r=>showStatus(r.status)).catch(()=>{}); }
(function wait(n){ hass=H(); if(hass&&hass.connection){ if(!hass.user||!hass.user.is_admin){ $('#status').innerHTML='<span class="err">Nur für Administratoren.</span>'; return; } load(); setInterval(refresh,30000); return; } if(n>50){ $('#status').innerHTML='<span class="err">Nur innerhalb von Home Assistant nutzbar.</span>'; return; } setTimeout(()=>wait(n+1),100); })(0);
</script></body></html>
"""
