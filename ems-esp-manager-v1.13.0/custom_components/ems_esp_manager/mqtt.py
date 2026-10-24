"""MQTT settings of EMS-ESP (/rest/mqttSettings) – page + websocket commands."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .api import EmsEspError
from .const import DOMAIN, ems_entries

_LOGGER = logging.getLogger(__name__)

URL_MQTT_CARD = "/api/ems_esp_manager/mqtt/card"
_DATA = f"{DOMAIN}_mqtt_http"

# field -> type, as EMS-ESP stores it (src/ESP32React/MqttSettingsService.cpp)
FIELDS: dict[str, type] = {
    "enabled": bool, "host": str, "port": int, "base": str, "username": str,
    "password": str, "client_id": str, "keep_alive": int, "clean_session": bool,
    "mqtt_qos": int, "mqtt_retain": bool, "enableTLS": bool, "rootCA": str,
    "nested_format": int, "send_response": bool, "publish_single": bool,
    "publish_single2cmd": bool, "ha_enabled": bool, "discovery_type": int,
    "discovery_prefix": str, "entity_format": int, "ha_number_mode": int,
    "publish_time_boiler": int, "publish_time_thermostat": int,
    "publish_time_solar": int, "publish_time_mixer": int, "publish_time_water": int,
    "publish_time_sensor": int, "publish_time_other": int, "publish_time_heartbeat": int,
}


def _api(hass: HomeAssistant):
    for e in ems_entries(hass):
        if e.state is ConfigEntryState.LOADED:
            return e.runtime_data
    return None


def _clean(changes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in changes.items():
        typ = FIELDS.get(key)
        if typ is None:
            raise ValueError(f"Unbekanntes Feld: {key}")
        if typ is bool:
            out[key] = bool(value)
        elif typ is int:
            out[key] = int(value)
            if out[key] < 0 or out[key] > 65535:
                raise ValueError(f"{key}: Wert außerhalb 0…65535")
        else:
            out[key] = str(value)
    return out


async def _status(api) -> dict[str, Any] | None:
    try:
        status, data = await api._request("GET", "/rest/mqttStatus")  # noqa: SLF001
        return data if status == 200 and isinstance(data, dict) else None
    except EmsEspError:
        return None


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/mqtt/get"})
@websocket_api.async_response
async def ws_mqtt_get(hass: HomeAssistant, connection, msg) -> None:
    rt = _api(hass)
    if rt is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    try:
        settings = await rt.api.get_mqtt_settings()
    except EmsEspError as err:
        connection.send_error(msg["id"], "ems_esp", str(err))
        return
    connection.send_result(msg["id"], {"settings": settings, "status": await _status(rt.api)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/mqtt/set", vol.Required("changes"): dict}
)
@websocket_api.async_response
async def ws_mqtt_set(hass: HomeAssistant, connection, msg) -> None:
    rt = _api(hass)
    if rt is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    try:
        changes = _clean(msg["changes"])
        settings = await rt.api.set_mqtt_settings(changes)
    except (ValueError, EmsEspError) as err:
        connection.send_error(msg["id"], "ems_esp", str(err))
        return
    shown = {k: ("***" if k == "password" else v) for k, v in changes.items()}
    _LOGGER.info("EMS-ESP MQTT-Einstellungen geändert: %s", shown)
    if rt.log is not None:
        rt.log.note("MQTT-Einstellungen geändert: " + ", ".join(f"{k}={v}" for k, v in shown.items()))
    connection.send_result(msg["id"], {"settings": settings, "status": await _status(rt.api)})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/mqtt/reconnect"})
@websocket_api.async_response
async def ws_mqtt_reconnect(hass: HomeAssistant, connection, msg) -> None:
    rt = _api(hass)
    if rt is None:
        connection.send_error(msg["id"], "not_loaded", "EMS-ESP Manager nicht geladen")
        return
    try:
        await rt.api.action("resetMQTT")
    except EmsEspError as err:
        connection.send_error(msg["id"], "ems_esp", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


class MqttCardView(HomeAssistantView):
    """Form page for an iframe card – data only via the HA websocket (admin)."""

    url = URL_MQTT_CARD
    name = "api:ems_esp_manager:mqtt_card"
    requires_auth = False  # no data in the page itself

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(text=PAGE, content_type="text/html", charset="utf-8",
                            headers={"Cache-Control": "no-store"})


def async_setup_mqtt(hass: HomeAssistant) -> None:
    if hass.data.get(_DATA):
        return
    hass.http.register_view(MqttCardView())
    for cmd in (ws_mqtt_get, ws_mqtt_set, ws_mqtt_reconnect):
        websocket_api.async_register_command(hass, cmd)
    hass.data[_DATA] = True


PAGE = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EMS-ESP MQTT</title>
<style>
:root{color-scheme:light dark;--fg:#212121;--dim:#727272;--line:#e0e0e0;--acc:#03a9f4;--err:#db4437;--ok:#0f9d58;--card:#fff;--in:#f5f5f5}
@media (prefers-color-scheme:dark){:root{--fg:#e1e1e1;--dim:#9b9b9b;--line:#3a3a3a;--card:#1c1c1c;--in:#2a2a2a}}
html,body{margin:0;background:transparent;color:var(--fg);font:14px/1.45 Roboto,"Segoe UI",Arial,sans-serif}
body{padding:8px 16px 80px;max-width:980px;margin:0 auto}
h2{font-size:16px;font-weight:500;margin:22px 0 6px;padding-bottom:4px;border-bottom:1px solid var(--line)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:4px 18px}
label.f{display:flex;flex-direction:column;gap:3px;padding:6px 0;font-size:13px;color:var(--dim)}
label.c{display:flex;align-items:center;gap:8px;padding:8px 0;cursor:pointer}
input[type=text],input[type=number],input[type=password],select,textarea{font:inherit;color:var(--fg);background:var(--in);border:1px solid var(--line);border-radius:6px;padding:7px 9px}
textarea{min-height:90px;font-family:monospace;font-size:12px}
input[type=checkbox]{width:18px;height:18px;accent-color:var(--acc)}
.dirty{outline:2px solid var(--acc)}
.st{display:flex;flex-wrap:wrap;gap:6px 18px;padding:10px 12px;border:1px solid var(--line);border-radius:8px}
.st b.ok{color:var(--ok)} .st b.bad{color:var(--err)}
.warn{font-size:12px;color:var(--err);margin:2px 0 6px}
.hint{font-size:12px;color:var(--dim)}
.bar{position:fixed;left:0;right:0;bottom:0;display:flex;gap:8px;justify-content:flex-end;align-items:center;padding:10px 16px;background:var(--card);border-top:1px solid var(--line)}
.bar #msg{flex:1;font-size:13px;color:var(--dim)}
button{font:inherit;font-size:13px;font-weight:500;border:0;border-radius:16px;padding:7px 14px;cursor:pointer;background:var(--acc);color:#fff}
button.sec{background:transparent;color:var(--acc);border:1px solid var(--acc)}
button:disabled{opacity:.45;cursor:default}
.err{color:var(--err)!important}
.hide{display:none}
</style></head><body>
<div id="status" class="st">Lade …</div>
<form id="f" autocomplete="off"></form>
<div class="bar"><span id="msg"></span>
  <button id="recon" class="sec" type="button">MQTT neu verbinden</button>
  <button id="undo" class="sec" type="button" disabled>Verwerfen</button>
  <button id="save" type="button" disabled>Übernehmen</button></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let hass=null, orig={}, cur={};
const SEC=[
 ['Verbindung',[
  ['enabled','c','MQTT aktivieren'],
  ['host','t','Broker-Adresse'],['port','n','Port'],
  ['base','t','Basis-Topic',{warn:'Ändert alle Topics – HA-Entitäten werden neu angelegt.'}],
  ['client_id','t','Client-ID (optional)'],
  ['username','t','Benutzername'],['password','p','Passwort'],
  ['keep_alive','n','Keep Alive (Sekunden)'],
  ['mqtt_qos','s','QoS',{opts:[[0,'0'],[1,'1'],[2,'2']]}],
  ['clean_session','c','Clean Session setzen'],
  ['mqtt_retain','c','Retain-Flag immer setzen'],
  ['enableTLS','c','TLS aktivieren'],
  ['rootCA','a','Root-Zertifikat (TLS)',{show:d=>d.enableTLS}],
 ]],
 ['Format',[
  ['nested_format','s','MQTT-Format',{opts:[[1,'Eingebettet in einem Gesamt-Topic'],[2,'Als einzelne Topics']]}],
  ['send_response','c','Kommando-Antwort als „response“-Topic senden'],
  ['publish_single','c','Einzelne Werte bei Änderung als eigene Topics senden'],
  ['publish_single2cmd','c','Als Kommando-Topic senden (ioBroker)',{show:d=>d.publish_single}],
 ]],
 ['Home Assistant (Discovery)',[
  ['ha_enabled','c','MQTT Discovery aktivieren',{warn:'Aus = EMS-ESP-Entitäten verschwinden aus Home Assistant.'}],
  ['discovery_type','s','Discovery-Typ',{opts:[[0,'Home Assistant'],[1,'Domoticz'],[2,'Domoticz (neu)']],show:d=>d.ha_enabled}],
  ['discovery_prefix','t','Discovery-Präfix',{show:d=>d.ha_enabled,warn:'Muss zum Präfix der HA-MQTT-Integration passen (Standard „homeassistant“).'}],
  ['entity_format','s','Entitäts-ID-Format',{opts:[[0,'Einzelinstanz, Langname (v3.4)'],[3,'Einzelinstanz, MQTT-Namen (v3.5)'],[4,'Mehrfachinstanzen, MQTT-Namen (v3.5)'],[1,'Einzelinstanz, MQTT-Namen (aktuell)'],[2,'Mehrfachinstanzen, MQTT-Namen (aktuell)']],show:d=>d.ha_enabled,warn:'Benennt alle Entitäten um – Dashboards, Automationen und die Zuordnung im Bosch-Panel prüfen!'}],
  ['ha_number_mode','s','Zahlenformat',{opts:[[0,'Eingabefeld (Box)'],[1,'Schieberegler']],show:d=>d.ha_enabled}],
 ]],
 ['Sendeintervalle in Sekunden (0 = automatisch bei Änderung)',[
  ['publish_time_boiler','n','Kessel / Wärmepumpe'],['publish_time_thermostat','n','Thermostate'],
  ['publish_time_solar','n','Solarmodule'],['publish_time_mixer','n','Mischermodule'],
  ['publish_time_water','n','Warmwassermodule'],['publish_time_sensor','n','Sensoren'],
  ['publish_time_other','n','Sonstige'],['publish_time_heartbeat','n','Heartbeat'],
 ]],
];
const RISKY=['base','ha_enabled','discovery_prefix','entity_format','discovery_type','enabled'];
function H(){ try{ const ha=window.parent.document.querySelector('home-assistant'); return ha&&ha.hass; }catch(e){ return null; } }
function field(k,t,l,o={}){
  const v=cur[k]; let inp;
  if(t==='c') return `<label class="c" data-k="${k}"><input type="checkbox" name="${k}" ${v?'checked':''}> ${esc(l)}</label>${o.warn?`<div class="warn hide" data-w="${k}">${esc(o.warn)}</div>`:''}`;
  if(t==='s') inp=`<select name="${k}">${o.opts.map(([x,y])=>`<option value="${x}" ${+v===x?'selected':''}>${esc(y)}</option>`).join('')}</select>`;
  else if(t==='a') inp=`<textarea name="${k}">${esc(v)}</textarea>`;
  else inp=`<input type="${t==='n'?'number':t==='p'?'password':'text'}" name="${k}" value="${esc(v)}" ${t==='n'?'min="0" max="65535"':''}>`;
  return `<label class="f" data-k="${k}">${esc(l)}${inp}</label>${o.warn?`<div class="warn hide" data-w="${k}">${esc(o.warn)}</div>`:''}`;
}
function render(){
  $('#f').innerHTML=SEC.map(([h,fs])=>`<h2>${esc(h)}</h2><div class="grid">${fs.filter(f=>f[0] in orig).map(f=>`<div class="${f[3]&&f[3].show&&!f[3].show(cur)?'hide':''}" data-box="${f[0]}">${field(...f)}</div>`).join('')}</div>`).join('');
  $('#f').querySelectorAll('input,select,textarea').forEach(el=>{ el.oninput=el.onchange=()=>change(el); });
  mark();
}
function val(el){ const k=el.name; if(el.type==='checkbox') return el.checked; if(typeof orig[k]==='number') return el.value===''?0:Number(el.value); return el.value; }
function change(el){ cur[el.name]=val(el); SEC.forEach(([,fs])=>fs.forEach(f=>{ const b=document.querySelector(`[data-box="${f[0]}"]`); if(b&&f[3]&&f[3].show) b.classList.toggle('hide',!f[3].show(cur)); })); mark(); }
function diff(){ const d={}; for(const k in cur) if(cur[k]!==orig[k]) d[k]=cur[k]; return d; }
function mark(){
  const d=diff(), n=Object.keys(d).length;
  document.querySelectorAll('[data-k]').forEach(e=>e.classList.toggle('dirty',e.dataset.k in d));
  document.querySelectorAll('[data-w]').forEach(e=>e.classList.toggle('hide',!(e.dataset.w in d)));
  $('#save').disabled=$('#undo').disabled=!n;
  $('#save').textContent=n?`Übernehmen (${n})`:'Übernehmen';
}
function showStatus(s){
  if(!s){ $('#status').innerHTML='<span class="hint">MQTT-Status nicht verfügbar.</span>'; return; }
  const on=s.connected===true||s.connected===1, bits=[];
  bits.push(`<span>Status: <b class="${on?'ok':'bad'}">${on?'verbunden':(s.enabled===false?'deaktiviert':'getrennt')}</b></span>`);
  if(s.client_id) bits.push(`<span>Client: ${esc(s.client_id)}</span>`);
  if(s.mqtt_queued!=null) bits.push(`<span>Warteschlange: ${esc(s.mqtt_queued)}</span>`);
  if(s.mqtt_fails!=null) bits.push(`<span>Fehler: ${esc(s.mqtt_fails)}</span>`);
  if(s.connect_count!=null) bits.push(`<span>Verbindungen: ${esc(s.connect_count)}</span>`);
  if(!on&&s.disconnect_reason!=null) bits.push(`<span class="err">Grund: ${esc(s.disconnect_reason)}</span>`);
  $('#status').innerHTML=bits.join('');
}
function msg(t,err){ $('#msg').className=err?'err':''; $('#msg').textContent=t; }
async function load(){
  try{ const r=await hass.connection.sendMessagePromise({type:'ems_esp_manager/mqtt/get'});
    orig={...r.settings}; cur={...r.settings}; render(); showStatus(r.status); msg('');
  }catch(e){ $('#status').innerHTML='<span class="err">'+esc(e&&e.message||e)+'</span>'; }
}
$('#undo').onclick=()=>{ cur={...orig}; render(); msg('Änderungen verworfen.'); };
$('#save').onclick=async()=>{
  const d=diff(); if(!Object.keys(d).length) return;
  const risky=Object.keys(d).filter(k=>RISKY.includes(k));
  if(risky.length&&!confirm('Achtung: '+risky.join(', ')+' wird geändert.\nDas kann Entitäten in Home Assistant umbenennen oder entfernen und die MQTT-Verbindung trennen.\n\nTrotzdem übernehmen?')) return;
  $('#save').disabled=true; msg('Speichere …');
  try{ const r=await hass.connection.sendMessagePromise({type:'ems_esp_manager/mqtt/set',changes:d});
    orig={...r.settings}; cur={...r.settings}; render(); showStatus(r.status);
    msg('Übernommen ('+Object.keys(d).length+' Änderungen). EMS-ESP verbindet MQTT neu.');
    setTimeout(load,4000);
  }catch(e){ msg('Fehler: '+(e&&e.message||e),true); mark(); }
};
$('#recon').onclick=async()=>{ try{ await hass.connection.sendMessagePromise({type:'ems_esp_manager/mqtt/reconnect'}); msg('MQTT wird neu verbunden …'); setTimeout(load,4000);}catch(e){ msg('Fehler: '+(e&&e.message||e),true); } };
(function wait(n){ hass=H(); if(hass&&hass.connection){ if(!hass.user||!hass.user.is_admin){ $('#status').innerHTML='<span class="err">Nur für Administratoren.</span>'; return; } load(); setInterval(()=>{ if(!Object.keys(diff()).length) hass.connection.sendMessagePromise({type:'ems_esp_manager/mqtt/get'}).then(r=>showStatus(r.status)).catch(()=>{}); },15000); return; } if(n>50){ $('#status').innerHTML='<span class="err">Nur innerhalb von Home Assistant nutzbar.</span>'; return; } setTimeout(()=>wait(n+1),100); })(0);
</script></body></html>
"""
