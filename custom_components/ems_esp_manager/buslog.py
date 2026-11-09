"""Download of the EMS bus log (text file) + small dashboard card."""

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

from .const import DOMAIN, ems_entries

_LOGGER = logging.getLogger(__name__)

URL_LOG_ALL = "/api/ems_esp_manager/buslog.txt"
URL_LOG_TELEGRAMS = "/api/ems_esp_manager/buslog_telegrams.txt"
URL_LOG_CARD = "/api/ems_esp_manager/buslog/card"
URL_LOG_LIVE = "/api/ems_esp_manager/buslog/live"
_DATA = f"{DOMAIN}_buslog_http"
_FLAGS = f"{DOMAIN}_log_flags"


# ---------------------------------------------------------------- Log-Schalter (Bus-Log, EHS-Log)
# Gespeichert in .storage/ems_esp_manager.log_flags – Standard: aus; ab v1.22.4 nach jedem HA-Start wieder aus
async def _flags(hass: HomeAssistant):
    from homeassistant.helpers.storage import Store

    d = hass.data.get(_FLAGS)
    if d is None:
        store = Store(hass, 1, f"{DOMAIN}.log_flags")
        old = (await store.async_load()) or {}
        # v1.22.4: Aufzeichnungen starten nach jedem HA-Start ausgeschaltet (nur bei Bedarf einschalten)
        d = {"store": store, "data": {}}
        hass.data[_FLAGS] = d
        if any(old.values()):
            await store.async_save({})
    return d


async def async_log_flag(hass: HomeAssistant, key: str, default: bool = False) -> bool:
    return bool((await _flags(hass))["data"].get(key, default))


async def async_set_log_flag(hass: HomeAssistant, key: str, value: bool) -> None:
    d = await _flags(hass)
    d["data"][key] = bool(value)
    await d["store"].async_save(d["data"])


def _logs(hass: HomeAssistant) -> list[Any]:
    return [
        e.runtime_data.log
        for e in ems_entries(hass)
        if e.state is ConfigEntryState.LOADED and getattr(e.runtime_data, "log", None)
    ]


class _BusLogView(HomeAssistantView):
    """Text download – needs a login (signed link from the dashboard card)."""

    requires_auth = True
    telegrams_only = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            return web.Response(status=403, text="Nur für Administratoren")
        logs = _logs(hass)
        if not logs:
            return web.Response(status=404, text="EMS-ESP Manager nicht geladen")
        from .entity_map import ems_devices

        parts = []
        for log in logs:
            try:
                devices = await ems_devices(log._api)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                devices = None
            parts.append(log.export(self.telegrams_only, devices))
        text = "\n".join(parts)
        stamp = dt_util.now().strftime("%Y%m%d_%H%M")
        name = f"ems-bus-log{'_telegramme' if self.telegrams_only else ''}_{stamp}.txt"
        return web.Response(
            text=text,
            content_type="text/plain",
            charset="utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
            },
        )


class BusLogAllView(_BusLogView):
    url = URL_LOG_ALL
    name = "api:ems_esp_manager:buslog"


class BusLogTelegramView(_BusLogView):
    url = URL_LOG_TELEGRAMS
    name = "api:ems_esp_manager:buslog_telegrams"
    telegrams_only = True


class BusLogCardView(HomeAssistantView):
    """Small page for an iframe card: line count + download buttons."""

    url = URL_LOG_CARD
    name = "api:ems_esp_manager:buslog_card"
    requires_auth = False  # no data in the page itself

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(
            text=CARD_PAGE,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store"},
        )


class BusLogLiveView(HomeAssistantView):
    """v1.22.4: Live-Ansicht des Bus-Logs (iframe) – holt die Zeilen per Websocket statt über ein Sensor-Attribut."""

    url = URL_LOG_LIVE
    name = "api:ems_esp_manager:buslog_live"
    requires_auth = False  # no data in the page itself

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(text=LIVE_PAGE, content_type="text/html", charset="utf-8",
                            headers={"Cache-Control": "no-store"})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/buslog/lines", vol.Optional("since", default=-1): int}
)
@websocket_api.async_response
async def ws_buslog_lines(hass: HomeAssistant, connection, msg) -> None:
    """Letzte Zeilen für die Live-Ansicht (nur geschickt, wenn sich etwas geändert hat)."""
    logs = _logs(hass)
    lines: list[str] = []
    for log in logs:
        lines.extend(log.lines)
    seq = sum(getattr(log, "seq", 0) for log in logs)
    out = {
        "seq": seq,
        "recording": any(log.recording for log in logs),
        "connected": any(log.connected for log in logs),
        "loaded": bool(logs),
    }
    if seq != msg["since"]:
        out["lines"] = lines
    connection.send_result(msg["id"], out)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/buslog/info"})
@websocket_api.async_response
async def ws_buslog_info(hass: HomeAssistant, connection, msg) -> None:
    logs = _logs(hass)
    lines = sum(len(log.history) for log in logs)
    from .log import MAX_DOWNLOAD

    telegrams = sum(log.telegrams for log in logs)
    connection.send_result(
        msg["id"],
        {
            "lines": lines,
            "telegrams": telegrams,
            "max": MAX_DOWNLOAD,
            "since": next((log.since for log in logs if log.since), None),
            "connected": any(log.connected for log in logs),
            "enabled": any(log.recording for log in logs) if logs else await async_log_flag(hass, "bus"),
            "is_admin": connection.user.is_admin,
        },
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/buslog/enable", vol.Required("enabled"): bool})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_buslog_enable(hass: HomeAssistant, connection, msg) -> None:
    """Bus-Log-Aufzeichnung ein/aus (Zeitprogramm-Erkennung und Anlernen laufen immer weiter)."""
    await async_set_log_flag(hass, "bus", msg["enabled"])
    for log in _logs(hass):
        log.set_recording(msg["enabled"])
    connection.send_result(msg["id"], {"enabled": msg["enabled"]})


def async_setup_buslog(hass: HomeAssistant) -> None:
    """Register views and websocket command once."""
    if hass.data.get(_DATA):
        return
    from .wpa import WpaAppView

    for view in (BusLogAllView(), BusLogTelegramView(), BusLogCardView(), BusLogLiveView(), WpaAppView()):
        hass.http.register_view(view)
    websocket_api.async_register_command(hass, ws_buslog_info)
    websocket_api.async_register_command(hass, ws_buslog_lines)
    websocket_api.async_register_command(hass, ws_buslog_enable)
    hass.data[_DATA] = True


CARD_PAGE = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bus-Log herunterladen</title>
<style>
:root{color-scheme:light dark;--fg:#212121;--dim:#727272;--line:#e0e0e0;--acc:#03a9f4;--err:#db4437}
@media (prefers-color-scheme:dark){:root{--fg:#e1e1e1;--dim:#9b9b9b;--line:#3a3a3a}}
html,body{margin:0;background:transparent;color:var(--fg);font:14px/1.45 Roboto,"Segoe UI",Arial,sans-serif}
body{padding:2px 14px 8px}
.row{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid var(--line)}
.row span:first-child{color:var(--dim)}
.btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
button{font:inherit;font-size:13px;font-weight:500;border:0;border-radius:16px;padding:6px 12px;cursor:pointer;background:var(--acc);color:#fff}
button.sec{background:transparent;color:var(--acc);border:1px solid var(--acc)}
button:disabled{opacity:.5;cursor:default}
#msg{margin-top:8px;color:var(--dim);font-size:13px}
.rec{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:6px 0 8px;border-bottom:1px solid var(--line)}
.rec small{display:block;color:var(--dim);font-size:12px}
.tog{width:44px;height:24px;border-radius:12px;background:#9e9e9e;position:relative;border:0;padding:0;cursor:pointer;flex:none}
.tog::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:left .2s}
.tog.on{background:var(--acc)}.tog.on::after{left:23px}
.err{color:var(--err)!important}
</style></head><body>
<div class="rec"><span><b>Aufzeichnung</b><small id="rect">–</small></span><button class="tog" id="rec" aria-label="Aufzeichnung ein/aus" disabled></button></div>
<div id="info">Lade …</div>
<div class="btns">
  <button id="tg">Telegramme herunterladen</button>
  <button id="all" class="sec">Komplettes Log</button>
  <button id="imp" class="sec">Zeitprogramm aus Log-Datei übernehmen</button>
</div>
<input id="file" type="file" accept=".txt,.log,text/plain" hidden>
<div id="msg">Datei (.txt) mit HA-Zeit, EMS-ESP-Zeit und Meldung – zum Auswerten fehlender Telegramme.</div>
<script>
const $=s=>document.querySelector(s);
let hass=null;
function H(){ for(const w of [window.parent, window.parent&&window.parent.parent, window.top]){ try{ const ha=w&&w!==window&&w.document.querySelector('home-assistant'); if(ha&&ha.hass) return ha.hass; }catch(e){} } return null; }
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){
  try{
    const i=await hass.connection.sendMessagePromise({type:'ems_esp_manager/buslog/info'});
    $('#info').innerHTML=`<div class="row"><span>Zeilen im Speicher</span><b>${i.lines.toLocaleString('de-DE')} / ${i.max.toLocaleString('de-DE')}</b></div>
      <div class="row"><span>davon Telegramme (Rx/Tx)</span><span>${i.telegrams.toLocaleString('de-DE')}</span></div>
      <div class="row"><span>Zeitprogramm HK1 im Speicher</span><span id="sch">–</span></div>
      <div class="row"><span>Zeitprogramm Warmwasser im Speicher</span><span id="schw">–</span></div>
      <div class="row"><span>Aufgezeichnet seit</span><span>${esc(i.since||'–')}${i.connected?'':' · <span class="err">Stream getrennt</span>'}</span></div>`;
    const on=i.enabled!==false; $('#rec').classList.toggle('on',on); $('#rec').disabled=!i.is_admin;
    $('#rect').textContent=on?'läuft – Bus-Meldungen werden gespeichert':'aus – nur bei Bedarf einschalten';
    $('#tg').disabled=$('#all').disabled=!i.is_admin||!i.lines;
    $('#imp').disabled=!i.is_admin;
    try{ const s=await hass.connection.sendMessagePromise({type:'ems_esp_manager/schedule/info'}); showSch(s); }catch(e){}
    if(!i.is_admin){ $('#msg').textContent='Download nur für Administratoren.'; }
  }catch(e){ $('#info').innerHTML='<span class="err">'+esc(e&&e.message||e)+'</span>'; }
}
async function dl(path){
  try{
    const r=await hass.connection.sendMessagePromise({type:'auth/sign_path',path,expires:60});
    // Download im Hauptfenster auslösen (das iframe darf selbst keine Downloads starten)
    const d=window.parent.document, a=d.createElement('a');
    a.href=r.path; a.download=''; a.style.display='none'; d.body.appendChild(a); a.click(); a.remove();
    $('#msg').className=''; $('#msg').textContent='Download gestartet.';
  }catch(e){ $('#msg').className='err'; $('#msg').textContent='Download fehlgeschlagen: '+(e&&e.message||e); }
}
function schTxt(s){
  if(!s) return '–';
  const n=(s.days||[]).filter(d=>d).length;
  return n? (s.complete?'vollständig':n+' von 7 Tagen')+(s.updated?' · '+new Date(s.updated).toLocaleString('de-DE',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'') : 'leer';
}
function showSch(s){
  const p=s.programs||{};
  if($('#sch')) $('#sch').textContent=schTxt(p.hc1||(s.prog==='hc1'||!s.prog?s:null));
  if($('#schw')&&p.dhw) $('#schw').textContent=schTxt(p.dhw);
}
$('#rec').onclick=async()=>{ const on=!$('#rec').classList.contains('on'); $('#rec').classList.toggle('on',on);
  try{ await hass.connection.sendMessagePromise({type:'ems_esp_manager/buslog/enable',enabled:on}); }catch(e){ $('#msg').className='err'; $('#msg').textContent='Nicht umgeschaltet: '+(e&&e.message||e); }
  load(); };
$('#imp').onclick=()=>$('#file').click();
$('#file').onchange=async ev=>{
  const f=ev.target.files[0]; ev.target.value=''; if(!f) return;
  try{
    const r=await hass.connection.sendMessagePromise({type:'ems_esp_manager/schedule/import',content:await f.text()});
    const names={hc1:'Heizkreis 1',dhw:'Warmwasser'};
    $('#msg').className=r.imported?'':'err';
    $('#msg').textContent=r.imported?'Zeitprogramm übernommen: '+(r.found||[]).map(k=>names[k]+' ('+r.programs[k].days.filter(d=>d).length+' von 7 Tagen)').join(', ')+'.':'In dieser Datei ist kein Zeitprogramm (Telegramm 0x02C3 / 0x02FF) enthalten.';
    load();
  }catch(e){ $('#msg').className='err'; $('#msg').textContent='Import fehlgeschlagen: '+(e&&e.message||e); }
};
$('#tg').onclick=()=>dl('""" + URL_LOG_TELEGRAMS + """');
$('#all').onclick=()=>dl('""" + URL_LOG_ALL + """');
(function wait(n){ hass=H(); if(hass&&hass.connection){ load(); setInterval(load,10000); return; } if(n>50){ $('#info').innerHTML='<span class="err">Nur innerhalb von Home Assistant nutzbar.</span>'; return; } setTimeout(()=>wait(n+1),100); })(0);
</script></body></html>
"""


LIVE_PAGE = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bus-Log live</title>
<style>
:root{color-scheme:light dark;--fg:#212121;--dim:#727272;--line:#e0e0e0;--acc:#03a9f4;--err:#db4437}
@media (prefers-color-scheme:dark){:root{--fg:#e1e1e1;--dim:#9b9b9b;--line:#3a3a3a}}
html,body{margin:0;background:transparent;color:var(--fg);font:14px/1.45 Roboto,"Segoe UI",Arial,sans-serif}
body{padding:4px 12px 8px}
#head{display:flex;justify-content:space-between;align-items:center;gap:8px;color:var(--dim);font-size:12px;padding:2px 0 6px;border-bottom:1px solid var(--line)}
#head button{font:inherit;font-size:12px;border:1px solid var(--acc);color:var(--acc);background:transparent;border-radius:12px;padding:3px 10px;cursor:pointer}
#log{font:12px/1.5 ui-monospace,Consolas,"Roboto Mono",monospace;white-space:pre-wrap;word-break:break-all;padding-top:6px}
#log div{padding:1px 0;border-bottom:1px dotted var(--line)}
.err{color:var(--err)}
</style></head><body>
<div id="head"><span id="st">Lade …</span><button id="pause">Anhalten</button></div>
<div id="log"></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function H(){ for(const w of [window.parent, window.parent&&window.parent.parent, window.top]){ try{ const ha=w&&w!==window&&w.document.querySelector('home-assistant'); if(ha&&ha.hass) return ha.hass; }catch(e){} } return null; }
let hass=null, seq=-1, pause=false, busy=false;
async function load(){
  if(busy||pause||document.hidden) return; busy=true;
  try{
    const r=await hass.connection.sendMessagePromise({type:'ems_esp_manager/buslog/lines',since:seq});
    $('#st').innerHTML=!r.loaded?'EMS-ESP nicht geladen':!r.recording?'Aufzeichnung aus – unter „Download“ einschalten':
      ('Aufzeichnung läuft'+(r.connected?'':' · <span class="err">Stream getrennt</span>'));
    if(r.lines){ seq=r.seq; const l=r.lines.slice().reverse();
      $('#log').innerHTML=l.length?l.map(x=>'<div>'+esc(x)+'</div>').join(''):'<div style="color:var(--dim);border:0">Noch keine Zeilen.</div>'; }
  }catch(e){ $('#st').innerHTML='<span class="err">'+esc(e&&e.message||e)+'</span>'; }
  finally{ busy=false; }
}
$('#pause').onclick=()=>{ pause=!pause; $('#pause').textContent=pause?'Weiter':'Anhalten'; if(!pause) load(); };
(function wait(n){ hass=H(); if(hass&&hass.connection){ load(); setInterval(load,2000); return; }
  if(n>50){ $('#st').innerHTML='<span class="err">Nur innerhalb von Home Assistant nutzbar.</span>'; return; } setTimeout(()=>wait(n+1),100); })(0);
</script></body></html>
"""
