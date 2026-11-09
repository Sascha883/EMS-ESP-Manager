"""WP-/Kessel-Auswertung (System → WP-Auswertung, ab v1.20.3) – Samsung, Bosch und Brötje, mit Einstellwerten.

Die Auswertung läuft komplett im Browser: Sie liest den Verlauf aus dem HA-Recorder
(history/history_during_period), bewertet Laufzeit, Taktung, Abtauen, Kältekreis, Hydraulik
und Effizienz (Brötje: Brenner, Modulation, Trinkwasserladung) und erzeugt daraus eine PDF-Datei
(eigener kleiner PDF-Schreiber, keine fremden Bibliotheken) sowie die Rohdaten als CSV.

- Samsung (EMS-EHS) und Brötje (EMS-BSB): als Umschalter unter „System“ in EHS_APP_PAGE (ehs.py),
  eingesetzt über die Platzhalter /*__WPA_CSS__*/ und /*__WPA_JS__*/.
- Bosch (Dashboard „EMS-ESP“): Unteransicht „WP-Auswertung“ (iframe auf URL_WPA_APP), geöffnet über
  den Knopf in „System & Firmware“.
"""

from __future__ import annotations

from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN

URL_WPA_APP = f"/api/{DOMAIN}/wpa"

WPA_CSS = r""".waleg{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:var(--dim);margin-top:4px}.waleg i{display:inline-block;width:11px;height:9px;margin-right:5px;border-radius:2px;vertical-align:middle}
.watab{width:100%;border-collapse:collapse;font-size:14px;margin:4px 0 6px}.watab th{color:var(--lbl);font-size:11px;letter-spacing:.06em;text-transform:uppercase;font-weight:500;text-align:right;padding:8px 6px}
.watab td{text-align:right;padding:9px 6px;border-top:1px solid var(--sep);color:var(--k)}.watab th:first-child,.watab td:first-child{text-align:left}
"""

WPA_JS = r"""/* =================== Logs → WP-/Kessel-Auswertung als PDF (Samsung · Bosch · Brötje) ===================
   Liest den HA-Verlauf (24 h / 7 Tage / 30 Tage), bewertet Taktung, Laufzeit, Abtauen, Kältekreis,
   Hydraulik und Effizienz (Brötje: Brenner, Modulation, Trinkwasserladung) und erzeugt daraus eine
   PDF-Datei (ohne fremde Bibliotheken) sowie die Rohdaten als CSV. */
// Hersteller folgt dem Reiter: „Brötje EHS“/ESP-Bus-Adapter = Kessel (BSB), sonst Samsung bzw. Bosch
const WA_DEF = (typeof BSBMODE!=='undefined' && BSBMODE) ? 'bsb' : (window.WA_VENDOR||'samsung');
let WA_V='', WA_BOIL=false, WT={}, WPR_SIG=[], WPR_ANL='';
const WT_OF = v=>v==='bsb' ? {comp:'Brenner',dev:'Heizkessel',title:'Kessel-Auswertung',runs:'Brennerläufe'} : {comp:'Verdichter',dev:'Wärmepumpe',title:'WP-Auswertung',runs:'Verdichterläufe'};
function wt(s){ if(!WA_BOIL) return s; return String(s).replace(/Verdichterläufe/g,'Brennerläufe').replace(/Verdichterstarts/g,'Brennerstarts').replace(/Verdichter Heizen/g,'Brenner Heizen')
  .replace(/Verdichter/g,'Brenner').replace(/Auswertung Wärmepumpe/g,'Auswertung Heizkessel').replace(/WP-Auswertung/g,'Kessel-Auswertung'); }
const WPR_PER = [{k:'1d',l:'24 h',d:1,step:30},{k:'7d',l:'7 Tage',d:7,step:60},{k:'30d',l:'30 Tage',d:30,step:180}];
// [Schlüssel, Bezeichnung, Einheit/Art, Entität bzw. Namensende]
const WA_SIGS = ({
  samsung:[
    ['comp','Verdichter','bin','outdoorcomp1status'],['hz','Verdichterfrequenz','Hz','outdoorcomp1runhz'],
    ['act','Status Außengerät','txt','outdooroperationstatus'],['dhw','3-Wege-Ventil','txt','dhwvalve'],
    ['def','Abtauen (4-Wege-Ventil)','bin','outdoor4wayvalve'],['ta','Außentemperatur','°C','outdoorouttemp'],
    ['vl','Vorlauf','°C','indoorwaterouttemp'],['rl','Rücklauf','°C','indoorwaterintemp'],
    ['vls','Vorlauf-Soll','°C','ehssentinelcurrenttargetflowtemp'],['ww','Warmwasser','°C','indoordhwcurrenttemp'],
    ['flow','Durchfluss','l/min','inflowsensorcalc'],['pump','Heizungspumpe','%','inwaterpumppwmvalue'],
    ['pel','Elektr. Leistung','kW','outdoorcontrolwattmeterallunit'],['pth','Heizleistung','kW','ehssentinelheatoutput'],
    ['tdis','Heißgas','°C','outdoordischargetemp1'],['tsuc','Sauggas','°C','outdoorsuction1temp'],
    ['sh','Sauggas-Überhitzung','K','outdoorsuctionoverheat'],['php','Hochdruck','bar','outdoorhighpress'],
    ['plp','Niederdruck','bar','outdoorlowpress'],['tph','Kondensationstemp. (HD)','°C','outdoorhighpresstemp'],
    ['tpl','Verdampfungstemp. (ND)','°C','outdoorlowpresstemp'],['tevi','Verdampfereintritt','°C','outdoorevain'],
    ['eev','Expansionsventil','Schritte','outdoormaineev1'],['fan','Lüfter','U/min','outdoorfanrpm1'],
    ['bu','Backup-Heizer','txt','inbackupheater'],['err','Fehlercode','txt','outerrorcode']],
  bosch:[
    ['comp','Verdichter','bin','binary_sensor.boiler_hpcompon'],['hz','Verdichterdrehzahl','%','sensor.boiler_hpcompspd'],
    ['act','Betriebsart','txt','sensor.boiler_hpactivity'],['dhw','3-Wege-Ventil WW','txt','binary_sensor.boiler_hpswitchvalve'],
    ['def','Abtauen (4-Wege-Ventil)','bin','binary_sensor.boiler_hp4way'],['ta','Außentemperatur','°C','sensor.boiler_outdoortemp'],
    ['vl','Vorlauf TC1','°C','sensor.boiler_hptc1'],['rl','Rücklauf TC0','°C','sensor.boiler_hptc0'],
    ['vls','Vorlauf-Soll','°C','sensor.thermostat_hc1_targetflowtemp'],['ww','Warmwasser','°C','sensor.boiler_dhw_curtemp'],
    ['flow','Durchfluss PC0','l/min','sensor.boiler_pc0flow'],['pump','Heizungspumpe PC0','%','sensor.boiler_hpcircspd'],
    ['pel','Elektr. Leistung','kW','sensor.boiler_hpcurrpower'],['pth','Heizleistung','kW','sensor.boiler_hppower'],
    ['tdis','Heißgas TR6','°C','sensor.boiler_hptr6'],['tsuc','Sauggas TR5','°C','sensor.boiler_hptr5'],
    ['tph','Kondensationstemp. PH1','°C','sensor.boiler_hpph1'],['tpl','Verdampfungstemp. PL1','°C','sensor.boiler_hppl1'],
    ['tliq','Kältemittel flüssig TR3','°C','sensor.boiler_hptr3'],['tevi','Verdampfereintritt TR4','°C','sensor.boiler_hptr4'],
    ['eev','Expansionsventil VR1','%','sensor.boiler_expvalve'],['fan','Ventilator','%','sensor.boiler_fanspd'],
    ['bu','Zuheizer-Stufe','txt','sensor.boiler_auxheaterlevel'],['err','Störungscode','txt','sensor.thermostat_errorcode']],
  bsb:[
    ['hz','Brennermodulation','%','sensor:brennermodulation'],['act','Status Kessel','txt','sensor:status_kessel'],
    ['dhw','Trinkwasserpumpe Q3','txt','binary_sensor:trinkwasserpumpe_q3'],['ta','Außentemperatur','°C','sensor:aussentemperatur'],
    ['vl','Vorlauf HK1','°C','sensor:vorlauftemperatur_hk1'],['vls','Vorlauf-Soll HK1','°C','sensor:vorlaufsollwert_hk1'],
    ['vl2','Vorlauf HK2','°C','sensor:vorlauftemperatur_hk2'],['vls2','Vorlauf-Soll HK2','°C','sensor:vorlaufsollwert_hk2'],
    ['ww','Trinkwasser','°C','sensor:trinkwassertemperatur'],['wwu','Trinkwasser unten','°C','sensor:trinkwassertemperatur_unten'],
    ['room','Raumtemperatur','°C','sensor:raumtemperatur_an_regler'],['q2','Heizkreispumpe Q2','bin','binary_sensor:heizkreispumpe_hk1_q2'],
    ['kol','Kollektor','°C','sensor:kollektortemperatur'],['kp','Kollektorpumpe','bin','binary_sensor:kollektorpumpe']],
});
const WA_ANLS = {samsung:'Samsung EHS (EHS Sentinel)',bosch:'Bosch / Buderus (EMS-ESP)',bsb:'Brötje / Siemens-BSB (ESP-Bus-Adapter)'};
// Signal-IDs im Panel-Format: Samsung „…ehssentinel_<name>“, Brötje „bsbpick_<name>“, Bosch EMS-ESP-Standard-ID
function waSigId(v,x){ if(v==='samsung') return (x[2]==='bin'?'binary_sensor':'sensor')+'.samsung_ehssentinel_'+x[3]; if(v==='bsb'){ const [d,o]=x[3].split(':'); return d+'.bsbpick_'+o; } return x[3]; }
function waVendor(v){ if(v===WA_V) return; WA_V=v; WA_BOIL=v==='bsb'; WT=WT_OF(v); WPR_ANL=WA_ANLS[v];
  WPR_SIG=WA_SIGS[v].map(x=>({k:x[0],l:x[1],u:x[2],id:waSigId(v,x)}));
  if(typeof waVendorSet==='function') waVendorSet(v); }
const waTabVendor=()=>(typeof TAB!=='undefined' && TAB==='bsb') ? 'bsb' : (typeof TAB!=='undefined' && TAB==='bosch') ? 'bosch' : WA_DEF;
const waN=(v,dec)=>{ if(v==null||isNaN(v)) return '–'; const d=dec!=null?dec:(Math.abs(v%1)>0.001?1:0); return Number(v).toLocaleString('de-DE',{minimumFractionDigits:d,maximumFractionDigits:d}); };
function waR(id){ return waResolve(id); }
function waHas(id){ const r=waResolve(id), s=r&&waS()[r]; return !!s && s.state!=='unavailable' && !(r.includes('ehssentinel') && s.state==='unknown' && !/^(number|select|switch)\./.test(r)); }
const WPA={per:0, cv:{}};   // v1.22.10: Vorauswahl 24 h (Index in WPR_PER)
const waC=()=>WPA.cv[WA_V]||(WPA.cv[WA_V]={});
let WA_DEMO=false;
function waLoad(per,force){
  const C=waC(), c=C[per]||(C[per]={}), v=WA_V;
  if(c.loading || (c.res && !force && Date.now()-c.at<10*60e3)) return;
  c.loading=true; c.err=''; if(force) waRender();
  const fin=()=>{ c.loading=false; if(WA_V===v) waRender(); };
  const analyse=raw=>{ c.raw=raw; c.res=wprAnalyze(raw); c.at=Date.now();
    try{ c.res.sets=waSetHints(c.res,waSettings()); }catch(e){ c.res.sets=[]; } c.res.setChg=[]; };
  if(window.__EHS_DEMO){ WA_DEMO=true; setTimeout(()=>{ try{ analyse(WA_BOIL?wprDemoBoiler(per):wprDemo(per)); c.res.setChg=waSetDemoChanges(c.res.sets,c.raw.t0,c.raw.t1); }catch(e){ c.err='Demo: '+e.message; } fin(); },300); return; }
  WA_DEMO=false;
  (async()=>{
    await waMapLoad();
    if(!WPR_SIG.some(s=>waHas(s.id))){ c.err={samsung:'Keine Werte von EHS Sentinel gefunden (Entitäten …ehssentinel_…).',bosch:'Keine Werte der Wärmepumpe von EMS-ESP gefunden (boiler_hp…).',bsb:'Keine Werte des ESP-Bus-Adapters gefunden.'}[v]; return; }
    const raw=await wprFetch(per);
    try{ analyse(raw); }catch(e){ c.err='Auswertung fehlgeschlagen: '+(e.message||e); return; }
    try{ c.res.setChg=await waSetHistory(c.res.sets,raw.t0,raw.t1); }catch(e){}
  })().catch(e=>{ c.err='Verlauf nicht geladen: '+((e&&e.message)||e); }).finally(fin);
}
function waOn(){ return (SYSV==='wpa' && (TAB==='ssystem' || (TAB==='bsb' && BSUB==='system'))) || (TAB==='bosch' && typeof BV!=='undefined' && BV==='bsystem'); }
function waRender(){ if(waOn()) render(); }

// ---- Demodaten Brötje: Gas-Brennwertkessel, taktet in der Übergangszeit, 2 Trinkwasserladungen am Tag ----
function wprDemoBoiler(per){
  const P=WPR_PER.find(p=>p.k===per), stp=P.step*1000, t1=Math.floor(Date.now()/stp)*stp, t0=t1-P.d*864e5;
  const ser={}; WPR_SIG.forEach(s=>ser[s.k]=[]); const push_=(k,t,v)=>{ const a_=ser[k]; if(!a_) return; if(!a_.length||a_[a_.length-1].v!==v) a_.push({t,v}); };
  let rnd=777; const rn=()=>{ rnd=(rnd*16807)%2147483647; return rnd/2147483647; };
  let vl=40, on=false, off=99, ww=50, wwLeft=0, st='Bereit';
  for(let t=t0;t<=t1;t+=stp){ const d=new Date(t), h=d.getHours()+d.getMinutes()/60, day=Math.floor((t-t0)/864e5), dt=stp/60000;
    const ta=7+4*Math.sin((h-9)/24*2*Math.PI)+3*Math.sin(day*1.1+P.d)+(rn()-.5)*0.3, vls=Math.max(28,52-1.3*ta), dem=Math.max(0,(17-ta)*0.6);
    if(wwLeft<=0 && (Math.abs(h-6)<dt/120 || Math.abs(h-17.5)<dt/120)) wwLeft=22+rn()*10;
    let mod=0;
    if(wwLeft>0){ on=true; mod=78; vl=Math.min(72,vl+2*dt); ww=Math.min(56,ww+0.5*dt); wwLeft-=dt; st='In Betrieb für Trinkwasser'; if(wwLeft<=0){ on=false; off=0; } }
    else { const qmin=5.5;
      if(on){ const q=Math.max(qmin,Math.min(20,dem)); mod=Math.round(20+(q-qmin)/(20-qmin)*80); if(dem>=qmin) vl+=(vls-vl)*Math.min(1,0.2*dt); else vl+=(q-dem)*0.35*dt; if(vl>=vls+5 && dem<qmin){ on=false; off=0; } st='In Betrieb für Heizkreis'; }
      else { vl-=Math.max(0.2,dem*0.1)*dt; vl=Math.max(vl,ta+10); off+=dt; if(dem>0.3 && vl<=vls-3 && off>=8){ on=true; } st=dem>0.3?'Bereit':'Ausgeschaltet'; }
      ww=Math.max(42,ww-0.005*dt); }
    const r1=x=>Math.round(x*10)/10, sun=Math.max(0,Math.sin((h-7)/12*Math.PI)), kol=r1(ta+sun*45*(0.5+0.5*Math.sin(day*2.1)));
    push_('hz',t,mod); push_('act',t,st); push_('dhw',t,wwLeft>0?'on':'off'); push_('ta',t,r1(ta)); push_('vl',t,r1(vl)); push_('vls',t,r1(vls));
    push_('vl2',t,r1(Math.min(vl,vls-12))); push_('vls2',t,r1(vls-12)); push_('ww',t,r1(ww)); push_('wwu',t,r1(ww-9)); push_('room',t,r1(21+0.4*Math.sin(h/24*2*Math.PI)));
    push_('q2',t,dem>0.3?1:0); push_('kol',t,kol); push_('kp',t,kol>ww+8?1:0);
  }
  return {t0,t1,step:stp,ser,demo:true};
}
// ---- Bewertung Brötje (Kessel) ----
function wprFindingsBoiler(r){
  const K=r.K, V=r.val, f=[], add=(lv,t,h)=>f.push({lv,t,h});
  if(K.starts>=3){
    if(K.medHeat!=null && K.medHeat<5) add(2,`Brenner taktet stark: typische Laufzeit ${waN(K.medHeat,0)} min je Start (Heizbetrieb)`,'Brennertaktsperre bzw. Schaltdifferenz Kessel erhöhen (Fachmann-Parameter), Heizkennlinie senken, kleinste Kesselleistung zu groß für die Heizlast, Thermostatventile/Volumenstrom prüfen.');
    else if(K.medHeat!=null && K.medHeat<10) add(1,`Brennerlaufzeit kurz: typisch ${waN(K.medHeat,0)} min je Start (Heizbetrieb)`,'Ziel: Brennerläufe ≥ 10–15 min. Taktsperre/Schaltdifferenz anpassen, Heizkennlinie prüfen.');
    else add(0,`Brennerlaufzeit typisch ${waN(K.medHeat,0)} min je Start`,'');
    if(K.perDay>48) add(2,`${waN(K.perDay,1)} Brennerstarts pro Tag`,'Viele Starts erhöhen Verschleiß (Zündung, Gebläse) und Emissionen; Brennwertnutzung sinkt.');
    else if(K.perDay>24) add(1,`${waN(K.perDay,1)} Brennerstarts pro Tag`,'Ziel: unter 20–24 Starts pro Tag.');
    if(K.maxPerHour>4) add(K.maxPerHour>6?2:1,`Bis zu ${K.maxPerHour} Brennerstarts in einer Stunde`,'Hinweis auf Takten – Brennertaktsperre prüfen.');
    if(K.shortShare>0.3) add(2,`${waN(K.shortShare*100,0)} % aller Brennerläufe kürzer als 10 min`,'');
    else if(K.shortShare>0.15) add(1,`${waN(K.shortShare*100,0)} % aller Brennerläufe kürzer als 10 min`,'');
    const md=((V.hz||{}).heat||{}).m; if(md!=null && md<30 && K.medHeat!=null && K.medHeat<15) add(1,`Brenner läuft im Heizbetrieb meist in kleiner Leistung (Ø ${waN(md,0)} % Modulation) und taktet`,'Die kleinste Kesselleistung ist größer als der Wärmebedarf – Heizkennlinie senken, alle Heizkreise offen lassen, ggf. Mindestleistung/Gebläsedrehzahl anpassen.');
    const ovN=r.runs.filter(x=>x.why==='Vorlauf über Soll').length;
    if(ovN>=3 && ovN/Math.max(1,K.heatN)>0.4) add(1,`${ovN} Heizläufe enden mit Vorlauf über Soll`,'Wärmeabnahme zu gering: Heizkennlinie/Parallelverschiebung senken, Pumpe Q2 prüfen.');
  } else add(0,'Kaum Brennerstarts im Zeitraum','Für eine Bewertung längeren Zeitraum wählen.');
  // Trinkwasserladungen (Q3)
  const wl=[]; { let s0=null,p=0; (r.raw.ser.dhw||[]).forEach(x=>{ const v=/^(1|on|ein|true)$/i.test(String(x.v))?1:0; if(v&&!p) s0=x.t; else if(!v&&p&&s0!=null){ wl.push((x.t-s0)/60000); s0=null; } p=v; }); }
  if(wl.length){ const avg=wl.reduce((s,x)=>s+x,0)/wl.length; r.K.wwN=wl.length; r.K.wwAvg=avg;
    if(avg>60) add(1,`Trinkwasserladung dauert lange: Ø ${waN(avg,0)} min`,'Speicher-Wärmetauscher verkalkt/verschlammt, Ladepumpe Q3 oder Kesselvorlauf bei Ladung zu niedrig.');
    else add(0,`${wl.length} Trinkwasserladungen, Ø ${waN(avg,0)} min`,''); }
  const wwm=((V.ww||{}).all||{}).min; if(wwm!=null && wwm<45) add(1,`Trinkwasser zeitweise nur ${waN(wwm,1)} °C`,'Nennsollwert, Ladezeiten und Legionellenschutz prüfen.');
  const sp=((V.vl||{}).heat||{}).m, ss=((V.vls||{}).heat||{}).m; if(sp!=null&&ss!=null&&sp-ss>4) add(1,`Vorlauf im Heizbetrieb Ø ${waN(sp-ss,1)} K über Soll`,'Kessel überschwingt – Taktsperre und Schaltdifferenz prüfen.');
  const err=(r.raw.ser.act||[]).filter(x=>/st(ö|oe)r|fehler|verriegel/i.test(String(x.v)));
  if(err.length){ r.errs=err.map(x=>({t:x.t,c:String(x.v)})); add(2,`${err.length} Störungsmeldung(en) im Kesselstatus`,'Zeitpunkte im Bericht; Störung am Brötje-Bedienteil ablesen.'); }
  else add(0,'Keine Störungsmeldung im Kesselstatus','');
  return f.sort((a_,b_)=>b_.lv-a_.lv);
}

// ---- Entitäten automatisch zuordnen (gleiche Logik wie im Panel) ----
// Bosch: Standard-ID → eigene Zuordnung → automatische Zuordnung des Managers (EMS-ESP unique_id: Kurz-/Langnamen,
// 3.6-Format, Präfix, umbenannt) → gleiche Domain mit Namensende → andere Domain (sensor ↔ number/select/switch).
// Samsung: Namensende „ehssentinel_<name>“ in jeder Domain (ohne Steuermodus sind Einstellwerte sensor.*).
// Brötje: Namensende der ESPHome-Entität des ESP-Bus-Adapters.
const WA_PICK='bsbpick_';
const WA_AG=['hpoperatingmode','summertemp','instantstart','coolstart','heatondelay','heatoffdelay','coolondelay','cooloffdelay'];
const WA_OV_BSB = {"number.thermostat_dhw_settemp":"number.bsbpick_trinkwasser_nennsollwert","select.thermostat_dhw_mode":"select.bsbpick_trinkwasserbetrieb","select.thermostat_hc1_mode":"select.bsbpick_hk1_betriebsart","number.thermostat_hc1_seltemp":"number.bsbpick_hk1_komfortsollwert","number.thermostat_hc1_ecotemp":"number.bsbpick_hk1_reduziertsollwert","number.thermostat_hc1_heatingcurve":"number.bsbpick_hk1_heizkennlinie_steilheit","select.thermostat_hc2_mode":"select.bsbpick_hk2_betriebsart","number.thermostat_hc2_seltemp":"number.bsbpick_hk2_komfortsollwert","number.thermostat_hc2_ecotemp":"number.bsbpick_hk2_reduziertsollwert","number.thermostat_hc2_heatingcurve":"number.bsbpick_hk2_heizkennlinie_steilheit"};
let WA_OV={}, WA_UMAP={}, WA_AMAP={}, WA_HAEMS=[], WA_RES={}, WA_MAPAT=0, WA_GR=null;
const waS=()=>(H()&&H().states)||{};
async function waMapLoad(){
  if(Date.now()-WA_MAPAT<60e3) return; WA_MAPAT=Date.now(); WA_RES={};
  try{ const r=await ws(Object.assign({type:'ems_esp_manager/panel/get'}, WA_V!=='bosch'?{vendor:WA_V}:{}));
    WA_UMAP=Object.assign({},WA_OV,(r&&r.map)||{}); WA_AMAP=(r&&r.auto)||{}; WA_HAEMS=(r&&r.ha_ems)||[]; }catch(e){}
  try{ if(WA_V==='samsung') WA_GR=await ws({type:'ems_esp_manager/samsung/entities'}); else if(WA_V==='bsb') WA_GR=await ws({type:'ems_esp_manager/bsb/entities'}); }catch(e){}
}
function waSamPick(t){ const S=waS(), cur=S[t]; if(cur && cur.state!=='unavailable' && cur.state!=='unknown') return t;
  const ck='@'+t; if(ck in WA_RES){ const r=WA_RES[ck]; if(r===null||S[r]) return r; }
  const dom=t.split('.')[0], obj=t.split('.')[1]||'', suf='ehssentinel_'+obj.replace(/^.*?ehssentinel_/,''), order=[dom,'number','select','switch','sensor','binary_sensor'];
  const c=Object.keys(S).filter(e=>{ const o=e.split('.')[1]||''; return o===suf||o.endsWith('_'+suf); });
  const sc=e=>{ const v=S[e].state; return (v==='unavailable'?20:v==='unknown'?10:0)+Math.max(0,order.indexOf(e.split('.')[0])); };
  c.sort((x,y)=>sc(x)-sc(y)); const hit=c[0]||(cur?t:null); WA_RES[ck]=hit; return hit; }
function waBsbPick(t){ const S=waS(), ck='@'+t; if(ck in WA_RES && WA_RES[ck] && S[WA_RES[ck]]) return WA_RES[ck];
  const dom=t.split('.')[0], suf=(t.split('.')[1]||'').slice(WA_PICK.length), order=[dom,'number','select','sensor','binary_sensor','switch'];
  const c=Object.keys(S).filter(e=>{ const o=e.split('.')[1]||''; return o===suf||o.endsWith('_'+suf); });
  const sc=e=>{ const o=e.split('.')[1], v=S[e].state; return (v==='unavailable'?40:0)+Math.max(0,order.indexOf(e.split('.')[0]))*4+(/brotje|brötje|bsb|heizung/.test(o)?0:2); };
  c.sort((x,y)=>sc(x)-sc(y)); const hit=c[0]||null; WA_RES[ck]=hit; return hit; }
function waR1(id){ const S=waS();
  if(WA_UMAP[id]){ const t=WA_UMAP[id]; return t.includes('ehssentinel')?(waSamPick(t)||t):t.includes(WA_PICK)?(waBsbPick(t)||t):t; }
  if(id.includes('ehssentinel')) return waSamPick(id)||id;
  if(id.includes(WA_PICK)) return waBsbPick(id)||id;
  if(WA_RES[id] && S[WA_RES[id]]) return WA_RES[id];
  if(S[id]){ WA_RES[id]=id; return id; }
  if(WA_AMAP[id] && S[WA_AMAP[id]]){ WA_RES[id]=WA_AMAP[id]; return WA_AMAP[id]; }
  const [dom,obj]=id.split('.');
  if(dom==='sensor'){ const alt='text.'+obj, ra=S[alt]?alt:(WA_AMAP[alt]&&S[WA_AMAP[alt]]?WA_AMAP[alt]:null); if(ra){ WA_RES[id]=ra; return ra; } }
  let cand=Object.keys(S).find(e=>e.startsWith(dom+'.')&&e.endsWith('_'+obj));
  if(!cand){ for(const d of ['number','select','switch','text','sensor','binary_sensor']){ if(d===dom) continue; const alt=d+'.'+obj;
    if(S[alt]){ cand=alt; break; } if(WA_AMAP[alt]&&S[WA_AMAP[alt]]){ cand=WA_AMAP[alt]; break; }
    const c2=Object.keys(S).find(e=>e.startsWith(d+'.')&&e.endsWith('_'+obj)); if(c2){ cand=c2; break; } } }
  WA_RES[id]=cand||null; return cand||id; }
function waResolve(id){ const S=waS(), m=/^(\w+)\.thermostat_hc\d+_(\w+)$/.exec(id);
  if(m && WA_AG.includes(m[2]) && !WA_UMAP[id]){ const g=waR1(m[1]+'.thermostat_'+m[2]); if(S[g]&&S[g].state!=='unavailable') return g; }
  const r=waR1(id); if(S[r]) return r;
  if(m && m[2]==='hpoperatingstate'){ const g=waR1('sensor.boiler_hpactivity'); if(S[g]) return g; }
  return null; }

// ---- Einstellwerte (Bezeichnungen und Gruppen wie im Panel) ----
const WA_SETS = {"bosch":[["Heizkurve Heizkreis 1","","number.thermostat_hc1_designtemp","Auslegungstemperatur","n",0],["Heizkurve Heizkreis 1","","number.thermostat_hc1_maxflowtemp","Max. Vorlauftemperatur","n",0],["Heizkurve Heizkreis 1","","number.thermostat_hc1_minflowtemp","Min. Vorlauftemperatur","n",0],["Heizkurve Heizkreis 1","","number.thermostat_hc1_offsettemp","Parallelverschiebung (Offset)","n",0],["Heizkurve Heizkreis 1","","number.thermostat_hc1_seltemp","Wunschtemperatur","n",0],["Heizkurve Heizkreis 1","","number.thermostat_hc1_ecotemp","Absenktemperatur","n",0],["Heizkurve Heizkreis 2","","number.thermostat_hc2_designtemp","Auslegungstemperatur","n",0],["Heizkurve Heizkreis 2","","number.thermostat_hc2_maxflowtemp","Max. Vorlauftemperatur","n",0],["Heizkurve Heizkreis 2","","number.thermostat_hc2_minflowtemp","Min. Vorlauftemperatur","n",0],["Heizkurve Heizkreis 2","","number.thermostat_hc2_offsettemp","Parallelverschiebung (Offset)","n",0],["Heizkurve Heizkreis 2","","number.thermostat_hc2_seltemp","Wunschtemperatur","n",0],["Heizkurve Heizkreis 2","","number.thermostat_hc2_ecotemp","Absenktemperatur","n",0],["Heizkurve Heizkreis 3","","number.thermostat_hc3_designtemp","Auslegungstemperatur","n",0],["Heizkurve Heizkreis 3","","number.thermostat_hc3_maxflowtemp","Max. Vorlauftemperatur","n",0],["Heizkurve Heizkreis 3","","number.thermostat_hc3_minflowtemp","Min. Vorlauftemperatur","n",0],["Heizkurve Heizkreis 3","","number.thermostat_hc3_offsettemp","Parallelverschiebung (Offset)","n",0],["Heizkurve Heizkreis 3","","number.thermostat_hc3_seltemp","Wunschtemperatur","n",0],["Heizkurve Heizkreis 3","","number.thermostat_hc3_ecotemp","Absenktemperatur","n",0],["Heizkurve Heizkreis 4","","number.thermostat_hc4_designtemp","Auslegungstemperatur","n",0],["Heizkurve Heizkreis 4","","number.thermostat_hc4_maxflowtemp","Max. Vorlauftemperatur","n",0],["Heizkurve Heizkreis 4","","number.thermostat_hc4_minflowtemp","Min. Vorlauftemperatur","n",0],["Heizkurve Heizkreis 4","","number.thermostat_hc4_offsettemp","Parallelverschiebung (Offset)","n",0],["Heizkurve Heizkreis 4","","number.thermostat_hc4_seltemp","Wunschtemperatur","n",0],["Heizkurve Heizkreis 4","","number.thermostat_hc4_ecotemp","Absenktemperatur","n",0],["Einstellungen","","switch.boiler_dhw_alternatingop","Wechselbetrieb","s",0],["Sommer-/Winterumschaltung","","number.thermostat_hc1_summertemp","Heizbetrieb bis","n",0],["Sommer-/Winterumschaltung","","number.thermostat_hc1_coolstart","Kühlbetrieb ab","n",0],["Sommer-/Winterumschaltung","","select.thermostat_hc1_hpoperatingmode","Betriebsart","s",0],["Photovoltaikanlage","","number.thermostat_pvraiseheat","Erhöhung der Wunschtemp. beim Heizen","n",0],["Photovoltaikanlage","","switch.thermostat_pvenabledhw","Erhöhter Warmwasserkomfort","s",0],["Photovoltaikanlage","","number.thermostat_pvlowercool","Absenkung der Wunschtemp. beim Kühlen","n",0],["Photovoltaikanlage","","switch.boiler_pvcooling","Kühlen nur mit PV-Energie","s",0],["Warmwasser","","number.thermostat_dhw_settemp","Wunschtemperatur Komfort","n",1],["Warmwasser","","number.thermostat_dhw_settemplow","Wunschtemperatur Eco","n",1],["Thermische Desinfektion","","switch.thermostat_dhw_disinfecting","Start / Stopp","s",0],["Thermische Desinfektion","","select.thermostat_dhw_disinfectday","Täglich/Wochentag","s",0],["Thermische Desinfektion","","number.thermostat_dhw_disinfecttime","Zeit","n",0],["Thermische Desinfektion","","number.boiler_dhw_disinfectiontemp","Temperatur","n",0],["WW-Zirkulationspumpe","","select.thermostat_dhw_circmode","Betriebsart","s",0],["WW-Zirkulationspumpe","","select.boiler_dhw_circmode","Einschalthäufigkeit","s",0],["WW-Zirkulationspumpe","","switch.boiler_dhw_circ","Zirkulation jetzt","s",0],["WW-Zirkulationspumpe","","switch.boiler_dhw_circpump","Zirkulationspumpe vorhanden","s",1],["Tägliche Aufheizung","","switch.thermostat_dhw_dailyheating","Tägliche Aufheizung","s",0],["Tägliche Aufheizung","","number.thermostat_dhw_dailyheattime","Zeit","n",0],["Wärmepumpe","","number.boiler_tempdiffheat","TC3-TC0 Temp.-diff. Hzg.","n",0],["Wärmepumpe","","number.boiler_tempdiffcool","TC0-TC3 Temp.-diff. Kühl.","n",0],["Wärmepumpe","","switch.boiler_heatcable","Heizkabel (Kondensatablauf)","s",0],["Wärmepumpe","Expertenansicht","number.boiler_hphystheat","Schalthysterese Heizen","n",1],["Wärmepumpe","Expertenansicht","number.boiler_hphystcool","Schalthysterese Kühlen","n",1],["Wärmepumpe","Expertenansicht","number.boiler_hphystpool","Schalthysterese Pool","n",1],["Wärmepumpe","Expertenansicht","number.boiler_fan","Ventilator","n",1],["Wärmepumpe","Expertenansicht","number.boiler_tempparmode","Außentemp. Parallelbetrieb","n",1],["Geräuscharmer Betrieb","","select.boiler_silentmode","Betriebsart","s",0],["Geräuscharmer Betrieb","","number.boiler_silentfrom","Von","n",0],["Geräuscharmer Betrieb","","number.boiler_silentto","Bis","n",0],["Geräuscharmer Betrieb","","number.boiler_mintempsilent","Abschalten unter min. Außentemp.","n",0],["Wechselbetrieb","","number.boiler_dhw_altopprio","Maximaldauer WW","n",0],["Wechselbetrieb","","number.boiler_dhw_altopprioheat","Maximaldauer Heizung","n",0],["Blockierschutz","","select.boiler_pumpkickday","Tag","s",0],["Blockierschutz","","number.boiler_pumpkickhour","Uhrzeit (Stunde)","n",0],["Blockierschutz","","number.boiler_pumpkickdelay","Intervall","n",0],["LIN-bus Pumpen","","select.boiler_hppumpmode","PC0 Betriebsart","s",0],["LIN-bus Pumpen","","number.boiler_hpsetdiffpress","PC0 Soll-Differenzdruck","n",0],["Stromaufnahme wird begrenzt","","number.boiler_hppowerlimit","Leistungsgrenze","n",0],["Stromaufnahme wird begrenzt","","number.boiler_hpmaxpower","Max. Kompressorleistung","n",0],["Stromaufnahme wird begrenzt","","number.boiler_powerreduction","Leistungsreduzierung","n",1],["Zuheizer","","select.boiler_auxheatersource","Zuheizer-Art","s",0],["Zuheizer","","switch.boiler_auxheateroff","Zuheizersperre","s",0],["Zuheizer","","switch.boiler_auxheateronly","Nur Zuheizer","s",0],["Zuheizer","","select.boiler_auxheatrmode","Betrieb nach EVU-Sperre","s",0],["Zuheizer","","number.boiler_auxheaterdelay","Verzögerung Heizung","n",0],["Zuheizer","","number.boiler_auxmaxlimit","Max. Begrenzung","n",0],["Zuheizer","","number.boiler_auxlimitstart","Begrenzung Start","n",0],["Zuheizer","Elektrischer Zuheizer","select.boiler_maxheatcomp","Begrenz. mit Kompressor","s",0],["Zuheizer","Elektrischer Zuheizer","select.boiler_maxheatheat","Lstg. Zuheizer begrenzen","s",0],["Zuheizer","Elektrischer Zuheizer","select.boiler_dhw_maxheat","Lstg. WW-Betr. begrenzen","s",0],["Zuheizer","Notbetrieb","switch.boiler_emergencyops","Notbetrieb","s",0],["Zuheizer","Notbetrieb","number.boiler_emergencytemp","Notbetrieb Temperatur","n",0],["Heizung / Kühlung","Anlageneinstellungen","select.thermostat_building","Dämpfung Gebäudeart","s",0],["Heizung / Kühlung","Anlageneinstellungen","switch.thermostat_damping","Dämpfung Außentemperatur","s",0],["Heizung / Kühlung","Anlageneinstellungen","switch.thermostat_solar","Solareinfluss aktiv","s",1],["Heizung / Kühlung","Anlageneinstellungen","number.thermostat_intoffset","Korrektur interner Fühler","n",1],["Warmwasser","Komfort","number.boiler_dhw_seltemp","Komfort Starttemperatur","n",0],["Warmwasser","Komfort","number.boiler_dhw_comfstop","Komfort Stopptemperatur","n",0],["Warmwasser","Komfort","number.boiler_dhw_comfdiff","Komfort Temp.-diff. für Beladung","n",0],["Warmwasser","Komfort","number.boiler_dhw_comfoff","Komfort Ausschalttemperatur","n",1],["Warmwasser","Eco","number.boiler_dhw_seltemplow","Eco Starttemperatur","n",0],["Warmwasser","Eco","number.boiler_dhw_ecostop","Eco Stopptemperatur","n",0],["Warmwasser","Eco","number.boiler_dhw_ecodiff","Eco Temp.-diff. für Beladung","n",0],["Warmwasser","Eco","number.boiler_dhw_ecooff","Eco Ausschalttemperatur","n",1],["Warmwasser","Eco+","number.boiler_dhw_tempecoplus","Eco+ Starttemperatur","n",0],["Warmwasser","Eco+","number.boiler_dhw_ecoplusstop","Eco+ Stopptemperatur","n",0],["Warmwasser","Eco+","number.boiler_dhw_ecoplusdiff","Eco+ Temp.-diff. für Beladung","n",0],["Warmwasser","Eco+","number.boiler_dhw_ecoplusoff","Eco+ Ausschalttemperatur","n",1],["Warmwasser","Extra-Warmwasser","number.boiler_dhw_seltempsingle","Extra-Warmwasser Temperatur","n",0],["Warmwasser","Extra-Warmwasser","number.boiler_dhw_maxtemp","Max. Temperatur","n",1],["Warmwasser","Weitere","switch.boiler_dhw_hpcircpump","HK-Pumpe an bei WW-Betrieb","s",0],["Warmwasser","Weitere","switch.boiler_dhw_activated","Warmwasser freigegeben","s",0],["Warmwasser","Weitere","switch.boiler_dhw_onetime","Einmalladung","s",0],["Warmwasser","Weitere","number.boiler_dhw_flowtempoffset","Vorlauftemp.-Offset","n",1],["Warmwasser","Weitere","number.boiler_dhw_hyston","Einschalthysterese","n",1],["Warmwasser","Weitere","number.boiler_dhw_hystoff","Ausschalthysterese","n",1],["Photovoltaikanlage","","number.boiler_pvmaxcomp","Max. Kompressorleistung bei PV","n",0],["Heizkreis 1","","select.thermostat_hc1_heatingtype","Heizsystem-Typ","s",0],["Heizkreis 1","","select.thermostat_hc1_control","Fernbedienung","s",0],["Heizkreis 1","","select.thermostat_hc1_hpmode","Systemfunktion","s",0],["Heizkreis 1","Heizen","select.thermostat_hc1_controlmode","Regelungsart","s",0],["Heizkreis 1","Heizen","number.thermostat_hc1_hpminflowtemp","Min. Vorlauftemp. WP","n",0],["Heizkreis 1","Heizen","number.thermostat_hc1_baseflowtemp","Fußpunkt","n",1],["Heizkreis 1","Heizen","number.thermostat_hc1_roominfluence","Raumeinfluss","n",0],["Heizkreis 1","Heizen","number.thermostat_hc1_roominflfactor","Raumeinfluss-Faktor","n",1],["Heizkreis 1","Heizen","number.thermostat_hc1_solarinfl","Solareinfluss","n",0],["Heizkreis 1","Heizen","select.thermostat_hc1_nofrostmode","Frostschutz","s",0],["Heizkreis 1","Heizen","number.thermostat_hc1_nofrosttemp","Frostschutz Grenztemp.","n",0],["Heizkreis 1","Heizen","select.thermostat_hc1_reducemode","Absenkart","s",0],["Heizkreis 1","Heizen","number.thermostat_hc1_noreducetemp","Durchheizen unter","n",0],["Heizkreis 1","Heizen","number.thermostat_hc1_reducetemp","Absenk-/Abschalttemperatur","n",1],["Heizkreis 1","So/Wi Umschaltung","select.thermostat_hc1_summersetmode","Sommer/Winter Heizkreis","s",0],["Heizkreis 1","So/Wi Umschaltung","number.thermostat_hc1_instantstart","Temp.-Diff. Sofortstart","n",0],["Heizkreis 1","So/Wi Umschaltung","number.thermostat_hc1_heatoffdelay","Sommerbetriebverzög.","n",0],["Heizkreis 1","So/Wi Umschaltung","number.thermostat_hc1_heatondelay","Heizbetriebverzög.","n",0],["Heizkreis 1","So/Wi Umschaltung","number.thermostat_hc1_coolondelay","Kühl-Aktivier. verzögert","n",0],["Heizkreis 1","So/Wi Umschaltung","number.thermostat_hc1_cooloffdelay","Kühl-Deaktiv. verzögert","n",0],["Heizkreis 1","Kühlung","number.thermostat_hc1_roomtempdiff","Raumtemp.-Schaltdiff.","n",0],["Heizkreis 1","Kühlung","number.thermostat_hc1_dewoffset","Taupunkt-Temp.diff.","n",0],["Heizkreis 1","Erweitert","switch.thermostat_hc1_dhwprio","Warmwasservorrang","s",1],["Heizkreis 1","Erweitert","select.thermostat_hc1_heatingpid","Regelverhalten (PID)","s",1],["Heizkreis 1","Erweitert","switch.thermostat_hc1_pumpopt","Pumpenoptimierung","s",1],["Heizkreis 1","Erweitert","switch.thermostat_hc1_switchonoptimization","Einschaltoptimierung","s",1],["Heizkreis 1","Erweitert","number.thermostat_hc1_fastheatup","Schnellaufheizung","n",1],["Heizkreis 1","Erweitert","number.thermostat_hc1_inttime","Integralzeit","n",1],["Heizkreis 1","Erweitert","number.thermostat_hc1_inttimefloor","Integralzeit Fußboden","n",1],["Heizkreis 1","Erweitert","select.thermostat_hc1_switchprogmode","Schaltprogramm-Modus","s",1],["Kühlen","","number.thermostat_hc1_cooltemp","Wunschtemperatur Kühlen","n",0],["Kühlen","","switch.thermostat_hc1_hpcooling","Kühlbetrieb freigegeben","s",0],["Heizkreis 2","","select.thermostat_hc2_heatingtype","Heizsystem-Typ","s",0],["Heizkreis 2","","select.thermostat_hc2_control","Fernbedienung","s",0],["Heizkreis 2","","select.thermostat_hc2_hpmode","Systemfunktion","s",0],["Heizkreis 2","Heizen","select.thermostat_hc2_controlmode","Regelungsart","s",0],["Heizkreis 2","Heizen","number.thermostat_hc2_hpminflowtemp","Min. Vorlauftemp. WP","n",0],["Heizkreis 2","Heizen","number.thermostat_hc2_baseflowtemp","Fußpunkt","n",1],["Heizkreis 2","Heizen","number.thermostat_hc2_roominfluence","Raumeinfluss","n",0],["Heizkreis 2","Heizen","number.thermostat_hc2_roominflfactor","Raumeinfluss-Faktor","n",1],["Heizkreis 2","Heizen","number.thermostat_hc2_solarinfl","Solareinfluss","n",0],["Heizkreis 2","Heizen","select.thermostat_hc2_nofrostmode","Frostschutz","s",0],["Heizkreis 2","Heizen","number.thermostat_hc2_nofrosttemp","Frostschutz Grenztemp.","n",0],["Heizkreis 2","Heizen","select.thermostat_hc2_reducemode","Absenkart","s",0],["Heizkreis 2","Heizen","number.thermostat_hc2_noreducetemp","Durchheizen unter","n",0],["Heizkreis 2","Heizen","number.thermostat_hc2_reducetemp","Absenk-/Abschalttemperatur","n",1],["Heizkreis 2","So/Wi Umschaltung","select.thermostat_hc2_hpoperatingmode","Betriebsart","s",0],["Heizkreis 2","So/Wi Umschaltung","number.thermostat_hc2_summertemp","Heizbetrieb bis","n",0],["Heizkreis 2","So/Wi Umschaltung","number.thermostat_hc2_coolstart","Kühlbetrieb ab","n",0],["Heizkreis 2","So/Wi Umschaltung","select.thermostat_hc2_summersetmode","Sommer/Winter Heizkreis","s",0],["Heizkreis 2","So/Wi Umschaltung","number.thermostat_hc2_instantstart","Temp.-Diff. Sofortstart","n",0],["Heizkreis 2","So/Wi Umschaltung","number.thermostat_hc2_heatoffdelay","Sommerbetriebverzög.","n",0],["Heizkreis 2","So/Wi Umschaltung","number.thermostat_hc2_heatondelay","Heizbetriebverzög.","n",0],["Heizkreis 2","So/Wi Umschaltung","number.thermostat_hc2_coolondelay","Kühl-Aktivier. verzögert","n",0],["Heizkreis 2","So/Wi Umschaltung","number.thermostat_hc2_cooloffdelay","Kühl-Deaktiv. verzögert","n",0],["Heizkreis 2","Kühlung","number.thermostat_hc2_roomtempdiff","Raumtemp.-Schaltdiff.","n",0],["Heizkreis 2","Kühlung","number.thermostat_hc2_dewoffset","Taupunkt-Temp.diff.","n",0],["Heizkreis 2","Erweitert","switch.thermostat_hc2_dhwprio","Warmwasservorrang","s",1],["Heizkreis 2","Erweitert","select.thermostat_hc2_heatingpid","Regelverhalten (PID)","s",1],["Heizkreis 2","Erweitert","switch.thermostat_hc2_pumpopt","Pumpenoptimierung","s",1],["Heizkreis 2","Erweitert","switch.thermostat_hc2_switchonoptimization","Einschaltoptimierung","s",1],["Heizkreis 2","Erweitert","number.thermostat_hc2_fastheatup","Schnellaufheizung","n",1],["Heizkreis 2","Erweitert","number.thermostat_hc2_inttime","Integralzeit","n",1],["Heizkreis 2","Erweitert","number.thermostat_hc2_inttimefloor","Integralzeit Fußboden","n",1],["Heizkreis 2","Erweitert","select.thermostat_hc2_switchprogmode","Schaltprogramm-Modus","s",1],["Kühlen","","number.thermostat_hc2_cooltemp","Wunschtemperatur Kühlen","n",0],["Kühlen","","switch.thermostat_hc2_hpcooling","Kühlbetrieb freigegeben","s",0],["Heizkreis 3","","select.thermostat_hc3_heatingtype","Heizsystem-Typ","s",0],["Heizkreis 3","","select.thermostat_hc3_control","Fernbedienung","s",0],["Heizkreis 3","","select.thermostat_hc3_hpmode","Systemfunktion","s",0],["Heizkreis 3","Heizen","select.thermostat_hc3_controlmode","Regelungsart","s",0],["Heizkreis 3","Heizen","number.thermostat_hc3_hpminflowtemp","Min. Vorlauftemp. WP","n",0],["Heizkreis 3","Heizen","number.thermostat_hc3_baseflowtemp","Fußpunkt","n",1],["Heizkreis 3","Heizen","number.thermostat_hc3_roominfluence","Raumeinfluss","n",0],["Heizkreis 3","Heizen","number.thermostat_hc3_roominflfactor","Raumeinfluss-Faktor","n",1],["Heizkreis 3","Heizen","number.thermostat_hc3_solarinfl","Solareinfluss","n",0],["Heizkreis 3","Heizen","select.thermostat_hc3_nofrostmode","Frostschutz","s",0],["Heizkreis 3","Heizen","number.thermostat_hc3_nofrosttemp","Frostschutz Grenztemp.","n",0],["Heizkreis 3","Heizen","select.thermostat_hc3_reducemode","Absenkart","s",0],["Heizkreis 3","Heizen","number.thermostat_hc3_noreducetemp","Durchheizen unter","n",0],["Heizkreis 3","Heizen","number.thermostat_hc3_reducetemp","Absenk-/Abschalttemperatur","n",1],["Heizkreis 3","So/Wi Umschaltung","select.thermostat_hc3_hpoperatingmode","Betriebsart","s",0],["Heizkreis 3","So/Wi Umschaltung","number.thermostat_hc3_summertemp","Heizbetrieb bis","n",0],["Heizkreis 3","So/Wi Umschaltung","number.thermostat_hc3_coolstart","Kühlbetrieb ab","n",0],["Heizkreis 3","So/Wi Umschaltung","select.thermostat_hc3_summersetmode","Sommer/Winter Heizkreis","s",0],["Heizkreis 3","So/Wi Umschaltung","number.thermostat_hc3_instantstart","Temp.-Diff. Sofortstart","n",0],["Heizkreis 3","So/Wi Umschaltung","number.thermostat_hc3_heatoffdelay","Sommerbetriebverzög.","n",0],["Heizkreis 3","So/Wi Umschaltung","number.thermostat_hc3_heatondelay","Heizbetriebverzög.","n",0],["Heizkreis 3","So/Wi Umschaltung","number.thermostat_hc3_coolondelay","Kühl-Aktivier. verzögert","n",0],["Heizkreis 3","So/Wi Umschaltung","number.thermostat_hc3_cooloffdelay","Kühl-Deaktiv. verzögert","n",0],["Heizkreis 3","Kühlung","number.thermostat_hc3_roomtempdiff","Raumtemp.-Schaltdiff.","n",0],["Heizkreis 3","Kühlung","number.thermostat_hc3_dewoffset","Taupunkt-Temp.diff.","n",0],["Heizkreis 3","Erweitert","switch.thermostat_hc3_dhwprio","Warmwasservorrang","s",1],["Heizkreis 3","Erweitert","select.thermostat_hc3_heatingpid","Regelverhalten (PID)","s",1],["Heizkreis 3","Erweitert","switch.thermostat_hc3_pumpopt","Pumpenoptimierung","s",1],["Heizkreis 3","Erweitert","switch.thermostat_hc3_switchonoptimization","Einschaltoptimierung","s",1],["Heizkreis 3","Erweitert","number.thermostat_hc3_fastheatup","Schnellaufheizung","n",1],["Heizkreis 3","Erweitert","number.thermostat_hc3_inttime","Integralzeit","n",1],["Heizkreis 3","Erweitert","number.thermostat_hc3_inttimefloor","Integralzeit Fußboden","n",1],["Heizkreis 3","Erweitert","select.thermostat_hc3_switchprogmode","Schaltprogramm-Modus","s",1],["Kühlen","","number.thermostat_hc3_cooltemp","Wunschtemperatur Kühlen","n",0],["Kühlen","","switch.thermostat_hc3_hpcooling","Kühlbetrieb freigegeben","s",0],["Heizkreis 4","","select.thermostat_hc4_heatingtype","Heizsystem-Typ","s",0],["Heizkreis 4","","select.thermostat_hc4_control","Fernbedienung","s",0],["Heizkreis 4","","select.thermostat_hc4_hpmode","Systemfunktion","s",0],["Heizkreis 4","Heizen","select.thermostat_hc4_controlmode","Regelungsart","s",0],["Heizkreis 4","Heizen","number.thermostat_hc4_hpminflowtemp","Min. Vorlauftemp. WP","n",0],["Heizkreis 4","Heizen","number.thermostat_hc4_baseflowtemp","Fußpunkt","n",1],["Heizkreis 4","Heizen","number.thermostat_hc4_roominfluence","Raumeinfluss","n",0],["Heizkreis 4","Heizen","number.thermostat_hc4_roominflfactor","Raumeinfluss-Faktor","n",1],["Heizkreis 4","Heizen","number.thermostat_hc4_solarinfl","Solareinfluss","n",0],["Heizkreis 4","Heizen","select.thermostat_hc4_nofrostmode","Frostschutz","s",0],["Heizkreis 4","Heizen","number.thermostat_hc4_nofrosttemp","Frostschutz Grenztemp.","n",0],["Heizkreis 4","Heizen","select.thermostat_hc4_reducemode","Absenkart","s",0],["Heizkreis 4","Heizen","number.thermostat_hc4_noreducetemp","Durchheizen unter","n",0],["Heizkreis 4","Heizen","number.thermostat_hc4_reducetemp","Absenk-/Abschalttemperatur","n",1],["Heizkreis 4","So/Wi Umschaltung","select.thermostat_hc4_hpoperatingmode","Betriebsart","s",0],["Heizkreis 4","So/Wi Umschaltung","number.thermostat_hc4_summertemp","Heizbetrieb bis","n",0],["Heizkreis 4","So/Wi Umschaltung","number.thermostat_hc4_coolstart","Kühlbetrieb ab","n",0],["Heizkreis 4","So/Wi Umschaltung","select.thermostat_hc4_summersetmode","Sommer/Winter Heizkreis","s",0],["Heizkreis 4","So/Wi Umschaltung","number.thermostat_hc4_instantstart","Temp.-Diff. Sofortstart","n",0],["Heizkreis 4","So/Wi Umschaltung","number.thermostat_hc4_heatoffdelay","Sommerbetriebverzög.","n",0],["Heizkreis 4","So/Wi Umschaltung","number.thermostat_hc4_heatondelay","Heizbetriebverzög.","n",0],["Heizkreis 4","So/Wi Umschaltung","number.thermostat_hc4_coolondelay","Kühl-Aktivier. verzögert","n",0],["Heizkreis 4","So/Wi Umschaltung","number.thermostat_hc4_cooloffdelay","Kühl-Deaktiv. verzögert","n",0],["Heizkreis 4","Kühlung","number.thermostat_hc4_roomtempdiff","Raumtemp.-Schaltdiff.","n",0],["Heizkreis 4","Kühlung","number.thermostat_hc4_dewoffset","Taupunkt-Temp.diff.","n",0],["Heizkreis 4","Erweitert","switch.thermostat_hc4_dhwprio","Warmwasservorrang","s",1],["Heizkreis 4","Erweitert","select.thermostat_hc4_heatingpid","Regelverhalten (PID)","s",1],["Heizkreis 4","Erweitert","switch.thermostat_hc4_pumpopt","Pumpenoptimierung","s",1],["Heizkreis 4","Erweitert","switch.thermostat_hc4_switchonoptimization","Einschaltoptimierung","s",1],["Heizkreis 4","Erweitert","number.thermostat_hc4_fastheatup","Schnellaufheizung","n",1],["Heizkreis 4","Erweitert","number.thermostat_hc4_inttime","Integralzeit","n",1],["Heizkreis 4","Erweitert","number.thermostat_hc4_inttimefloor","Integralzeit Fußboden","n",1],["Heizkreis 4","Erweitert","select.thermostat_hc4_switchprogmode","Schaltprogramm-Modus","s",1],["Kühlen","","number.thermostat_hc4_cooltemp","Wunschtemperatur Kühlen","n",0],["Kühlen","","switch.thermostat_hc4_hpcooling","Kühlbetrieb freigegeben","s",0]],"samsung":[["Heizkurve (Zone 1)","","number.samsung_ehssentinel_intempwaterlawtargetf","Heizkurven-Verschiebung","n",0],["Warmwasser","","number.samsung_ehssentinel_indoordhwsettemp","Warmwasser-Soll","n",0],["Warmwasser","","select.samsung_ehssentinel_dhwopmode","Warmwasser-Betriebsart","s",0],["Betrieb","","select.samsung_ehssentinel_power","Wärmepumpe","s",0],["Betrieb","","select.samsung_ehssentinel_indooropmode","Betriebsart","s",0],["Betrieb","","switch.samsung_ehssentinel_controlsilence","Flüstermodus","s",0],["Betrieb","","switch.samsung_ehssentinel_indooroutgoing","Abwesenheit (Outing)","s",0],["Betrieb","Zone 2","select.samsung_ehssentinel_powerzone2","Zone 2","s",0],["Betrieb","Zone 2","number.samsung_ehssentinel_intempwateroutlettargetzone2f","Zone 2 Vorlauf-Soll","n",0],["Betrieb","Verdichter","number.samsung_ehssentinel_outdoorcomp1orderhz","Frequenz-Vorgabe","n",1],["Betrieb","Verdichter","select.samsung_ehssentinel_outcompfreqratecontrol","Leistungsbegrenzung (FSV 5051)","s",0],["FSV 10xx","Heizen","number.samsung_ehssentinel_infsv1031","Vorlauf max. (1031)","n",0],["FSV 10xx","Heizen","number.samsung_ehssentinel_infsv1032","Vorlauf min. (1032)","n",0],["FSV 10xx","Heizen","number.samsung_ehssentinel_infsv1041","Raum max. (1041)","n",0],["FSV 10xx","Heizen","number.samsung_ehssentinel_infsv1042","Raum min. (1042)","n",0],["FSV 10xx","Warmwasser","number.samsung_ehssentinel_infsv1051","Speicher max. (1051)","n",0],["FSV 10xx","Warmwasser","number.samsung_ehssentinel_infsv1052","Speicher min. (1052)","n",0],["FSV 10xx","Kühlen","number.samsung_ehssentinel_infsv1011","Vorlauf max. (1011)","n",0],["FSV 10xx","Kühlen","number.samsung_ehssentinel_infsv1012","Vorlauf min. (1012)","n",0],["FSV 10xx","Kühlen","number.samsung_ehssentinel_infsv1021","Raum max. (1021)","n",0],["FSV 10xx","Kühlen","number.samsung_ehssentinel_infsv1022","Raum min. (1022)","n",0],["FSV 20xx Heizkurve","Heizen","number.samsung_ehssentinel_infsv2011","Außentemp. kalt (2011)","n",0],["FSV 20xx Heizkurve","Heizen","number.samsung_ehssentinel_infsv2012","Außentemp. warm (2012)","n",0],["FSV 20xx Heizkurve","Heizen","number.samsung_ehssentinel_infsv2021","Vorlauf bei kalt WL1 (2021)","n",0],["FSV 20xx Heizkurve","Heizen","number.samsung_ehssentinel_infsv2022","Vorlauf bei warm WL1 (2022)","n",0],["FSV 20xx Heizkurve","Heizen","number.samsung_ehssentinel_infsv2031","Vorlauf bei kalt WL2 (2031)","n",0],["FSV 20xx Heizkurve","Heizen","number.samsung_ehssentinel_infsv2032","Vorlauf bei warm WL2 (2032)","n",0],["FSV 20xx Heizkurve","Heizen","select.samsung_ehssentinel_infsv2041","Heizkurven-Typ (2041)","s",0],["FSV 20xx Heizkurve","Kühlen","number.samsung_ehssentinel_infsv2051","Außentemp. max (2051)","n",0],["FSV 20xx Heizkurve","Kühlen","number.samsung_ehssentinel_infsv2052","Außentemp. min (2052)","n",0],["FSV 20xx Heizkurve","Kühlen","number.samsung_ehssentinel_infsv2061","Vorlauf WL1 max (2061)","n",0],["FSV 20xx Heizkurve","Kühlen","number.samsung_ehssentinel_infsv2062","Vorlauf WL1 min (2062)","n",0],["FSV 20xx Heizkurve","Kühlen","number.samsung_ehssentinel_infsv2071","Vorlauf WL2 max (2071)","n",0],["FSV 20xx Heizkurve","Kühlen","number.samsung_ehssentinel_infsv2072","Vorlauf WL2 min (2072)","n",0],["FSV 20xx Heizkurve","Kühlen","select.samsung_ehssentinel_infsv2081","Kühlkurven-Typ (2081)","s",0],["FSV 20xx Heizkurve","Raumthermostat","select.samsung_ehssentinel_infsv2091","Ext. Thermostat WL1 (2091)","s",0],["FSV 20xx Heizkurve","Raumthermostat","select.samsung_ehssentinel_infsv2092","Ext. Thermostat WL2 (2092)","s",0],["FSV 30xx Warmwasser","","select.samsung_ehssentinel_infsv3011","WW-Anwendung (3011)","s",0],["FSV 30xx Warmwasser","Wärmepumpe","number.samsung_ehssentinel_infsv3021","WP max. Temperatur (3021)","n",0],["FSV 30xx Warmwasser","Wärmepumpe","number.samsung_ehssentinel_infsv3022","WP Stopp-Diff. (3022)","n",0],["FSV 30xx Warmwasser","Wärmepumpe","number.samsung_ehssentinel_infsv3023","WP Start-Diff. (3023)","n",0],["FSV 30xx Warmwasser","Wärmepumpe","number.samsung_ehssentinel_infsv3024","Min. Heizlaufzeit (3024)","n",0],["FSV 30xx Warmwasser","Wärmepumpe","number.samsung_ehssentinel_infsv3025","Max. WW-Laufzeit (3025)","n",0],["FSV 30xx Warmwasser","Wärmepumpe","number.samsung_ehssentinel_infsv3026","Max. Heizlaufzeit (3026)","n",0],["FSV 30xx Warmwasser","Booster-Heizstab","number.samsung_ehssentinel_infsv3032","Verzögerung (3032)","n",0],["FSV 30xx Warmwasser","Desinfektion","switch.samsung_ehssentinel_infsv3041","Desinfektion (3041)","s",0],["FSV 30xx Warmwasser","Desinfektion","select.samsung_ehssentinel_infsv3042","Intervall (3042)","s",0],["FSV 30xx Warmwasser","Desinfektion","number.samsung_ehssentinel_infsv3043","Startzeit (3043)","n",0],["FSV 30xx Warmwasser","Desinfektion","number.samsung_ehssentinel_infsv3044","Temperatur (3044)","n",0],["FSV 30xx Warmwasser","Desinfektion","number.samsung_ehssentinel_infsv3045","Dauer (3045)","n",0],["FSV 30xx Warmwasser","Desinfektion","number.samsung_ehssentinel_infsv3046","Max. Zeit (3046)","n",0],["FSV 30xx Warmwasser","Forced DHW","select.samsung_ehssentinel_infsv3051","Zeitbegrenzung (3051)","s",0],["FSV 30xx Warmwasser","Forced DHW","number.samsung_ehssentinel_infsv3052","Dauer (3052)","n",0],["FSV 30xx Warmwasser","Weitere","select.samsung_ehssentinel_infsv3061","Solar / Thermostat (3061)","s",0],["FSV 30xx Warmwasser","Weitere","select.samsung_ehssentinel_infsv3071","3-Wege-Ventil Richtung (3071)","s",0],["FSV 30xx Warmwasser","Weitere","number.samsung_ehssentinel_infsv3081","BUH Stufe 1 Leistung (3081)","n",1],["FSV 30xx Warmwasser","Weitere","number.samsung_ehssentinel_infsv3082","BUH Stufe 2 Leistung (3082)","n",1],["FSV 30xx Warmwasser","Weitere","number.samsung_ehssentinel_infsv3083","BSH Leistung (3083)","n",1],["FSV 40xx Heizung","","select.samsung_ehssentinel_infsv4011","Vorrang Heizen/WW (4011)","s",0],["FSV 40xx Heizung","","number.samsung_ehssentinel_infsv4012","Außentemp. für Vorrang (4012)","n",0],["FSV 40xx Heizung","","number.samsung_ehssentinel_infsv4013","Heizgrenze (4013)","n",0],["FSV 40xx Heizung","Backup-Heizer","select.samsung_ehssentinel_infsv4021","Anwendung (4021)","s",0],["FSV 40xx Heizung","Backup-Heizer","select.samsung_ehssentinel_infsv4022","BUH/BSH Vorrang (4022)","s",0],["FSV 40xx Heizung","Backup-Heizer","select.samsung_ehssentinel_infsv4023","Kaltwetter-Kompensation (4023)","s",0],["FSV 40xx Heizung","Backup-Heizer","number.samsung_ehssentinel_infsv4024","Schwelltemperatur (4024)","n",0],["FSV 40xx Heizung","Backup-Heizer","number.samsung_ehssentinel_infsv4025","Abtau-Backup Temp. (4025)","n",0],["FSV 40xx Heizung","Backup-Kessel","select.samsung_ehssentinel_infsv4031","Anwendung (4031)","s",0],["FSV 40xx Heizung","Backup-Kessel","select.samsung_ehssentinel_infsv4032","Kesselvorrang (4032)","s",0],["FSV 40xx Heizung","Backup-Kessel","number.samsung_ehssentinel_infsv4033","Schwelle (4033)","n",0],["FSV 40xx Heizung","Mischer","select.samsung_ehssentinel_infsv4041","Mischer (4041)","s",0],["FSV 40xx Heizung","Mischer","number.samsung_ehssentinel_infsv4042","ΔT Heizen (4042)","n",0],["FSV 40xx Heizung","Mischer","number.samsung_ehssentinel_infsv4043","ΔT Kühlen (4043)","n",0],["FSV 40xx Heizung","Mischer","select.samsung_ehssentinel_infsv4044","Regelfaktor (4044)","s",0],["FSV 40xx Heizung","Mischer","number.samsung_ehssentinel_infsv4045","Regelfaktor (4045)","n",1],["FSV 40xx Heizung","Mischer","number.samsung_ehssentinel_infsv4046","Laufzeit (4046)","n",0],["FSV 40xx Heizung","Pumpe","select.samsung_ehssentinel_infsv4051","Inverter-Pumpe (4051)","s",0],["FSV 40xx Heizung","Pumpe","number.samsung_ehssentinel_infsv4052","Pumpe ΔT (4052)","n",0],["FSV 40xx Heizung","Pumpe","select.samsung_ehssentinel_infsv4053","Regelfaktor (4053)","s",0],["FSV 40xx Heizung","Zonen","select.samsung_ehssentinel_infsv4061","Zonenregelung (4061)","s",0],["FSV 50xx Sonstiges","Abwesenheit (Outing)","number.samsung_ehssentinel_infsv5013","Vorlauf Heizen (5013)","n",0],["FSV 50xx Sonstiges","Abwesenheit (Outing)","number.samsung_ehssentinel_infsv5014","Raum Heizen (5014)","n",0],["FSV 50xx Sonstiges","Abwesenheit (Outing)","number.samsung_ehssentinel_infsv5017","Auto Heizen WL1 (5017)","n",0],["FSV 50xx Sonstiges","Abwesenheit (Outing)","number.samsung_ehssentinel_infsv5018","Auto Heizen WL2 (5018)","n",0],["FSV 50xx Sonstiges","Abwesenheit (Outing)","number.samsung_ehssentinel_infsv5019","Speicher (5019)","n",0],["FSV 50xx Sonstiges","Abwesenheit (Outing)","number.samsung_ehssentinel_infsv5011","Vorlauf Kühlen (5011)","n",1],["FSV 50xx Sonstiges","Abwesenheit (Outing)","number.samsung_ehssentinel_infsv5012","Raum Kühlen (5012)","n",1],["FSV 50xx Sonstiges","WW-Sparbetrieb","switch.samsung_ehssentinel_infsv5022","Sparbetrieb (5022)","s",0],["FSV 50xx Sonstiges","WW-Sparbetrieb","number.samsung_ehssentinel_infsv5021","Spartemperatur (5021)","n",0],["FSV 50xx Sonstiges","WW-Sparbetrieb","number.samsung_ehssentinel_infsv5023","Thermo-on Temp. (5023)","n",0],["FSV 50xx Sonstiges","Leistung","select.samsung_ehssentinel_infsv5041","Leistungsspitzen-Steuerung (5041)","s",0],["FSV 50xx Sonstiges","Leistung","select.samsung_ehssentinel_infsv5042","Zwangsabschaltung (5042)","s",0],["FSV 50xx Sonstiges","Leistung","select.samsung_ehssentinel_infsv5043","Eingangsspannung (5043)","s",0],["FSV 50xx Sonstiges","Leistung","select.samsung_ehssentinel_infsv5051","Frequenzverhältnis (5051)","s",0],["FSV 50xx Sonstiges","Leistung","select.samsung_ehssentinel_infsv5061","WW/Heizen-Verhältnis (5061)","s",0],["FSV 50xx Sonstiges","PV","select.samsung_ehssentinel_infsv5081","PV-Steuerung (5081)","s",0],["FSV 50xx Sonstiges","PV","number.samsung_ehssentinel_infsv5083","Anhebung Heizen (5083)","n",0],["FSV 50xx Sonstiges","PV","number.samsung_ehssentinel_infsv5082","Absenkung Kühlen (5082)","n",0],["FSV 50xx Sonstiges","Smart Grid","select.samsung_ehssentinel_infsv5091","Smart Grid (5091)","s",0],["FSV 50xx Sonstiges","Smart Grid","number.samsung_ehssentinel_infsv5092","Anhebung Heizen (5092)","n",0],["FSV 50xx Sonstiges","Smart Grid","number.samsung_ehssentinel_infsv5093","Anhebung WW (5093)","n",0],["FSV 50xx Sonstiges","Smart Grid","select.samsung_ehssentinel_infsv5094","WW-Modus (5094)","s",0]],"bsb":[["Heizkreis 1","Sollwerte","select.thermostat_hc1_mode","Betriebsart","s",0],["Heizkreis 1","Sollwerte","number.thermostat_hc1_seltemp","Komfortsollwert","n",0],["Heizkreis 1","Sollwerte","number.thermostat_hc1_ecotemp","Reduziertsollwert","n",0],["Heizkreis 1","Sollwerte","number.thermostat_hc1_heatingcurve","Heizkennlinie Steilheit","n",0],["Heizkreis 2","Sollwerte","select.thermostat_hc2_mode","Betriebsart","s",0],["Heizkreis 2","Sollwerte","number.thermostat_hc2_seltemp","Komfortsollwert","n",0],["Heizkreis 2","Sollwerte","number.thermostat_hc2_ecotemp","Reduziertsollwert","n",0],["Heizkreis 2","Sollwerte","number.thermostat_hc2_heatingcurve","Heizkennlinie Steilheit","n",0],["Trinkwasser","","select.thermostat_dhw_mode","Trinkwasserbetrieb","s",0],["Trinkwasser","","number.thermostat_dhw_settemp","Nennsollwert","n",0]]}; let WA_SET=[];
function waVendorSet(v){ WA_OV=v==='bsb'?WA_OV_BSB:{}; WA_UMAP=Object.assign({},WA_OV); WA_AMAP={}; WA_HAEMS=[]; WA_GR=null; WA_RES={}; WA_MAPAT=0; WA_SET=WA_SETS[v]||[]; }
function waFmt(s,a){ if(s==null||s==='unknown'||s==='unavailable') return '–'; if(s==='on') return 'Ein'; if(s==='off') return 'Aus';
  const v=parseFloat(s); if(!isNaN(v) && /^-?[\d.]+$/.test(String(s).trim())){ const st=+((a||{}).step)||0; return waN(v,st&&st<1?(st<0.1?2:1):(v%1?1:0))+((a||{}).unit_of_measurement?' '+a.unit_of_measurement:''); } return String(s); }
function waSettings(){
  const S=waS(), out=[], used=new Set();
  WA_SET.forEach(([g,sec,id,l,k,adv])=>{ const r=waResolve(id); if(!r||used.has(r)) return; const s=S[r]; if(!s||s.state==='unavailable') return; used.add(r);
    out.push({g,sec,id,r,l,k,adv,st:s.state,a:s.attributes||{}}); });
  // weitere Einstellwerte, die die Anlage liefert, aber nicht im Panel-Menü stehen (andere Versionen/Modelle)
  const extra=[]; const lab=e=>{ const a=(S[e]||{}).attributes||{}; return a.friendly_name||e.split('.')[1]; };
  if(WA_V==='bosch') WA_HAEMS.filter(e=>/^(number|select|switch)\./.test(e)).forEach(e=>extra.push({g:'Weitere Einstellwerte (EMS-ESP)',r:e,l:lab(e)}));
  else if(WA_GR && WA_GR.settings){ const gs=Array.isArray(WA_GR.settings)?WA_GR.settings:Object.entries(WA_GR.settings).map(([title,items])=>({title,items}));
    gs.forEach(gr=>{ if(/adapter/i.test(gr.title||'')) return; (gr.items||[]).forEach(it=>{ const e=it.id||it.entity_id; if(e && !/^button\./.test(e)) extra.push({g:'Weitere – '+(gr.title||''),r:e,l:it.label||lab(e)}); }); }); }
  extra.forEach(x=>{ if(used.has(x.r)) return; const s=S[x.r]; if(!s||s.state==='unavailable'||s.state==='unknown') return; used.add(x.r);
    out.push({g:x.g,sec:'',id:x.r,r:x.r,l:x.l,k:x.r.split('.')[0][0],adv:1,st:s.state,a:s.attributes||{}}); });
  return out; }

// ---- Bezug Einstellung ↔ Auffälligkeit: nur ausgewählte Stellgrößen, mit konkretem Hinweis ----
// [Hersteller, Muster auf die Entität, Thema, Hinweis]
const WA_RULES=[
  ['samsung',/infsv202[12]$|intempwaterlawtargetf$/,'takt','Heizkurve bzw. Verschiebung etwas senken – weniger Vorlauf, längere Laufzeiten, bessere Arbeitszahl.'],
  ['samsung',/infsv1032$/,'takt','Min. Vorlauf: liegt er über dem Bedarf, erreicht die WP das Soll sofort und schaltet ab.'],
  ['samsung',/infsv209[12]$/,'takt','Mit externem Raumthermostat schaltet das Thermostat die WP ein und aus – häufige Ursache für Takten.'],
  ['samsung',/infsv4013$/,'takt','Heizgrenze: oberhalb dieser Außentemperatur heizt die WP nicht – zu hoch eingestellt verlängert sie Kurzläufe in der Übergangszeit.'],
  ['samsung',/infsv202[12]$|infsv1031$/,'eff','Jedes Kelvin weniger Vorlauf verbessert die Arbeitszahl um etwa 2–3 %.'],
  ['samsung',/infsv302[23]$/,'ww','Start-/Stopp-Differenz bestimmt, wie oft und wie lange Warmwasser geladen wird.'],
  ['samsung',/infsv302[456]$|infsv4011$/,'ww','Laufzeiten Warmwasser/Heizen und Vorrang regeln den Wechsel zwischen WW und Heizen.'],
  ['samsung',/infsv3021$|indoordhwsettemp$/,'ww','Warmwasser-Temperatur so niedrig wie möglich (ca. 48–50 °C).'],
  ['samsung',/infsv402[1-5]$|infsv3032$|infsv308[123]$|infsv403[123]$/,'aux','Freigabe von Backup-Heizer/Booster: Schwelle und Verzögerung so wählen, dass er nur bei Kälte oder Abtauung hilft.'],
  ['samsung',/infsv405[123]$|infsv4042$/,'hyd','Pumpen-ΔT/Regelfaktor bestimmen Durchfluss und Spreizung (Ziel 3–7 K).'],
  ['samsung',/infsv4025$/,'def','Abtau-Backup: unter dieser Außentemperatur hilft der Backup-Heizer beim Abtauen.'],
  ['bosch',/thermostat_hc\d_(designtemp|offsettemp|maxflowtemp|seltemp)$/,'takt','Heizkurve (Auslegung/Offset) etwas senken – weniger Vorlauf, längere Laufzeiten.'],
  ['bosch',/thermostat_hc\d_(minflowtemp|hpminflowtemp|baseflowtemp)$/,'takt','Min. Vorlauf/Fußpunkt: liegt er über dem Bedarf, erreicht die WP das Soll sofort und schaltet ab.'],
  ['bosch',/boiler_hphystheat$/,'takt','Schalthysterese Heizen: größerer Wert → längere Laufzeiten, weniger Starts.'],
  ['bosch',/thermostat_hc\d_(roominfluence|roominflfactor|roomtempdiff|instantstart)$/,'takt','Raumeinfluss/Schaltdifferenz: starker Raumeinfluss schaltet die WP häufig ein und aus.'],
  ['bosch',/thermostat_hc\d_(designtemp|maxflowtemp)$/,'eff','Jedes Kelvin weniger Vorlauf verbessert die Arbeitszahl um etwa 2–3 %.'],
  ['bosch',/boiler_dhw_(comfdiff|ecodiff|ecoplusdiff|hyston|hystoff)$/,'ww','Temperaturdifferenz für die Beladung bestimmt, wie oft Warmwasser geladen wird.'],
  ['bosch',/boiler_dhw_(seltemp|seltemplow|comfstop|ecostop)$|thermostat_dhw_settemp$|boiler_dhw_altopprio(heat)?$/,'ww','Warmwasser-Temperaturen und Wechselbetrieb (Maximaldauer WW/Heizung) prüfen.'],
  ['bosch',/boiler_(aux\w*|maxheat\w*|tempparmode|emergency\w*)$/,'aux','Zuheizer-Freigabe, Verzögerung und Parallelbetrieb so wählen, dass er nur bei Kälte hilft.'],
  ['bosch',/boiler_(hppumpmode|hpsetdiffpress|tempdiffheat)$/,'hyd','Pumpenbetrieb/Temperaturdifferenz bestimmen Durchfluss und Spreizung (Ziel 3–7 K).'],
  ['bsb',/hc\d_(heatingcurve|seltemp)$/,'takt','Heizkennlinie bzw. Komfortsollwert senken – weniger Vorlauf, längere Brennerläufe.'],
  ['bsb',/dhw_(settemp|mode)$/,'ww','Trinkwasser-Nennsollwert und Betrieb bestimmen Ladehäufigkeit und -dauer.'],
];
function waFindCat(t){ return /Laufzeit je Start|Brennerlaufzeit|Starts pro Tag|Brennerstarts pro Tag|Starts in(nerhalb)? einer Stunde|kürzer als 10 min|Vorlauf über Soll|taktet|kleiner Leistung|über Soll/i.test(t)?'takt'
  : /Trinkwasser|Warmwasser/i.test(t)?'ww' : /Zuheizer|Backup/i.test(t)?'aux' : /Spreizung|Durchfluss/i.test(t)?'hyd' : /Abtau/i.test(t)?'def' : /Arbeitszahl/i.test(t)?'eff' : ''; }
function waSetHints(r,list){
  const worst={}; r.find.forEach(f=>{ const c=waFindCat(f.t); if(c && f.lv>=1 && (!worst[c]||f.lv>worst[c].lv)) worst[c]=f; });
  list.forEach(x=>{ const id=x.id+' '+x.r, v=parseFloat(x.st), H=[];
    WA_RULES.forEach(([vd,re,c,t])=>{ if(vd===WA_V && re.test(x.id) && worst[c]) H.push({lv:worst[c].lv,t:t+' (Auffällig: '+worst[c].t+')'}); });
    if(!isNaN(v)){
      if(WA_V==='samsung'){
        if(/infsv1031$/.test(id) && v>55) H.push({lv:1,t:'Max. Vorlauf über 55 °C – für die WP möglichst ≤ 45–50 °C.'});
        if(/infsv2021$/.test(id) && v>50) H.push({lv:1,t:'Vorlauf bei Auslegung über 50 °C – bei Fußbodenheizung meist 30–38 °C ausreichend.'});
        if(/infsv3021$|indoordhwsettemp$/.test(id) && v>55) H.push({lv:1,t:'Warmwasser über 55 °C mit der WP kostet viel Effizienz – Legionellenschutz über die Desinfektion.'}); }
      if(WA_V==='bosch'){
        if(/hc\d_designtemp$/.test(id) && v>50) H.push({lv:1,t:'Auslegungstemperatur über 50 °C – bei Fußbodenheizung meist 30–38 °C ausreichend.'});
        if(/hc\d_maxflowtemp$/.test(id) && v>55) H.push({lv:1,t:'Max. Vorlauf über 55 °C – für die WP möglichst ≤ 45–50 °C.'});
        if(/thermostat_dhw_settemp$|boiler_dhw_seltemp$/.test(id) && v>55) H.push({lv:1,t:'Warmwasser über 55 °C mit der WP kostet viel Effizienz – Legionellenschutz über die Desinfektion.'}); }
      if(WA_V==='bsb'){
        if(/dhw_settemp$/.test(id) && v>60) H.push({lv:1,t:'Trinkwasser-Nennsollwert über 60 °C: mehr Verluste und Verkalkung.'});
        if(/heatingcurve$/.test(id) && v>1.6) H.push({lv:1,t:'Steile Heizkennlinie – Fußbodenheizung meist 0,4–0,8, Heizkörper 1,0–1,4.'}); }
    }
    H.sort((a,b)=>b.lv-a.lv); x.hint=H[0]||null; x.hints=H; });
  return list; }

// ---- Änderungen der Einstellwerte im Zeitraum (HA-Verlauf) ----
async function waSetHistory(list,t0,t1){
  const ids=list.map(x=>x.r), out=[]; if(!ids.length) return out;
  for(let i=0;i<ids.length;i+=60){ const part=ids.slice(i,i+60);
    const r=await ws({type:'history/history_during_period',start_time:new Date(t0).toISOString(),end_time:new Date(t1).toISOString(),entity_ids:part,minimal_response:true,no_attributes:true,significant_changes_only:false});
    part.forEach(id=>{ const arr=(r&&r[id])||[]; let prev=null; arr.forEach((p,j)=>{ const s=p.s!=null?p.s:p.state, t=p.lu!=null?p.lu*1000:Date.parse(p.last_changed||p.last_updated);
      if(s==null||s==='unavailable'||s==='unknown') return; if(prev!=null && s!==prev && j>0) out.push({t,r:id,from:prev,to:s}); prev=s; }); }); }
  return out.sort((a,b)=>b.t-a.t); }
function waSetDemoChanges(list,t0,t1){ const pick=list.filter(x=>x.k==='n' && !isNaN(parseFloat(x.st))).slice(0,40), out=[], d=(t1-t0);
  [[3,0.55,-1],[11,0.3,1],[17,0.82,-2]].forEach(([i,f,dv])=>{ const x=pick[i]; if(!x) return; const v=parseFloat(x.st), st=+(x.a.step)||1; out.push({t:t0+d*f,r:x.r,from:String(v-dv*st),to:String(v)}); });
  return out.sort((a,b)=>b.t-a.t); }
// ---- Demodaten: Anlage mit leichter Überdimensionierung (taktet in der Übergangszeit) ----
function wprDemo(per){
  const P=WPR_PER.find(p=>p.k===per), stp=P.step*1000, t1=Math.floor(Date.now()/stp)*stp, t0=t1-P.d*864e5;
  const ser={}; WPR_SIG.forEach(s=>ser[s.k]=[]);
  let rnd=12345; const rn=()=>{ rnd=(rnd*16807)%2147483647; return rnd/2147483647; };
  let vl=30, on=false, mode='heat', runMin=0, sinceDef=0, defLeft=0, dhwLeft=0, offMin=99, lastErr='0', errLeft=0;
  const sat=T=>7*Math.exp(0.0265*T), push_=(k,t,v)=>{ const a_=ser[k]; if(!a_) return; if(!a_.length || a_[a_.length-1].v!==v) a_.push({t,v}); };
  for(let t=t0; t<=t1; t+=stp){
    const d=new Date(t), h=d.getHours()+d.getMinutes()/60, day=Math.floor((t-t0)/864e5), dt=stp/60000;
    const ta=7.5+4*Math.sin((h-9)/24*2*Math.PI)+3.5*Math.sin(day*1.3+P.d)+ (rn()-.5)*0.4;
    const vls=Math.max(25, 34-0.6*(ta-0)), dem=Math.max(0,(16-ta)*0.42);   // kW Heizlast
    if(dhwLeft<=0 && Math.abs(h-13)<dt/120 ) { dhwLeft=48; }
    if(day===P.d-3 && Math.abs(h-4.2)<dt/120) errLeft=25;
    let hz=0;
    if(errLeft>0){ errLeft-=dt; on=false; mode='heat'; }
    else if(dhwLeft>0){ if(!on){ on=true; runMin=0; } mode='dhw'; dhwLeft-=dt; hz=68; vl=Math.min(52,vl+1.4*dt); if(dhwLeft<=0){ on=false; mode='heat'; offMin=0; vl=36; } }
    else if(defLeft>0){ mode='def'; defLeft-=dt; hz=58; vl-=0.9*dt; if(defLeft<=0){ mode='heat'; sinceDef=0; } }
    else {
      mode='heat'; const qmin=3.6;   // kleinste Heizleistung der WP
      if(on){ const q=Math.max(qmin,Math.min(9,dem*1.05)); hz=Math.round(30+(q-qmin)/(9-qmin)*45); if(dem>=qmin) vl+=(vls+0.5-vl)*Math.min(1,0.15*dt); else vl+=(q-dem)*0.25*dt; vl=Math.min(vl,vls+4);
        if(vl>=vls+3.2 && dem<qmin){ on=false; offMin=0; }
        sinceDef+=dt; if(ta<6 && sinceDef>(ta<2?45:70)+rn()*20){ defLeft=5+rn()*3; } }
      else { vl-= Math.max(0.15,dem*0.12)*dt; vl=Math.max(vl,ta+8); offMin+=dt; if(dem>0.4 && vl<=vls-2 && offMin>=10){ on=true; runMin=0; } }
    }
    if(on) runMin+=dt;
    const flowOn = on || dem>0.4, flow = flowOn ? 17.5+0.4*Math.sin(t/7e6) : 0, spread = on ? (mode==='dhw'?6.5:(hz-20)*0.11+2.4) : 0.3;
    const rl = vl-spread, tph = on ? vl+2+hz*0.03 : vl, tpl = on ? ta-4-hz*0.08 : ta;
    const shv = on ? (runMin<4 ? 1.2+runMin*0.8 : 5.2+(rn()-.5)*1.4) : 0;
    const pel = on ? 0.25+hz*0.026 : 0.012, cop = on ? Math.max(1.6, 6.2-(vl-ta)*0.075) : 0, pth = mode==='def'? -1.5 : on ? pel*cop : 0;
    const errNow = errLeft>0 ? (true?'E911':'A11-3061') : '0';
    push_('comp',t,on?1:0); push_('hz',t,hz); push_('def',t,mode==='def'?1:0);
    push_('act',t,!on?(true?'OP_STOP':'off'):mode==='dhw'?(true?'OP_NORMAL':'hot water'):mode==='def'?(true?'OP_DEFROST':'defrost'):(true?'OP_NORMAL':'heating'));
    push_('dhw',t,mode==='dhw'?(true?'DHW':1):(true?'ROOM':0));
    const r1=x=>Math.round(x*10)/10;
    push_('ta',t,r1(ta)); push_('vl',t,r1(vl)); push_('rl',t,r1(rl)); push_('vls',t,r1(vls)); push_('ww',t,r1(mode==='dhw'?42+(48-dhwLeft)*0.25:Math.max(38,54-((h+11)%24)*0.5)));
    push_('flow',t,r1(flow)); push_('pump',t,flowOn?62:0); push_('pel',t,Math.round(pel*100)/100); push_('pth',t,r1(pth));
    push_('tdis',t,r1(on?tph+24+hz*0.22+(rn()-.5):Math.max(ta,vl-10))); push_('tsuc',t,r1(tpl+shv)); push_('sh',t,r1(shv));
    push_('php',t,r1(sat(tph))); push_('plp',t,r1(sat(tpl))); push_('tph',t,r1(tph)); push_('tpl',t,r1(tpl)); push_('tliq',t,r1(on?rl+2:vl));
    push_('tevi',t,r1(on?tpl+1.2:ta)); push_('eev',t,on?Math.round(true?200+hz*3:20+hz*0.4):0); push_('fan',t,on?Math.round(true?300+hz*6:30+hz*0.7):0);
    push_('bu',t,true?'OFF':0); push_('err',t,errNow);
  }
  return {t0,t1,step:stp,ser,demo:true};
}

// ---- Verlauf aus HA holen ----
function wprNum(s,k){ if(s==null) return null; const x=String(s);
  if(/^(on|true|ein|an)$/i.test(x)) return 1; if(/^(off|false|aus)$/i.test(x)) return 0; const v=parseFloat(x); return isNaN(v)?null:v; }
function wprScale(sig){ const r=waR(sig.id), u=String((((H()&&H().states||{})[r])||{}).attributes&&H().states[r].attributes.unit_of_measurement||'');
  if(sig.k==='pel'||sig.k==='pth') return /^W$/.test(u)?0.001:1;
  if(sig.k==='flow') return /l\/h/i.test(u)?1/60:/m³\/h|m3\/h/i.test(u)?1000/60:1;
  return 1; }
async function wprFetch(per){
  const P=WPR_PER.find(p=>p.k===per), stp=P.step*1000, t1=Date.now(), t0=t1-P.d*864e5;
  const sigs=WPR_SIG.filter(s=>waHas(s.id)), ser={};
  WPR_SIG.forEach(s=>ser[s.k]=[]);
  const ids=sigs.map(s=>waR(s.id));
  // in Tagesblöcken laden (große Antworten vermeiden)
  const chunk = P.d>7 ? 7*864e5 : P.d*864e5;
  for(let a0=t0; a0<t1; a0+=chunk){
    const r = await ws({type:'history/history_during_period', start_time:new Date(a0).toISOString(), end_time:new Date(Math.min(t1,a0+chunk)).toISOString(),
      entity_ids:ids, minimal_response:true, no_attributes:true, significant_changes_only:false});
    sigs.forEach(s=>{ const arr=(r&&r[waR(s.id)])||[], f=wprScale(s);
      arr.forEach(x=>{ const ts = x.lu!=null ? x.lu*1000 : (x.last_updated_ts!=null ? x.last_updated_ts*1000 : Date.parse(x.last_changed||x.last_updated));
        const raw = x.s!=null?x.s:x.state; if(isNaN(ts)||raw==null||raw==='unavailable'||raw==='unknown') return;
        let v = s.u==='txt' ? String(raw) : wprNum(raw); if(v==null) return; if(s.u!=='txt'&&s.u!=='bin') v*=f;
        ser[s.k].push({t:ts,v}); }); });
  }
  WPR_SIG.forEach(s=>ser[s.k].sort((x,y)=>x.t-y.t));
  return {t0,t1,step:stp,ser,demo:false,found:sigs.map(s=>s.k)};
}
// ---- Auswertung ----
const WPR_MODE = v => { const x=String(v==null?'':v); return /defrost|abtau/i.test(x)?'def':/hot ?water|dhw|warmw|trinkw|tap/i.test(x)?'dhw':/cool|kühl/i.test(x)?'cool':'heat'; };
function wprAnalyze(raw){
  const {t0,t1,step,ser}=raw, n=Math.floor((t1-t0)/step)+1, tg=i=>t0+i*step, G={};
  // auf festes Zeitraster bringen (letzter Wert gilt bis zum nächsten)
  WPR_SIG.forEach(s=>{ const a_=ser[s.k]||[], g=new Array(n).fill(null); let j=0, cur=null;
    for(let i=0;i<n;i++){ const t=tg(i); while(j<a_.length && a_[j].t<=t){ cur=a_[j].v; j++; } g[i]=cur; } G[s.k]=g; });
  ['comp','hz','act','dhw','def','ta','vl','rl','vls','ww','flow','pump','pel','pth','tdis','tsuc','sh','php','plp','tph','tpl','tliq','tevi','eev','fan','bu','err'].forEach(k=>{ if(!G[k]) G[k]=new Array(n).fill(null); });
  const has_=k=>(ser[k]||[]).length>0;
  // Verdichter-Läufe aus den Rohwechseln (genau) – sonst aus Frequenz > 0
  let src = has_('comp') ? ser.comp.map(p=>({t:p.t,v:p.v>0.5?1:0})) : (ser.hz||[]).map(p=>({t:p.t,v:p.v>0?1:0}));
  const runs=[]; let st_=null, prev=0;
  src.forEach(p=>{ if(p.t>t1) return; if(p.v && !prev){ st_=Math.max(p.t,t0); } else if(!p.v && prev && st_!=null){ runs.push({s:st_,e:p.t}); st_=null; } prev=p.v; });
  if(prev && st_!=null) runs.push({s:st_,e:t1,open:true});
  const idx=t=>Math.max(0,Math.min(n-1,Math.round((t-t0)/step)));
  const mean=(k,i0,i1,f)=>{ const g=G[k]; let s=0,c=0; for(let i=i0;i<=i1;i++){ const v=g[i]; if(v==null||typeof v!=='number') continue; if(f&&!f(i)) continue; s+=v; c++; } return c?s/c:null; };
  const mx=(k,i0,i1)=>{ const g=G[k]; let m=null; for(let i=i0;i<=i1;i++){ const v=g[i]; if(typeof v==='number' && (m==null||v>m)) m=v; } return m; };
  // Modus je Lauf, Abschaltgrund
  runs.forEach(r=>{ r.d=(r.e-r.s)/60000; const i0=idx(r.s), i1=Math.max(i0,idx(r.e)-1); const cnt={heat:0,dhw:0,def:0,cool:0};
    for(let i=i0;i<=i1;i++){ let m=WPR_MODE(G.act[i]); if(m==='heat' && G.dhw[i]!=null && /^(1|dhw|on|tank)$/i.test(String(G.dhw[i]))) m='dhw'; if(G.def[i]===1) m='def'; cnt[m]++; }
    const c2={...cnt}; delete c2.def; r.m=Object.keys(c2).reduce((x,y)=>c2[x]>=c2[y]?x:y); r.def=cnt.def;
    r.ta=mean('ta',i0,i1); r.hz=mean('hz',i0,i1,i=>G.hz[i]>0); r.vlmax=mx('vl',i0,i1);
    const ie=Math.min(n-1,idx(r.e)); const ov = G.vl[ie-1]!=null && G.vls[ie-1]!=null ? G.vl[ie-1]-G.vls[ie-1] : null; r.ov=ov;
    let err=false; for(let i=i0;i<=Math.min(n-1,ie+1);i++){ const e=G.err[i]; if(e!=null && !/^(0|-|none|kein.*|-\s*\(0\)|.*\(0\)|ok)?$/i.test(String(e).trim())) err=true; }
    r.why = r.open?'läuft noch' : err?'Störung' : r.m==='dhw'?'Warmwasser fertig' : ov!=null && ov>=1.5 ? 'Vorlauf über Soll' : 'Regelung / Wärmebedarf gedeckt';
  });
  const days=(t1-t0)/864e5, hrun=runs.reduce((s,r)=>s+r.d,0)/60, closed=runs.filter(r=>!r.open), heat=closed.filter(r=>r.m==='heat');
  const sorted=closed.map(r=>r.d).sort((x,y)=>x-y), med=sorted.length?sorted[Math.floor(sorted.length/2)]:null;
  const short=closed.filter(r=>r.d<10).length, shortH=heat.filter(r=>r.d<10).length;
  const pauses=[]; for(let i=1;i<runs.length;i++) pauses.push((runs[i].s-runs[i-1].e)/60000);
  const shortP=pauses.filter(p=>p<10).length;
  // Starts je Stunde (max) und je Tag
  const perHour={}, perDay={}; runs.forEach(r=>{ const h=Math.floor(r.s/36e5); perHour[h]=(perHour[h]||0)+1; const dk=new Date(r.s).toLocaleDateString('de-DE'); (perDay[dk]=perDay[dk]||{n:0,h:0,def:0}).n++; });
  runs.forEach(r=>{ const dk=new Date(r.s).toLocaleDateString('de-DE'); perDay[dk].h+=r.d/60; });
  // Abtauungen
  const defs=[]; { let s0=null,p=0; (ser.def||[]).forEach(x=>{ if(x.t>t1) return; const v=x.v>0.5?1:0; if(v&&!p) s0=Math.max(x.t,t0); else if(!v&&p&&s0!=null){ defs.push({s:s0,e:x.t,d:(x.t-s0)/60000}); s0=null; } p=v; }); }
  if(!has_('def')){ let s0=null,p=0; for(let i=0;i<n;i++){ const v=WPR_MODE(G.act[i])==='def'?1:0; if(v&&!p) s0=tg(i); else if(!v&&p){ defs.push({s:s0,e:tg(i),d:(tg(i)-s0)/60000}); } p=v; } }
  defs.forEach(d_=>{ d_.ta=mean('ta',idx(d_.s),idx(d_.e)); const dk=new Date(d_.s).toLocaleDateString('de-DE'); if(perDay[dk]) perDay[dk].def++; });
  // Messwerte im eingeschwungenen Betrieb (Verdichter > 5 min an, kein Abtauen), getrennt Heizen/WW
  const runAt=new Uint8Array(n), modeAt=new Array(n).fill('');
  runs.forEach(r=>{ const i0=idx(r.s), i1=idx(r.e)-1; for(let i=i0;i<=i1;i++){ runAt[i]= (tg(i)-r.s)>=5*60000 ? 2 : 1; modeAt[i]=r.m; } });
  const steady=i=>runAt[i]===2 && G.def[i]!==1 && WPR_MODE(G.act[i])!=='def';
  const stat=(k,f)=>{ const g=G[k]; let s=0,c=0,mi=null,ma=null; for(let i=0;i<n;i++){ const v=g[i]; if(typeof v!=='number'||(f&&!f(i))) continue; s+=v;c++; if(mi==null||v<mi)mi=v; if(ma==null||v>ma)ma=v; } return c?{m:s/c,min:mi,max:ma,c}:null; };
  const sH=i=>steady(i)&&modeAt[i]==='heat', sW=i=>steady(i)&&modeAt[i]==='dhw';
  const val={}; WPR_SIG.forEach(s=>{ if(s.u==='txt'||s.u==='bin') return; val[s.k]={all:stat(s.k), heat:stat(s.k,sH), dhw:stat(s.k,sW)}; });
  // abgeleitete Werte je Rasterpunkt
  const der=(f,flt)=>{ let s=0,c=0,mi=null,ma=null; for(let i=0;i<n;i++){ if(!flt(i)) continue; const v=f(i); if(v==null||isNaN(v)) continue; s+=v;c++; if(mi==null||v<mi)mi=v; if(ma==null||v>ma)ma=v; } return c?{m:s/c,min:mi,max:ma}:null; };
  const num=(k,i)=>typeof G[k][i]==='number'?G[k][i]:null;
  val.spread={heat:der(i=>num('vl',i)!=null&&num('rl',i)!=null?G.vl[i]-G.rl[i]:null,sH), dhw:der(i=>num('vl',i)!=null&&num('rl',i)!=null?G.vl[i]-G.rl[i]:null,sW)};
  if(!has_('sh')) val.sh={heat:der(i=>num('tsuc',i)!=null&&num('tpl',i)!=null?G.tsuc[i]-G.tpl[i]:null,sH)};
  val.subc={heat:der(i=>num('tph',i)!=null&&num('tliq',i)!=null?G.tph[i]-G.tliq[i]:null,sH)};
  val.capp={heat:der(i=>num('tph',i)!=null&&num('vl',i)!=null?G.tph[i]-G.vl[i]:null,sH)};      // Kondensator: HD-Sättigung − Vorlauf
  val.eapp={heat:der(i=>num('ta',i)!=null&&num('tpl',i)!=null?G.ta[i]-G.tpl[i]:null,sH)};      // Verdampfer: Außenluft − ND-Sättigung
  // Energie (Leistung × Zeit)
  let eel=0, eth=0, eelH=0, ethH=0, eelW=0, ethW=0; const hstep=step/36e5;
  for(let i=0;i<n;i++){ const p=num('pel',i), q=num('pth',i); if(p!=null){ eel+=p*hstep; if(modeAt[i]==='heat')eelH+=p*hstep; if(modeAt[i]==='dhw')eelW+=p*hstep; }
    if(q!=null){ eth+=q*hstep; if(modeAt[i]==='heat')ethH+=q*hstep; if(modeAt[i]==='dhw')ethW+=q*hstep; } }
  // Zuheizer / Backup
  let buMin=0; for(let i=0;i<n;i++){ const b=G.bu[i]; if(b!=null && !/^(0|off|aus|false)?$/i.test(String(b).trim())) buMin+=step/60000; }
  // Fehlercodes
  const errs=[]; { let last=null; (ser.err||[]).forEach(x=>{ const e=String(x.v).trim(); const ok=/^(0|-|none|kein.*|-\s*\(0\)|.*\(0\)|ok)?$/i.test(e); if(!ok && e!==last) errs.push({t:x.t,c:e}); last=ok?null:e; }); }
  // Taktung nach Außentemperatur
  const bins=[[-99,-5,'unter −5 °C'],[-5,0,'−5 … 0 °C'],[0,5,'0 … 5 °C'],[5,10,'5 … 10 °C'],[10,15,'10 … 15 °C'],[15,99,'über 15 °C']];
  const taB=bins.map(b=>{ let hrs=0; for(let i=0;i<n;i++){ const v=num('ta',i); if(v!=null&&v>=b[0]&&v<b[1]) hrs+=step/36e5; }
    let rh=0; for(let i=0;i<n;i++){ const v=num('ta',i); if(v!=null&&v>=b[0]&&v<b[1]&&runAt[i]) rh+=step/36e5; }
    const rr=heat.filter(r=>r.ta!=null&&r.ta>=b[0]&&r.ta<b[1]); const run=rr.reduce((s,r)=>s+r.d,0);
    return {l:b[2],hrs,n:rr.length,per_h:hrs>0?rr.length/hrs:null,avg:rr.length?run/rr.length:null,share:hrs>0?rh/hrs:null}; }).filter(b=>b.hrs>0.2);
  const whys={}; closed.forEach(r=>whys[r.why]=(whys[r.why]||0)+1);
  const K={days,starts:runs.length,perDay:runs.length/days,hrun,hrunDay:hrun/days,avg:closed.length?closed.reduce((s,r)=>s+r.d,0)/closed.length:null,med,
    min:sorted.length?sorted[0]:null,short,shortH,shortShare:closed.length?short/closed.length:0,heatN:heat.length,dhwN:closed.filter(r=>r.m==='dhw').length,
    avgHeat:heat.length?heat.reduce((s,r)=>s+r.d,0)/heat.length:null,medHeat:(()=>{ const x=heat.map(r=>r.d).sort((p,q)=>p-q); return x.length?x[Math.floor(x.length/2)]:null; })(),pauseAvg:pauses.length?pauses.reduce((s,p)=>s+p,0)/pauses.length:null,shortP,
    maxPerHour:Object.values(perHour).reduce((m,x)=>Math.max(m,x),0),defN:defs.length,defDay:defs.length/days,defAvg:defs.length?defs.reduce((s,x)=>s+x.d,0)/defs.length:null,
    defWarm:defs.filter(x=>x.ta!=null&&x.ta>7).length,eel,eth,az:eel>0.05?eth/eel:null,azH:eelH>0.05?ethH/eelH:null,azW:eelW>0.05?ethW/eelW:null,eelH,ethH,eelW,ethW,buMin,
    taMean:(val.ta&&val.ta.all||{}).m,duty:hrun/(days*24)};
  const res={raw,G,n,tg,runs,defs,errs,val,K,perDay,taB,whys,found:raw.found||WPR_SIG.map(s=>s.k)};
  res.find=WA_BOIL?wprFindingsBoiler(res):wprFindings(res);
  return res;
}

// ---- Bewertung: rot / gelb / grün mit Hinweis zur Fehlersuche bzw. Optimierung ----
function wprFindings(r){
  const K=r.K, V=r.val, f=[], add=(lv,t,h)=>f.push({lv,t,h}), g=(k,m,x)=>((V[k]||{})[m]||{})[x];
  // Taktung
  if(K.starts>=3){
    if(K.medHeat!=null && K.medHeat<10) add(2,`Heizbetrieb taktet stark: typische Laufzeit (Median) ${waN(K.medHeat,0)} min je Start`,'Mindestlaufzeit ≥ 20–30 min anstreben. Prüfen: Heizkurve zu hoch, Hysterese/Schaltdifferenz zu klein, kleinste Leistung der WP größer als Heizlast, zu wenig Wasservolumen (Pufferspeicher / Überströmventil), Einzelraumregler schließen zu viele Kreise.');
    else if(K.medHeat!=null && K.medHeat<25) add(1,`Laufzeit je Start kurz: typisch ${waN(K.medHeat,0)} min im Heizbetrieb (Median)`,'Optimierung: Heizkurve/Fußpunkt etwas senken, Hysterese bzw. Einschaltverzögerung erhöhen, Raumeinfluss reduzieren, alle Heizkreise offen lassen.');
    else add(0,`Laufzeit je Start gut: typisch ${waN(K.medHeat,0)} min (Median)`,'');
    if(K.perDay>24) add(2,`${waN(K.perDay,1)} Starts pro Tag`,'Mehr als 24 Starts/Tag verkürzen die Lebensdauer des Verdichters deutlich (Herstellerempfehlung meist ≤ 3 Starts/h und ≤ 20–24/Tag).');
    else if(K.perDay>12) add(1,`${waN(K.perDay,1)} Starts pro Tag`,'Ziel: unter 10–12 Starts pro Tag in der Heizperiode.');
    if(K.maxPerHour>3) add(1,`Bis zu ${K.maxPerHour} Starts innerhalb einer Stunde`,'Hinweis auf Kurztakten (z. B. Vorlauf erreicht Soll sofort). Durchfluss, Pumpenstufe und Mindest-Stillstandszeit prüfen.');
    if(K.shortShare>0.3) add(2,`${waN(K.shortShare*100,0)} % aller Läufe kürzer als 10 min`,'Kurzläufe bringen kaum Wärme, aber viel Verschleiß und schlechte Effizienz (Anlaufverluste, Öl-Rückführung).');
    else if(K.shortShare>0.15) add(1,`${waN(K.shortShare*100,0)} % aller Läufe kürzer als 10 min`,'');
    const ovN=r.runs.filter(x=>x.why==='Vorlauf über Soll').length;
    if(ovN>=3 && ovN/Math.max(1,K.heatN)>0.4) add(1,`${ovN} Heizläufe enden mit Vorlauf über Soll`,'Die WP kann ihre Leistung nicht weit genug absenken → Wärmeabnahme zu gering. Heizkurve senken, Volumenstrom erhöhen, Puffer/Überströmer prüfen.');
  } else add(0,'Kaum Verdichterstarts im Zeitraum','Für eine Taktungs-Bewertung längeren Zeitraum wählen.');
  // Abtauen
  if(K.defN){ if(K.defAvg>10) add(1,`Abtauungen dauern lange: Ø ${waN(K.defAvg,1)} min`,'Verdampfer auf Verschmutzung/Vereisung, Kondensatablauf und Wannenheizung prüfen; Durchfluss während Abtauen ausreichend?');
    if(K.defWarm>2) add(1,`${K.defWarm} Abtauungen bei über 7 °C Außentemperatur`,'Ungewöhnlich bei milder Luft – Verdampfer verschmutzt, Lüfter, Fühler TL/Verdampfereintritt oder Kältemittelfüllung prüfen.');
    if(K.defDay>24) add(1,`${waN(K.defDay,1)} Abtauungen pro Tag`,'Viele Abtauungen kosten Effizienz. Bei Temperaturen um 0 °C und hoher Luftfeuchte normal.'); }
  // Kältekreis
  const td=g('tdis','all','max'); if(td!=null){ if(td>110) add(2,`Heißgas bis ${waN(td,0)} °C`,'Sehr hoch: Kältemittelmangel, zu hohe Überhitzung, verschmutzter Verflüssiger oder zu hohe Vorlauftemperatur. Kältetechniker hinzuziehen.');
    else if(td>100) add(1,`Heißgas bis ${waN(td,0)} °C`,'Grenzbereich – Verlauf im Bericht prüfen (nur bei WW-Bereitung oder auch im Heizbetrieb?).'); else add(0,`Heißgas max. ${waN(td,0)} °C`,''); }
  const sh=g('sh','heat','m'); if(sh!=null){ if(sh<2) add(2,`Sauggas-Überhitzung im Heizbetrieb nur Ø ${waN(sh,1)} K`,'Gefahr von Flüssigkeitsschlägen: Expansionsventil, Fühler Sauggas/ND und Füllmenge (Überfüllung) prüfen.');
    else if(sh>12) add(1,`Sauggas-Überhitzung hoch: Ø ${waN(sh,1)} K`,'Hinweis auf Kältemittelmangel oder Expansionsventil öffnet zu wenig. Mit Heißgas und ND vergleichen.'); else add(0,`Sauggas-Überhitzung Ø ${waN(sh,1)} K (normal 3–10 K)`,''); }
  const ph=g('php','all','max'); if(ph!=null && true){ if(ph>40) add(2,`Hochdruck bis ${waN(ph,1)} bar`,'Nahe der Hochdruckabschaltung (R410A ca. 41,5 bar): Durchfluss, Luft im System, Filter/Schmutzfänger prüfen.'); else if(ph>36) add(1,`Hochdruck bis ${waN(ph,1)} bar`,'Hohe Vorlauftemperaturen vermeiden, Durchfluss prüfen.'); }
  const ca=g('capp','heat','m'); if(ca!=null && ca>6) add(1,`Kondensator: HD-Sättigung Ø ${waN(ca,1)} K über Vorlauf`,'Wärmeübergang schlecht – Durchfluss zu gering, Plattenwärmetauscher verschmutzt/verkalkt oder Luft im Heizwasser.');
  const ea=g('eapp','heat','m'); if(ea!=null && ea>12) add(1,`Verdampfer: ND-Sättigung Ø ${waN(ea,1)} K unter Außenluft`,'Verdampfer verschmutzt/vereist, Lüfterleistung zu gering oder Kältemittelmangel.');
  // Hydraulik
  const sp=g('spread','heat','m'); if(sp!=null){ if(sp<2.5) add(1,`Spreizung im Heizbetrieb nur Ø ${waN(sp,1)} K`,'Volumenstrom sehr hoch – Pumpe kann reduziert werden (spart Strom), bei Takten aber zuerst Heizkurve prüfen.');
    else if(sp>8) add(2,`Spreizung im Heizbetrieb Ø ${waN(sp,1)} K`,'Volumenstrom zu gering: Schmutzfänger, Pumpe, Luft, geschlossene Ventile prüfen – erhöht Vorlauf und senkt Effizienz.');
    else add(0,`Spreizung Heizbetrieb Ø ${waN(sp,1)} K (Ziel 3–7 K)`,''); }
  // Effizienz
  if(r.K.azH!=null){ if(r.K.azH<2.5) add(2,`Arbeitszahl Heizen nur ${waN(r.K.azH,2)}`,'Vorlauftemperatur, Taktung, Abtauungen und Zuheizer prüfen.'); else if(r.K.azH<3.2) add(1,`Arbeitszahl Heizen ${waN(r.K.azH,2)}`,'Potenzial: niedrigere Heizkurve, längere Laufzeiten, weniger Abtauungen.'); else add(0,`Arbeitszahl Heizen ${waN(r.K.azH,2)}`,''); }
  if(K.buMin>0) add(1,`Zuheizer/Backup ${waN(K.buMin/60,1)} h aktiv`,'Bivalenzpunkt und Freigabe des Zuheizers prüfen – kostet viel Strom.');
  // Störungen
  if(r.errs.length) add(2,`${r.errs.length} Störungsmeldung(en): ${[...new Set(r.errs.map(e=>e.c))].join(', ')}`,'Zeitpunkte und Werte davor im Bericht ansehen; Bedeutung laut Servicehandbuch.');
  else add(0,'Keine Störungen im Zeitraum','');
  return f.sort((a_,b_)=>b_.lv-a_.lv);
}

function wprMin(m){ if(m==null) return '–'; return m<90 ? waN(m,0)+' min' : waN(m/60,1)+' h'; }
function wprV(o,k,dec,u){ return o&&o[k]!=null ? waN(o[k],dec)+(u?' '+u:'') : '–'; }
// ---- CSV (Semikolon, deutsches Dezimalkomma – öffnet direkt in Excel) ----
function wprCsv(r){
  const sigs=WPR_SIG.filter(s=>(r.raw.ser[s.k]||[]).length), d=v=>v==null?'':typeof v==='number'?String(Math.round(v*100)/100).replace('.',','):String(v).replace(/;/g,',');
  const L=['Zeit;'+sigs.map(s=>s.l+(s.u&&s.u!=='txt'&&s.u!=='bin'?' ['+s.u+']':'')).join(';')];
  for(let i=0;i<r.n;i++) L.push(new Date(r.tg(i)).toLocaleString('de-DE')+';'+sigs.map(s=>d(r.G[s.k][i])).join(';'));
  L.push(''); L.push('Verdichterläufe'); L.push('Start;Ende;Dauer [min];Betrieb;Außen Ø [°C];Verdichter Ø;Vorlauf max [°C];Abschaltgrund');
  r.runs.forEach(x=>L.push([new Date(x.s).toLocaleString('de-DE'),new Date(x.e).toLocaleString('de-DE'),d(x.d),({heat:'Heizen',dhw:'Warmwasser',cool:'Kühlen',def:'Abtauen'})[x.m],d(x.ta),d(x.hz),d(x.vlmax),x.why].join(';')));
  if(r.sets && r.sets.length){ L.push(''); L.push('Einstellwerte'); L.push('Gruppe;Einstellung;Wert;Einheit;Min;Max;Entität;Hinweis');
    r.sets.forEach(x=>L.push([x.g+(x.sec?' · '+x.sec:''),x.l,d(isNaN(parseFloat(x.st))?x.st:parseFloat(x.st)),x.a.unit_of_measurement||'',x.a.min!=null?d(+x.a.min):'',x.a.max!=null?d(+x.a.max):'',x.r,x.hint?x.hint.t:''].map(s=>String(s).replace(/;/g,',')).join(';'))); }
  if(r.setChg && r.setChg.length){ L.push(''); L.push('Änderungen der Einstellwerte'); L.push('Zeit;Entität;vorher;nachher');
    r.setChg.forEach(c=>L.push([new Date(c.t).toLocaleString('de-DE'),c.r,c.from,c.to].join(';'))); }
  return L.join('\r\n');
}


// ---- Diagramme: eine Zeichenroutine für Bildschirm (SVG) und PDF ----
const WA_DARK={grid:'#262a30',frame:'#3a3f46',txt:'#9aa1ab',run:'rgba(58,149,230,.16)',dhw:'rgba(242,182,50,.22)',def:'rgba(120,220,240,.25)'};
const WA_LIGHT={grid:'#e3e6ea',frame:'#c8ccd2',txt:'#666666',run:'#dcebfa',dhw:'#fde7c8',def:'#c9f0f7'};
function waChartLine(g,r,lines,o){
  const W=o.w,H=o.h,pl=o.pl||34,pr=o.r2?34:8,pt=6,pb=16,t0=r.raw.t0,t1=r.raw.t1,th=o.th,fs=o.fs||9,X=t=>pl+(t-t0)/(t1-t0)*(W-pl-pr);
  const rng=ks=>{ let mi=Infinity,ma=-Infinity; ks.forEach(k=>r.G[k].forEach(v=>{ if(typeof v==='number'){ if(v<mi)mi=v; if(v>ma)ma=v; } })); if(mi===Infinity){mi=0;ma=1;} if(ma-mi<1){ma+=.5;mi-=.5;} const p=(ma-mi)*0.08; return [mi-p,ma+p]; };
  const L1=lines.filter(l=>!l.r2),L2=lines.filter(l=>l.r2),y1=rng(L1.map(l=>l.k)),y2=L2.length?rng(L2.map(l=>l.k)):null,Y=(v,y)=>pt+(1-(v-y[0])/(y[1]-y[0]))*(H-pt-pb);
  if(o.shade!==false){ r.runs.forEach(x=>g.rect(X(x.s),pt,Math.max(0.5,X(x.e)-X(x.s)),H-pt-pb,x.m==='dhw'?th.dhw:th.run)); r.defs.forEach(x=>g.rect(X(x.s),pt,Math.max(0.8,X(x.e)-X(x.s)),H-pt-pb,th.def)); }
  for(let k=0;k<=4;k++){ const v=y1[0]+(y1[1]-y1[0])*k/4,yy=Y(v,y1); g.line(pl,yy,W-pr,yy,th.grid,0.5); g.text(pl-4,yy+fs*0.35,waN(v,Math.abs(y1[1]-y1[0])<8?1:0),fs,th.txt,'end');
    if(y2){ const v2=y2[0]+(y2[1]-y2[0])*k/4; g.text(W-pr+4,yy+fs*0.35,waN(v2,Math.abs(y2[1]-y2[0])<8?1:0),fs,th.txt,'start'); } }
  const days=(t1-t0)/864e5,tick=days<=1.1?3*36e5:days<=8?864e5:5*864e5,tz=new Date().getTimezoneOffset()*60000; let tt=tick>=864e5?Math.ceil((t0-tz)/864e5)*864e5+tz:Math.ceil(t0/tick)*tick;
  for(;tt<t1;tt+=tick){ const xx=X(tt); g.line(xx,pt,xx,H-pb,th.grid,0.5); g.text(xx,H-4,tick<864e5?new Date(tt).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}):new Date(tt).toLocaleDateString('de-DE',{day:'2-digit',month:'2-digit'}),fs,th.txt,'middle'); }
  lines.forEach(l=>{ const gg=r.G[l.k],y=l.r2?y2:y1,n=r.n,bucket=Math.max(1,Math.floor(n/(o.pts||600))); let seg=[]; const segs=[];
    for(let i=0;i<n;i+=bucket){ let mi=null,ma=null,im=i,iM=i; for(let j=i;j<Math.min(n,i+bucket);j++){ const v=gg[j]; if(typeof v!=='number') continue; if(mi==null||v<mi){mi=v;im=j;} if(ma==null||v>ma){ma=v;iM=j;} }
      if(mi==null){ if(seg.length) segs.push(seg); seg=[]; continue; }
      (im===iM?[[im,mi]]:im<iM?[[im,mi],[iM,ma]]:[[iM,ma],[im,mi]]).forEach(p=>seg.push([X(r.tg(p[0])),Y(p[1],y)])); }
    if(seg.length) segs.push(seg); segs.forEach(s=>g.poly(s,l.c,l.w||1,l.dash)); });
  g.rect(pl,pt,W-pl-pr,H-pt-pb,null,th.frame,0.6);
}
function waChartBars(g,items,o){
  const W=o.w,H=o.h,pl=30,pt=12,pb=16,th=o.th,fs=o.fs||9,mx=Math.max(1e-9,...items.map(x=>x.v)),bw=(W-pl-4)/Math.max(1,items.length);
  for(let k=0;k<=4;k++){ const v=mx*k/4,y=pt+(1-k/4)*(H-pt-pb); g.line(pl,y,W-4,y,th.grid,0.5); g.text(pl-4,y+fs*0.35,waN(v,mx<8?1:0),fs,th.txt,'end'); }
  items.forEach((x,i)=>{ const h=x.v/mx*(H-pt-pb),xx=pl+i*bw+bw*0.15; if(h>0) g.rect(xx,H-pb-h,bw*0.7,h,x.c||o.c||'#3a95e6');
    if(items.length<=31) g.text(xx+bw*0.35,H-4,x.l,items.length>16?fs-1.5:fs,th.txt,'middle');
    if(items.length<=16&&x.v>0) g.text(xx+bw*0.35,H-pb-h-3,waN(x.v,x.v<10?1:0),fs,o.vc||th.txt,'middle'); });
}
function waSvg(w,h,draw){ const P=[]; const g={
    rect:(x,y,ww,hh,f,s,lw)=>P.push(`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${ww.toFixed(1)}" height="${hh.toFixed(1)}" fill="${f||'none'}"${s?` stroke="${s}" stroke-width="${lw||1}"`:''}/>`),
    line:(x1,y1,x2,y2,c,lw)=>P.push(`<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${c}" stroke-width="${lw||1}"/>`),
    poly:(pts,c,lw,dash)=>P.push(`<path d="${pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join('')}" fill="none" stroke="${c}" stroke-width="${lw||1}"${dash?' stroke-dasharray="4 3"':''}/>`),
    text:(x,y,s,fs,c,an)=>P.push(`<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" font-size="${fs}" fill="${c}" text-anchor="${an||'start'}">${esc(s)}</text>`)};
  draw(g); return `<svg viewBox="0 0 ${w} ${h}" width="100%" style="display:block">${P.join('')}</svg>`; }
const WA_C={vl2:'#ff9f43',ww:'#f2b632',wwu:'#b388ff',room:'#4caf50',kol:'#ff9f43',vl:'#ff6b5b',rl:'#4aa3ff',vls:'#9aa1ab',ta:'#4caf50',hz:'#b388ff',pel:'#f2b632',tdis:'#ff6b5b',tph:'#f2b632',tpl:'#4aa3ff',tsuc:'#4caf50',php:'#ff6b5b',plp:'#4aa3ff',sh:'#4caf50',flow:'#4aa3ff',eev:'#9aa1ab'};
const WA_CHARTS=r=>{ const has_=k=>r.G[k]&&r.G[k].some(v=>typeof v==='number'), f=ls=>ls.filter(l=>has_(l.k));
  if(WA_BOIL) return [
    {t:'Vorlauf und Brennerläufe',l:f([{k:'vl',l:'Vorlauf HK1'},{k:'vls',l:'Vorlauf-Soll HK1',dash:1},{k:'vl2',l:'Vorlauf HK2'},{k:'ta',l:'Außen'}])},
    {t:'Brennermodulation',l:f([{k:'hz',l:'Modulation (%)'}])},
    {t:'Trinkwasser',l:f([{k:'ww',l:'Trinkwasser'},{k:'wwu',l:'Trinkwasser unten'}])},
    {t:'Raum und Solar',l:f([{k:'room',l:'Raum'},{k:'kol',l:'Kollektor',r2:1}])},
  ].filter(c=>c.l.length).map(c=>(c.l.forEach(l=>l.c=WA_C[l.k]),c.r2=c.l.some(l=>l.r2),c));
  return [
    {t:'Temperaturen und Verdichterläufe',l:f([{k:'vl',l:'Vorlauf'},{k:'rl',l:'Rücklauf'},{k:'vls',l:'Vorlauf-Soll',dash:1},{k:'ta',l:'Außen'}])},
    {t:'Verdichter und Leistungsaufnahme',l:f([{k:'hz',l:WA_V==='bosch'?'Verdichter (%)':'Verdichter (Hz)'},{k:'pel',l:'Strom (kW)',r2:1}])},
    {t:'Kältekreis – Temperaturen',l:f([{k:'tdis',l:'Heißgas'},{k:'tph',l:'Kondensation (HD)'},{k:'tpl',l:'Verdampfung (ND)'},{k:'tsuc',l:'Sauggas'}])},
    {t:'Kältekreis – Drücke und Überhitzung',l:f([{k:'php',l:'Hochdruck (bar)'},{k:'plp',l:'Niederdruck (bar)'},{k:'sh',l:'Überhitzung (K)',r2:1}])},
    {t:'Durchfluss und Expansionsventil',l:f([{k:'flow',l:'Durchfluss (l/min)'},{k:'eev',l:'Expansionsventil',r2:1}])},
  ].filter(c=>c.l.length).map(c=>(c.l.forEach(l=>l.c=WA_C[l.k]),c.r2=c.l.some(l=>l.r2),c)); };
function waLegend(c,dark){ const th=dark?WA_DARK:WA_LIGHT;
  return c.l.map(l=>`<span><i style="background:${l.c}"></i>${esc(l.l)}${l.r2?' (rechts)':''}</span>`).join('')
    +`<span><i style="background:${th.run}"></i>Verdichter Heizen</span><span><i style="background:${th.dhw}"></i>Warmwasser</span>`+(WA_BOIL?'':`<span><i style="background:${th.def}"></i>Abtauen</span>`); }

// ---- Anzeige im Manager (System → WP-Auswertung, ab v1.20.3; vorher unter Logs) ----
let LSUB='log';   // Logs zeigen nur noch das Log
let SYSV='sys'; try{ SYSV=localStorage.getItem('emsehs_sysv')==='wpa'?'wpa':'sys'; }catch(e){}
function vLogs(){ return vLog(); }
// Umschalter oben im Menüpunkt „System“: System | WP-Auswertung (Brötje: Kessel-Auswertung)
function sysNav(){ waVendor(waTabVendor());
  return `<div class="tools" style="margin:0 0 14px">${[['sys','System'],['wpa',WT.title]].map(([k,l])=>`<button class="chip ${SYSV===k?'on':''}" data-sysv="${k}">${l}</button>`).join('')}</div>`; }
function vWpa(){
  waVendor(waTabVendor()); const P=WPR_PER[WPA.per], c=waC()[P.k]||{}; if(!c.res && !c.loading && !c.err) waLoad(P.k);
  const r=c.res, lvP=['<span class="pill">OK</span>','<span class="pill dm">Prüfen</span>','<span class="pill no">Kritisch</span>'];
  const chips=WPR_PER.map((p,i)=>`<button class="chip ${i===WPA.per?'on':''}" data-wper="${i}">${p.l}</button>`).join('');
  const meta = r ? `${WA_DEMO?'<b style="color:var(--warn)">Demo-Daten</b> · ':''}${esc(new Date(r.raw.t0).toLocaleString('de-DE'))} – ${esc(new Date(r.raw.t1).toLocaleString('de-DE'))} · Raster ${P.step} s`
    : c.err ? `<span style="color:var(--err)">${esc(c.err)}</span>` : 'Lade Verlauf aus Home Assistant … (30 Tage kann etwas dauern)';
  const head=`<div class="card pad"><div class="cap">${WT.title}</div>
    ${hRow('Zeitraum',`<div class="tools" style="margin:0">${chips}</div>`,'Verlauf aus Home Assistant (Recorder)')}
    <div class="tools" style="margin:6px 0 10px"><button class="btn pri sm" data-act="wpdf" ${r?'':'disabled'}>${I.dl} PDF herunterladen</button>
      <button class="btn sm" data-act="wcsv" ${r?'':'disabled'}>${I.dl} Rohdaten CSV</button><button class="btn sm" data-act="wre" ${c.loading?'disabled':''}>${I.re} ${c.loading?'lädt …':'Neu laden'}</button></div>
    <div class="note" style="padding-bottom:4px">${meta}</div>
    ${r?`<div class="tiles" style="margin-top:8px">
      ${tile(WT.comp+'starts',nf(r.K.starts),waN(r.K.perDay,1)+' pro Tag',r.K.perDay>(WA_BOIL?48:24)?'hot':'')}
      ${tile('Laufzeit je Start',wprMin(r.K.medHeat),'Heizen, Median · Ø '+wprMin(r.K.avgHeat),r.K.medHeat!=null&&r.K.medHeat<(WA_BOIL?5:10)?'hot':'')}
      ${tile('Läufe unter 10 min',waN(r.K.shortShare*100,0)+' %',r.K.short+' von '+r.runs.length,r.K.shortShare>0.3?'hot':'')}
      ${tile('Laufzeit',waN(r.K.hrun,1)+' h',waN(r.K.duty*100,0)+' % der Zeit')}
      ${WA_BOIL?tile('Ø Modulation',waN(((r.val.hz||{}).heat||{}).m,0)+' %','Heizbetrieb'):tile('Abtauungen',nf(r.K.defN),'Ø '+wprMin(r.K.defAvg))}
      ${WA_BOIL?tile('Trinkwasserladungen',nf(r.K.wwN||0),'Ø '+wprMin(r.K.wwAvg)):tile('Arbeitszahl',waN(r.K.az,2),'Heizen '+waN(r.K.azH,2)+' · WW '+waN(r.K.azW,2))}
    </div>`:''}</div>`;
  if(!r) return wt(head);
  const K=r.K,V=r.val, rw=(k,v)=>row(k,esc(v));
  const bew=`<div class="card"><div class="cap">Bewertung und Hinweise</div>${r.find.map(x=>`<div class="row" style="align-items:flex-start;justify-content:flex-start;gap:12px"><span style="flex:none;width:74px">${lvP[x.lv]}</span><span class="k">${esc(x.t)}${x.h?`<small style="word-break:normal;font-size:12px;line-height:1.4;margin-top:3px">${esc(x.h)}</small>`:''}</span></div>`).join('')}</div>`;
  const ch=WA_CHARTS(r).slice(0,3).map(cc=>`<div style="padding:6px 0 10px;border-top:1px solid var(--sep)"><div style="font-size:13px;color:var(--k);margin:4px 0">${esc(cc.t)}</div>${waSvg(640,170,g=>waChartLine(g,r,cc.l,{w:640,h:170,th:WA_DARK,r2:cc.r2,fs:10}))}<div class="waleg">${waLegend(cc,true)}</div></div>`).join('');
  const days=Object.entries(r.perDay).map(([d,x])=>({l:d.slice(0,d.lastIndexOf('.')),v:x.n}));
  const verl=`<div class="card"><div class="cap">Verläufe</div>${ch}<div style="padding:6px 0 10px;border-top:1px solid var(--sep)"><div style="font-size:13px;color:var(--k);margin:4px 0">Verdichterstarts je Tag</div>${waSvg(640,140,g=>waChartBars(g,days,{w:640,h:140,th:WA_DARK,c:'#3a95e6',fs:10,vc:'#d5d9de'}))}</div></div>`;
  const takt=`<div class="card"><div class="cap">Laufzeit & Taktung</div>${rw('Verdichterstarts',nf(K.starts)+' ('+waN(K.perDay,1)+' / Tag)')}${rw('Laufzeit je Start Heizen (Median / Ø)',wprMin(K.medHeat)+' / '+wprMin(K.avgHeat))}
    ${rw('Kürzester Lauf',wprMin(K.min))}${rw('Ø Stillstand zwischen Läufen',wprMin(K.pauseAvg))}${rw('Max. Starts in einer Stunde',nf(K.maxPerHour))}${rw('Läufe Heizen / Warmwasser',K.heatN+' / '+K.dhwN)}
    ${WA_BOIL?rw('Trinkwasserladungen',(K.wwN||0)+' (Ø '+wprMin(K.wwAvg)+')'):rw('Abtauungen',K.defN+' ('+waN(K.defDay,1)+' / Tag, Ø '+wprMin(K.defAvg)+')')+rw('Backup-Heizer aktiv',waN(K.buMin/60,1)+' h')}
    ${Object.entries(r.whys).sort((a,b)=>b[1]-a[1]).map(([k,v])=>rw('Abschaltgrund: '+k,v+(v===1?' Lauf':' Läufe'))).join('')}</div>`;
  const kk=(WA_BOIL?[['hz','Brennermodulation','%',0],['vl','Vorlauf HK1','°C',1],['vls','Vorlauf-Soll HK1','°C',1],['vl2','Vorlauf HK2','°C',1],['ww','Trinkwasser','°C',1],['room','Raumtemperatur','°C',1],['ta','Außentemperatur','°C',1]]:[['hz','Verdichter',WA_V==='bosch'?'%':'Hz',0],['tdis','Heißgas','°C',1],['tsuc','Sauggas','°C',1],['sh','Sauggas-Überhitzung','K',1],['php','Hochdruck','bar',1],['plp','Niederdruck','bar',1],['tph','Kondensationstemp. (HD)','°C',1],['tpl','Verdampfungstemp. (ND)','°C',1],['capp','Kondensator: HD − Vorlauf','K',1],['eapp','Verdampfer: Außen − ND','K',1],['eev','Expansionsventil','',0],['spread','Spreizung VL − RL','K',1],['flow','Durchfluss','l/min',1]])
    .filter(x=>V[x[0]]&&V[x[0]].heat).map(x=>{ const o=V[x[0]].heat; return row(x[1],`${waN(o.m,x[3])} ${esc(x[2])}<small>${waN(o.min,x[3])} … ${waN(o.max,x[3])}</small>`); }).join('');
  const kae=kk?`<div class="card"><div class="cap">${WA_BOIL?'Werte im Brennerbetrieb Heizen, eingeschwungen':'Kältekreis & Hydraulik – Heizbetrieb, eingeschwungen'} (Ø, min … max)</div>${kk}</div>`:'';
  const tab=`<div class="card"><div class="cap">Taktung nach Außentemperatur (Heizbetrieb)</div><table class="watab"><tr><th>Außen</th><th>Zeit</th><th>Starts</th><th>Starts/h</th><th>Ø Lauf</th><th>Laufanteil</th></tr>
    ${r.taB.map(b=>`<tr><td>${esc(b.l)}</td><td>${waN(b.hrs,1)} h</td><td>${b.n}</td><td>${waN(b.per_h,2)}</td><td>${wprMin(b.avg)}</td><td>${waN(b.share*100,0)} %</td></tr>`).join('')}</table>
    <div class="note">Die vollständige Auswertung mit allen Diagrammen, Messwerten, Störungen und der Liste aller Verdichterläufe steht in der PDF-Datei.</div></div>`;
  return wt(head+`<div style="height:18px"></div><div class="grid"><div class="col">${bew}${takt}${tab}</div><div class="col">${verl}${kae}</div></div>`+waSetCard(r));
}
function waStamp(){ const d=new Date(),p=n=>String(n).padStart(2,'0'); return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}_${p(d.getHours())}-${p(d.getMinutes())}`; }
function waSave(data,name,type){ const b=new Blob([data],{type}); const u=URL.createObjectURL(b); const a=document.createElement('a'); a.href=u; a.download=name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(u),5000); }
function waClick(b){
  if(b.dataset.sysv){ SYSV=b.dataset.sysv==='wpa'?'wpa':'sys'; try{ localStorage.setItem('emsehs_sysv',SYSV); }catch(e){} render(); return true; }
  if(b.dataset.lsub){ LSUB=b.dataset.lsub; try{ localStorage.setItem('emsehs_lsub',LSUB); }catch(e){} render(); if(LSUB==='log'){ if(TAB==='bsb') loadBLog().then(()=>{ if(TAB==='bsb') bLogRefresh(); }); else loadLog(true); } return true; }
  if(b.dataset.wper){ WPA.per=+b.dataset.wper; render(); return true; }
  if(b.dataset.wsf){ WPA.sf=b.dataset.wsf; render(); return true; }
  waVendor(waTabVendor()); const act=b.dataset.act, P=WPR_PER[WPA.per], c=waC()[P.k]||{};
  if(act==='wre'){ waLoad(P.k,true); return true; }
  if(act==='wcsv' && c.res){ waSave('﻿'+wt(wprCsv(c.res)),`${WA_BOIL?'kessel':'wp'}-rohdaten_${P.k}_${waStamp()}.csv`,'text/csv;charset=utf-8'); return true; }
  if(act==='wpdf' && c.res){ const t=b.innerHTML; b.disabled=true; b.textContent='PDF wird erstellt …';
    setTimeout(()=>{ try{ waSave(waPdf(c.res,P),`${WA_BOIL?'kessel':'wp'}-auswertung_${P.k}_${waStamp()}.pdf`,'application/pdf'); }catch(e){ alert('PDF fehlgeschlagen: '+(e.message||e)); } b.disabled=false; b.innerHTML=t; },30); return true; }
  return false;
}

// ---- Kleiner PDF-Schreiber (A4, Helvetica, WinAnsi) ----
function WaPdf(){ this.W=595.28; this.H=841.89; this.pages=[]; this.newPage(); const cv=document.createElement('canvas'); this.ctx=cv.getContext('2d'); }
WaPdf.prototype.newPage=function(){ this.c=[]; this.pages.push(this.c); };
WaPdf.prototype.rgb=function(h){ if(!h) return null; let m=/rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(h); if(m) return [m[1],m[2],m[3]].map(x=>(x/255).toFixed(3)).join(' ');
  h=h.replace('#',''); if(h.length===3) h=h.split('').map(x=>x+x).join(''); return [0,2,4].map(i=>(parseInt(h.substr(i,2),16)/255).toFixed(3)).join(' '); };
WaPdf.prototype.enc=function(s){ const map={'–':150,'—':151,'…':133,'„':132,'“':147,'”':148,'‚':130,'‘':145,'’':146,'€':128,'•':149};
  const rep={'→':'->','≥':'>=','≤':'<=','−':'-','✓':'ok','↓':'','⛔':'!','⚠':'!','️':''};
  let o=''; for(const ch of String(s)){ let c; if(rep[ch]!=null){ o+=this.enc(rep[ch]); continue; } const cp=ch.codePointAt(0);
    c = map[ch]!=null?map[ch] : cp<256?cp : 63; if(c===40||c===41||c===92) o+='\\'+String.fromCharCode(c); else if(c<32||c>126) o+='\\'+c.toString(8).padStart(3,'0'); else o+=String.fromCharCode(c); } return o; };
WaPdf.prototype.tw=function(s,fs,bold){ this.ctx.font=(bold?'bold ':'')+'100px Helvetica, Arial, sans-serif'; return this.ctx.measureText(String(s).replace(/→/g,'->').replace(/≥/g,'>=')).width/100*fs; };
WaPdf.prototype.text=function(x,y,s,fs,col,an,bold){ s=wt(String(s)); const w=this.tw(s,fs,bold); if(an==='end'||an==='right') x-=w; else if(an==='middle') x-=w/2;
  this.c.push(`BT /${bold?'F2':'F1'} ${fs} Tf ${this.rgb(col||'#222')} rg ${x.toFixed(2)} ${(this.H-y).toFixed(2)} Td (${this.enc(s)}) Tj ET`); };
WaPdf.prototype.rect=function(x,y,w,h,fill,stroke,lw){ if(fill) this.c.push(`${this.rgb(fill)} rg ${x.toFixed(2)} ${(this.H-y-h).toFixed(2)} ${w.toFixed(2)} ${h.toFixed(2)} re f`);
  if(stroke) this.c.push(`${lw||0.6} w ${this.rgb(stroke)} RG [] 0 d ${x.toFixed(2)} ${(this.H-y-h).toFixed(2)} ${w.toFixed(2)} ${h.toFixed(2)} re S`); };
WaPdf.prototype.line=function(x1,y1,x2,y2,c,lw){ this.c.push(`${lw||0.6} w ${this.rgb(c)} RG [] 0 d ${x1.toFixed(2)} ${(this.H-y1).toFixed(2)} m ${x2.toFixed(2)} ${(this.H-y2).toFixed(2)} l S`); };
WaPdf.prototype.poly=function(pts,c,lw,dash){ if(!pts.length) return; this.c.push(`${lw||0.8} w ${this.rgb(c)} RG ${dash?'[3 2]':'[]'} 0 d 1 j `+pts.map((p,i)=>`${p[0].toFixed(2)} ${(this.H-p[1]).toFixed(2)} ${i?'l':'m'}`).join(' ')+' S'); };
WaPdf.prototype.wrap=function(s,fs,mw,bold){ const out=[]; let cur=''; String(s).split(/\s+/).forEach(w=>{ const t=cur?cur+' '+w:w; if(this.tw(t,fs,bold)>mw && cur){ out.push(cur); cur=w; } else cur=t; }); if(cur) out.push(cur); return out; };
WaPdf.prototype.group=function(x,y){ const p=this; return { rect:(a,b,w,h,f,s,lw)=>p.rect(x+a,y+b,w,h,f,s,lw), line:(a,b,c,d,col,lw)=>p.line(x+a,y+b,x+c,y+d,col,lw),
  poly:(pts,c,lw,dash)=>p.poly(pts.map(q=>[x+q[0],y+q[1]]),c,lw,dash), text:(a,b,s,fs,c,an)=>p.text(x+a,y+b,s,fs,c,an) }; };
WaPdf.prototype.out=function(){
  const objs=[], add=s=>{ objs.push(s); return objs.length; };
  const cat=add(''), pgs=add(''), f1=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>'), f2=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>');
  const kids=this.pages.map(c=>{ const st=c.join('\n'); const cs=add(`<< /Length ${st.length} >>\nstream\n${st}\nendstream`);
    return add(`<< /Type /Page /Parent ${pgs} 0 R /MediaBox [0 0 ${this.W} ${this.H}] /Resources << /Font << /F1 ${f1} 0 R /F2 ${f2} 0 R >> >> /Contents ${cs} 0 R >>`); });
  objs[cat-1]=`<< /Type /Catalog /Pages ${pgs} 0 R >>`; objs[pgs-1]=`<< /Type /Pages /Kids [${kids.map(k=>k+' 0 R').join(' ')}] /Count ${kids.length} >>`;
  let s='%PDF-1.4\n%\xE2\xE3\xCF\xD3\n'; const off=[];
  objs.forEach((o,i)=>{ off.push(s.length); s+=`${i+1} 0 obj\n${o}\nendobj\n`; });
  const x=s.length; s+=`xref\n0 ${objs.length+1}\n0000000000 65535 f \n`+off.map(o=>String(o).padStart(10,'0')+' 00000 n \n').join('');
  s+=`trailer\n<< /Size ${objs.length+1} /Root ${cat} 0 R /Info << /Title (${this.enc(this.title||'WP-Auswertung')}) /Producer (EMS-ESP Manager) >> >>\nstartxref\n${x}\n%%EOF`;
  const u=new Uint8Array(s.length); for(let i=0;i<s.length;i++) u[i]=s.charCodeAt(i)&255; return u; };

// ---- Inhalt der PDF ----
function waPdf(r,P){
  const pdf=new WaPdf(), M=40, CW=pdf.W-2*M, BOT=pdf.H-44, f2=(v,d)=>v==null?'–':waN(v,d), K=r.K, V=r.val;
  pdf.title=wt('Auswertung Wärmepumpe – '+P.l); let y=M;
  const need=h=>{ if(y+h>BOT){ pdf.newPage(); y=M; } };
  const h2=(t,nx)=>{ need(40+(nx||50)); y+=10; pdf.text(M,y+12,t,13,'#1f2a37',null,true); y+=17; pdf.line(M,y,M+CW,y,'#3a95e6',1.4); y+=10; };
  // Kopf
  pdf.rect(0,0,pdf.W,6,'#3a95e6');
  pdf.text(M,y+18,'Auswertung Wärmepumpe – '+P.l,20,'#1f2a37',null,true); y+=28;
  pdf.text(M,y+10,`${WPR_ANL} · ${new Date(r.raw.t0).toLocaleString('de-DE')} – ${new Date(r.raw.t1).toLocaleString('de-DE')}`,9.5,'#6b7380'); y+=14;
  pdf.text(M,y+10,`erstellt ${new Date().toLocaleString('de-DE')} · EMS-ESP Manager${(typeof ST!=='undefined'&&ST&&ST.version)?' '+ST.version:''}${WA_DEMO?' · DEMO-DATEN':''}`,9.5,WA_DEMO?'#d98a00':'#6b7380',null,WA_DEMO); y+=16;
  // Kennzahlen
  h2('Kennzahlen');
  const kp=WA_BOIL?[[K.starts,`Brennerstarts (${f2(K.perDay,1)} / Tag)`],[wprMin(K.medHeat),`Laufzeit je Start Heizen (Median, Ø ${wprMin(K.avgHeat)})`],[f2(K.shortShare*100,0)+' %','Läufe unter 10 min'],
    [f2(K.hrun,1)+' h',`Brennerlaufzeit (${f2(K.duty*100,0)} % der Zeit)`],[K.maxPerHour,'max. Starts in einer Stunde'],[f2(((V.hz||{}).heat||{}).m,0)+' %','Ø Modulation im Heizbetrieb'],
    [K.wwN||0,`Trinkwasserladungen (Ø ${wprMin(K.wwAvg)})`],[f2(((V.ww||{}).all||{}).m,1)+' °C','Ø Trinkwassertemperatur'],[f2(K.taMean,1)+' °C','Ø Außentemperatur']]:[[K.starts,`Verdichterstarts (${f2(K.perDay,1)} / Tag)`],[wprMin(K.medHeat),`Laufzeit je Start Heizen (Median, Ø ${wprMin(K.avgHeat)})`],[f2(K.shortShare*100,0)+' %','Läufe unter 10 min'],
    [f2(K.hrun,1)+' h',`Laufzeit (${f2(K.duty*100,0)} % der Zeit)`],[K.maxPerHour,'max. Starts in einer Stunde'],[K.defN,`Abtauungen (Ø ${wprMin(K.defAvg)})`],
    [f2(K.az,2),`Arbeitszahl (Heizen ${f2(K.azH,2)} · WW ${f2(K.azW,2)})`],[f2(K.eel,1)+' kWh',`Strom · Wärme ${f2(K.eth,1)} kWh`],[f2(K.taMean,1)+' °C','Ø Außentemperatur']];
  const bw=(CW-2*8)/3;
  kp.forEach((k,i)=>{ const cx=M+(i%3)*(bw+8), cy=y+Math.floor(i/3)*50; pdf.rect(cx,cy,bw,44,'#f2f5f9'); pdf.text(cx+9,cy+19,String(k[0]),15,'#1f2a37',null,true);
    pdf.wrap(k[1],8,bw-16).slice(0,2).forEach((l,j)=>pdf.text(cx+9,cy+31+j*9,l,8,'#6b7380')); });
  y+=3*50+4;
  // Bewertung
  h2('Bewertung und Hinweise');
  const lvC=['#2e9d5b','#d98a00','#e0453a'], lvT=['OK','Prüfen','Kritisch'];
  r.find.forEach(x=>{ const tl=pdf.wrap(x.t,10,CW-70,true), hl=x.h?pdf.wrap(x.h,8.5,CW-70):[]; const h=tl.length*13+hl.length*11+8; need(h);
    pdf.rect(M,y+2,54,14,lvC[x.lv]); pdf.text(M+27,y+12,lvT[x.lv],8,'#ffffff','middle',true);
    tl.forEach((l,i)=>pdf.text(M+66,y+12+i*13,l,10,'#1f2a37',null,true)); hl.forEach((l,i)=>pdf.text(M+66,y+12+tl.length*13+i*11-2,l,8.5,'#555555'));
    y+=h; pdf.line(M,y-3,M+CW,y-3,'#eef0f2',0.5); });
  // Verläufe
  h2('Verläufe',196);
  WA_CHARTS(r).forEach(cc=>{ need(196); pdf.text(M,y+9,cc.t,10,'#333333',null,true); y+=14;
    waChartLine(pdf.group(M,y),r,cc.l,{w:CW,h:160,th:WA_LIGHT,r2:cc.r2,fs:7.5,pts:500}); y+=164;
    let lx=M; [...cc.l.map(l=>[l.c,l.l+(l.r2?' (rechts)':'')]),[WA_LIGHT.run,'Verdichter Heizen'],[WA_LIGHT.dhw,'Warmwasser'],...(WA_BOIL?[]:[[WA_LIGHT.def,'Abtauen']])].forEach(([c,l])=>{ pdf.rect(lx,y+2,9,6,c); pdf.text(lx+12,y+8,l,7.5,'#555555'); lx+=pdf.tw(l,7.5)+26; }); y+=16; });
  // Taktung
  h2('Laufzeit und Taktung',150);
  need(150); const half=(CW-16)/2;
  pdf.text(M,y+9,'Verdichterstarts je Tag',10,'#333333',null,true); pdf.text(M+half+16,y+9,'Laufzeit je Tag (h)',10,'#333333',null,true); y+=14;
  const pd=Object.entries(r.perDay);
  waChartBars(pdf.group(M,y),pd.map(([d,x])=>({l:d.slice(0,d.lastIndexOf('.')),v:x.n})),{w:half,h:120,th:WA_LIGHT,c:'#3a95e6',fs:7});
  waChartBars(pdf.group(M+half+16,y),pd.map(([d,x])=>({l:d.slice(0,d.lastIndexOf('.')),v:x.h})),{w:half,h:120,th:WA_LIGHT,c:'#2e9d5b',fs:7}); y+=130;
  need(150); pdf.text(M,y+9,'Verteilung der Laufzeiten je Start (Anzahl Läufe, Minuten)',10,'#333333',null,true); y+=14;
  const hb=[[0,5],[5,10],[10,15],[15,20],[20,30],[30,45],[45,60],[60,90],[90,120],[120,1e9]];
  waChartBars(pdf.group(M,y),hb.map(b=>({l:b[1]>1e8?'>'+b[0]:b[0]+'–'+b[1],v:r.runs.filter(x=>!x.open&&x.d>=b[0]&&x.d<b[1]).length,c:b[1]<=10?'#e0453a':b[1]<=20?'#d98a00':'#3a95e6'})),{w:CW,h:120,th:WA_LIGHT,fs:7.5}); y+=130;
  // Tabellen
  const table=(cols,rows,o={})=>{ const fs=o.fs||8.5, rh=fs+6, hdr=()=>{ pdf.rect(M,y,CW,rh+2,'#f2f5f9'); let x=M; cols.forEach(c=>{ pdf.text(c.a==='r'?x+c.w*CW-5:x+5,y+rh-2,c.l,fs,'#1f2a37',c.a==='r'?'end':null,true); x+=c.w*CW; }); y+=rh+2; };
    need(rh*3); hdr();
    rows.forEach(rw=>{
      if(rw._g){ if(y+rh*5>BOT){ pdf.newPage(); y=M; hdr(); } pdf.rect(M,y+2,CW,rh+1,'#e8eef6'); pdf.text(M+5,y+rh,rw._g,fs,'#1f2a37',null,true); y+=rh+3; return; }
      const cells=cols.map((c,i)=>{ let s=String(rw[i]==null?'':rw[i]); if(c.wrap) return pdf.wrap(s,fs,c.w*CW-10).slice(0,4); while(s.length>3 && pdf.tw(s,fs)>c.w*CW-10) s=s.slice(0,-2)+'…'; return [s]; });
      const nl=Math.max(...cells.map(c=>c.length)), h=rh+(nl-1)*(fs+2);
      if(y+h>BOT){ pdf.newPage(); y=M; hdr(); } let x=M; const col=rw._c||'#222222';
      if(rw._bg) pdf.rect(M,y,CW,h,rw._bg);
      cols.forEach((c,i)=>{ cells[i].forEach((s,k)=>pdf.text(c.a==='r'?x+c.w*CW-5:x+5,y+rh-2+k*(fs+2),s,fs,(c.hc&&rw._hc)||col,c.a==='r'?'end':null,c.b&&rw._b)); x+=c.w*CW; });
      y+=h; pdf.line(M,y,M+CW,y,'#e3e6ea',0.4); }); y+=8; };
  const sub=t=>{ need(80); pdf.text(M,y+10,t,10,'#333333',null,true); y+=16; };
  sub('Taktung nach Außentemperatur (Heizbetrieb)');
  table([{l:'Außen',w:.22},{l:'Zeit',w:.14,a:'r'},{l:'Starts',w:.12,a:'r'},{l:'Starts/h',w:.14,a:'r'},{l:'Ø Lauf',w:.18,a:'r'},{l:'Laufanteil',w:.2,a:'r'}],
    r.taB.map(b=>[b.l,f2(b.hrs,1)+' h',b.n,f2(b.per_h,2),wprMin(b.avg),f2(b.share*100,0)+' %']));
  sub('Abschaltgrund und Stillstand');
  table([{l:'Grund',w:.6},{l:'Läufe',w:.2,a:'r'},{l:'Anteil',w:.2,a:'r'}],
    Object.entries(r.whys).sort((a,b)=>b[1]-a[1]).map(([k,v])=>[k,v,f2(v/Math.max(1,r.runs.length)*100,0)+' %'])
    .concat([['Ø Stillstand zwischen Läufen',wprMin(K.pauseAvg),''],['Stillstände unter 10 min',K.shortP,''],['Median / kürzester Lauf',wprMin(K.med)+' / '+wprMin(K.min),''],...(WA_BOIL?[]:[['Backup-Heizer aktiv',f2(K.buMin/60,1)+' h','']])]));
  h2('Messwerte (Ø und min … max)');
  pdf.wrap('„Heizen“ und „Warmwasser“: nur eingeschwungener Betrieb (Verdichter länger als 5 min an, ohne Abtauen).',8.5,CW).forEach(l=>{ pdf.text(M,y+8,l,8.5,'#6b7380'); y+=11; }); y+=4;
  const vr=(WA_BOIL?[['hz','Brennermodulation','%',0],['vl','Vorlauf HK1','°C',1],['vls','Vorlauf-Soll HK1','°C',1],['vl2','Vorlauf HK2','°C',1],['vls2','Vorlauf-Soll HK2','°C',1],['ww','Trinkwasser','°C',1],['wwu','Trinkwasser unten','°C',1],['room','Raumtemperatur','°C',1],['kol','Kollektor','°C',1],['ta','Außentemperatur','°C',1]]:[['hz','Verdichter',WA_V==='bosch'?'%':'Hz',0],['pel','Elektr. Leistung','kW',2],['pth','Heizleistung','kW',1],['tdis','Heißgas','°C',1],['tsuc','Sauggas','°C',1],['sh','Sauggas-Überhitzung','K',1],['php','Hochdruck','bar',1],['plp','Niederdruck','bar',1],['tph','Kondensationstemp. (HD)','°C',1],['tpl','Verdampfungstemp. (ND)','°C',1],['capp','Kondensator: HD − Vorlauf','K',1],['eapp','Verdampfer: Außen − ND','K',1],['tevi','Verdampfereintritt','°C',1],['eev','Expansionsventil','',0],['fan','Lüfter','U/min',0],['vl','Vorlauf','°C',1],['rl','Rücklauf','°C',1],['spread','Spreizung VL − RL','K',1],['flow','Durchfluss','l/min',1],['pump','Heizungspumpe','%',0],['ta','Außentemperatur','°C',1]])
    .filter(x=>V[x[0]]&&(V[x[0]].heat||V[x[0]].dhw||V[x[0]].all)).map(x=>{ const o=V[x[0]], c=m=>o[m]?`${f2(o[m].m,x[3])} (${f2(o[m].min,x[3])} … ${f2(o[m].max,x[3])})`:'–'; return [x[1],x[2],c('heat'),c('dhw'),c('all')]; });
  table([{l:'Wert',w:.25},{l:'Einheit',w:.09},{l:'Heizen',w:.22},{l:'Warmwasser',w:.22},{l:'Gesamt',w:.22}],vr);
  if(r.sets && r.sets.length){
    h2('Einstellwerte der Anlage');
    const nh=r.sets.filter(x=>x.hint&&x.hint.lv>=1).length;
    pdf.wrap(`Aktuelle Werte aus Home Assistant (Stand ${new Date().toLocaleString('de-DE')}), ${r.sets.length} Einstellungen, ${nh} mit Hinweis. Hinweise nennen in Klammern die Auffälligkeit dieser Auswertung, die mit der Einstellung zusammenhängen kann.`,8.5,CW).forEach(l=>{ pdf.text(M,y+8,l,8.5,'#6b7380'); y+=11; }); y+=4;
    const rows=[]; let lg=null; const chg=new Set((r.setChg||[]).map(c=>c.r));
    r.sets.forEach(x=>{ const g=x.g+(x.sec?' · '+x.sec:''); if(g!==lg){ rows.push({_g:g}); lg=g; }
      const rg=x.a.min!=null&&x.a.max!=null?`${waN(+x.a.min)} … ${waN(+x.a.max)}`:(x.a.options?x.a.options.length+' Optionen':'');
      const rw=[x.l+(chg.has(x.r)?' (geändert)':''),waFmt(x.st,x.a),rg,x.hint?x.hint.t:'']; rw._b=1;
      if(x.hint&&x.hint.lv>=1){ rw._hc=x.hint.lv>=2?'#c0392b':'#b36b00'; rw._bg=x.hint.lv>=2?'#fdf0ee':'#fff8ea'; }
      rows.push(rw); });
    table([{l:'Einstellung',w:.33,wrap:1},{l:'Wert',w:.13,a:'r',b:1},{l:'Bereich',w:.14},{l:'Hinweis',w:.40,wrap:1,hc:1}],rows,{fs:8});
    h2('Änderungen der Einstellwerte im Zeitraum');
    const lab=Object.fromEntries(r.sets.map(x=>[x.r,x]));
    table([{l:'Zeit',w:.2},{l:'Einstellung',w:.44,wrap:1},{l:'vorher',w:.18,a:'r'},{l:'nachher',w:.18,a:'r'}],
      (r.setChg||[]).length?r.setChg.slice(0,200).map(c=>{ const x=lab[c.r]||{l:c.r,a:{}}; return [new Date(c.t).toLocaleString('de-DE'),x.l+(x.g?' ('+x.g+')':''),waFmt(c.from,x.a),waFmt(c.to,x.a)]; }):[['keine Änderungen','','','']]);
  }
  h2(WA_BOIL?'Störungen (Kesselstatus)':'Störungen');
  table([{l:'Zeit',w:.4},{l:'Code',w:.6}], r.errs.length?r.errs.map(e=>[new Date(e.t).toLocaleString('de-DE'),e.c]):[['keine','']]);
  h2('Verdichterläufe');
  const lr=r.runs.slice().reverse(), MAXR=300;
  pdf.text(M,y+8,`Neueste zuerst${lr.length>MAXR?` (die letzten ${MAXR} von ${lr.length} – vollständig in der CSV)`:''}; rot = kürzer als 10 min.`,8.5,'#6b7380'); y+=14;
  table([{l:'Start',w:.15},{l:'Dauer',w:.1,a:'r'},{l:'Betrieb',w:.17},{l:'Außen °C',w:.1,a:'r'},{l:'Verdichter Ø',w:.12,a:'r'},{l:'VL max °C',w:.11,a:'r'},{l:'Abschaltgrund',w:.25}],
    lr.slice(0,MAXR).map(x=>{ const a=[new Date(x.s).toLocaleString('de-DE',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}),f2(x.d,0)+' min',({heat:'Heizen',dhw:'Warmwasser',cool:'Kühlen',def:'Abtauen'})[x.m]+(x.def?' + Abtauen':''),f2(x.ta,1),f2(x.hz,0),f2(x.vlmax,1),x.why]; if(x.d<10&&!x.open) a._c='#c0392b'; return a; }),{fs:8});
  h2('Datengrundlage');
  const used=WPR_SIG.filter(s=>(r.raw.ser[s.k]||[]).length).map(s=>s.l), miss=WPR_SIG.filter(s=>!(r.raw.ser[s.k]||[]).length).map(s=>s.l);
  [`Home-Assistant-Verlauf, Raster ${P.step} s. Werte: ${used.join(', ')}.`+(miss.length?` Nicht vorhanden: ${miss.join(', ')}.`:''),
   'Die Bewertung ist ein Hinweis für Fehlersuche und Optimierung und ersetzt keine Prüfung durch den Fachbetrieb. Grenzwerte für R410A/R32-Anlagen typischer Bauart.']
   .forEach(t=>pdf.wrap(t,8.5,CW).forEach(l=>{ need(12); pdf.text(M,y+8,l,8.5,'#555555'); y+=11; }));
  // Fußzeile
  const n=pdf.pages.length; pdf.pages.forEach((pg,i)=>{ pdf.c=pg; pdf.line(M,pdf.H-30,M+CW,pdf.H-30,'#e3e6ea',0.5);
    pdf.text(M,pdf.H-18,'WP-Auswertung '+P.l+' · '+WPR_ANL+' · EMS-ESP Manager',7.5,'#888888'); pdf.text(M+CW,pdf.H-18,`Seite ${i+1} von ${n}`,7.5,'#888888','end'); });
  return pdf.out();
}

// ---- Karte „Einstellwerte der Anlage“ ----
function waSetCard(r){
  const L=r.sets||[]; if(!L.length) return `<div style="height:18px"></div><div class="card pad"><div class="cap">Einstellwerte der Anlage</div><div class="note top0">Keine Einstellwerte gefunden.</div></div>`;
  const chg=new Set((r.setChg||[]).map(c=>c.r)), f=WPA.sf||'hint';
  const sel=L.filter(x=>f==='all'||(f==='hint'&&x.hint&&x.hint.lv>=1)||(f==='chg'&&chg.has(x.r)));
  const nH=L.filter(x=>x.hint&&x.hint.lv>=1).length;
  const chip=(k,l)=>`<button class="chip ${f===k?'on':''}" data-wsf="${k}">${l}</button>`;
  let rows='', lg=null;
  sel.forEach(x=>{ const g=x.g+(x.sec?' · '+x.sec:''); if(g!==lg){ rows+=`<tr><td colspan="4" style="text-align:left;color:var(--lbl);font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding-top:14px">${esc(g)}</td></tr>`; lg=g; }
    const rg=x.a.min!=null&&x.a.max!=null?`${waN(+x.a.min)} … ${waN(+x.a.max)}`:'';
    const hc=x.hint&&x.hint.lv>=2?'var(--err)':x.hint&&x.hint.lv>=1?'var(--warn)':'var(--dim)';
    rows+=`<tr><td style="text-align:left">${esc(x.l)}${chg.has(x.r)?' <span class="pill dm" style="font-size:11px;padding:1px 7px">geändert</span>':''}<div style="font-size:11px;color:var(--lbl)">${esc(x.r)}</div></td><td style="font-weight:700;color:var(--txt)">${esc(waFmt(x.st,x.a))}</td><td style="color:var(--dim)">${rg}</td><td style="text-align:left;color:${hc};font-size:13px;max-width:420px">${x.hint?esc(x.hint.t):''}</td></tr>`; });
  const ch=(r.setChg||[]).slice(0,30).map(c=>{ const x=L.find(y=>y.r===c.r)||{l:c.r,a:{}}; return `<tr><td style="text-align:left">${esc(new Date(c.t).toLocaleString('de-DE'))}</td><td style="text-align:left">${esc(x.l)}</td><td>${esc(waFmt(c.from,x.a))}</td><td style="font-weight:700;color:var(--txt)">${esc(waFmt(c.to,x.a))}</td></tr>`; }).join('');
  return `<div style="height:18px"></div><div class="grid" style="grid-template-columns:2fr 1fr"><div class="card"><div class="cap">Einstellwerte der Anlage</div>
    <div class="tools" style="margin:6px 0 4px">${chip('hint','Mit Hinweis ('+nH+')')}${chip('chg','Geändert ('+chg.size+')')}${chip('all','Alle ('+L.length+')')}</div>
    ${sel.length?`<table class="watab"><tr><th>Einstellung</th><th>Wert</th><th>Bereich</th><th style="text-align:left">Hinweis</th></tr>${rows}</table>`:'<div class="note top0">Keine Einstellungen in dieser Auswahl.</div>'}
    <div class="note">Aktuelle Werte aus Home Assistant, Entitäten automatisch zugeordnet (wie im Panel, inkl. eigener Zuordnung). Hinweise nennen in Klammern die Auffälligkeit dieser Auswertung, die mit der Einstellung zusammenhängen kann. Alle Einstellwerte stehen auch in der PDF.</div></div>
    <div class="card"><div class="cap">Änderungen im Zeitraum</div>${ch?`<table class="watab"><tr><th>Zeit</th><th style="text-align:left">Einstellung</th><th>vorher</th><th>nachher</th></tr>${ch}</table>`:'<div class="note top0">Keine Einstellung wurde im Zeitraum geändert.</div>'}</div></div>`;
}

waVendor(WA_DEF);
"""

WPA_PAGE = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WP-Auswertung</title>
<style>
:root{--bg:#000;--card:#111317;--line:#262a30;--sep:#22262b;--txt:#f2f4f7;--k:#d5d9de;--dim:#9aa1ab;--lbl:#868d97;
  --acc:#2196f3;--sam:#3a95e6;--ok:#4caf50;--warn:#f2b632;--err:#e2453a;--in:#0b0d10}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.4 Roboto,"Segoe UI",Arial,sans-serif;-webkit-font-smoothing:antialiased}
button,select,input{font:inherit;color:inherit}
/* Kopf */
.top{position:sticky;top:0;z-index:5;background:var(--bg);padding:4px 22px 0}
.hd{display:flex;align-items:center;gap:14px;max-width:1500px;margin:0 auto}
.hd .lg{width:44px;height:44px;border-radius:50%;background:#262d38 center/100% no-repeat;flex:none}
.hd .tt{font-size:22px;font-weight:700;letter-spacing:.2px}.hd .tt b{color:var(--sam)}
.hd .st{color:var(--dim);font-size:13px}
.hd .sp{flex:1}
.tabs{display:flex;gap:4px;max-width:1500px;margin:12px auto 0;overflow-x:auto;scrollbar-width:none}
.tabs a{padding:10px 16px 12px;color:var(--dim);text-decoration:none;font-size:15px;font-weight:500;white-space:nowrap;border-bottom:2px solid transparent}
.tabs a.on{color:var(--txt);border-bottom-color:var(--sam)}
.vtabs{margin:6px 0 18px;border-bottom:1px solid var(--line);max-width:none}.vtabs a{font-size:14px;padding:9px 14px 11px}
.rule{height:3px;background:linear-gradient(90deg,#3a95e6,#1f6fc0);max-width:1500px;margin:0 auto}
/* Inhalt */
.wrap{max-width:1500px;margin:0 auto;padding:18px 22px 30px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start}
.grid.g3{grid-template-columns:1fr 1fr 1fr}@media (max-width:1250px){.grid.g3{grid-template-columns:1fr 1fr}}
.fr{width:100%;height:calc(100vh - 150px);min-height:420px;border:1px solid var(--line);border-radius:12px;background:#000;display:block}
.col{display:flex;flex-direction:column;gap:18px;min-width:0}
.mas{columns:2 460px;column-gap:18px}.mas .card{break-inside:avoid;margin-bottom:18px}
@media (max-width:860px){.grid,.grid.g3{grid-template-columns:1fr}.wrap,.top{padding-left:12px;padding-right:12px}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px 6px}
.card.pad{padding-bottom:16px}
.cap{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--lbl);font-weight:500;margin:2px 0 4px}
.row{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:11px 0;border-top:1px solid var(--sep);min-height:48px}
.cap+.row,.cap+div>.row:first-child{border-top:0}
.row .k{color:var(--k);font-size:16px;min-width:0}
.row .k small{display:block;color:var(--lbl);font-size:11px;margin-top:1px;word-break:break-all}
.row .v{font-weight:700;font-size:16px;text-align:right;white-space:nowrap}
.row .v small{font-weight:400;color:var(--dim);font-size:13px;margin-left:6px}
.row .v.d{color:var(--dim);font-weight:400}
.pill{display:inline-block;font-size:13px;font-weight:600;padding:3px 10px;border-radius:12px;background:#183b23;color:#7fd99a}
.pill.no{background:#3d1a1a;color:#f19a9a}.pill.dm{background:#3a3000;color:#ffd866}
.brand{display:flex;align-items:center;justify-content:center;gap:18px;padding:8px 0 16px}
.logo{width:100px;height:100px;border-radius:50%;background:#262d38 center/100% no-repeat;flex:none}
.bt{font-size:clamp(20px,2.1vw,27px);font-weight:700;line-height:1.05;white-space:nowrap}.bt b{color:var(--sam)}
.bs{color:var(--dim);font-size:14px;margin-top:4px}
.ins{display:flex;align-items:center;gap:16px;padding:6px 0 10px}
.ins .logo{width:60px;height:60px}
.ins .t2{font-size:20px;font-weight:700;margin-top:2px}
.line{display:flex;gap:12px;color:var(--k);font-size:16px;padding:5px 2px}
.line svg{width:18px;height:18px;flex:none;margin-top:2px;opacity:.75}.line.tel{font-weight:700;color:var(--txt)}
.btns{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0 12px}
.btns.b3{grid-template-columns:1fr 1fr 1fr}@media (max-width:640px){.btns.b3{grid-template-columns:1fr}}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:42px;padding:0 16px;border-radius:9px;border:1px solid var(--line);background:#0c0e11;
  color:var(--txt);font-size:15px;font-weight:600;cursor:pointer;text-decoration:none;white-space:nowrap}
.btn svg{width:17px;height:17px;flex:none}.btn.pri{background:var(--acc);border-color:var(--acc);color:#fff}
.btn.sm{height:34px;padding:0 12px;font-size:13px}.btn:disabled{opacity:.45;cursor:default}.btn:active{filter:brightness(1.15)}
.note{color:var(--dim);font-size:13px;padding:10px 0 12px;border-top:1px solid var(--sep)}
.note.top0{border-top:0}
.msg{font-size:13px;min-height:1em;padding:2px 0 10px}.msg.err{color:var(--err)}.msg.ok{color:var(--ok)}
code{font-size:12px;word-break:break-all;user-select:all;color:#cfe3f7}
/* Bedienelemente */
.ctl{display:flex;align-items:center;gap:8px;flex:none}
.num{display:flex;align-items:center;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--in)}
.num button{width:34px;height:34px;border:0;background:transparent;color:var(--k);font-size:18px;cursor:pointer}
.num button:active{background:#1b1f25}
.num input{width:70px;height:34px;border:0;background:transparent;text-align:center;font-weight:700;font-size:15px;-moz-appearance:textfield;outline:none}
.num input::-webkit-outer-spin-button,.num input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
.u{color:var(--dim);font-size:13px;min-width:26px}
select{height:36px;border:1px solid var(--line);border-radius:8px;background:var(--in);padding:0 30px 0 10px;font-weight:600;max-width:230px;
  appearance:none;background-image:linear-gradient(45deg,transparent 50%,#9aa1ab 50%),linear-gradient(135deg,#9aa1ab 50%,transparent 50%);
  background-position:calc(100% - 15px) 15px,calc(100% - 10px) 15px;background-size:5px 5px;background-repeat:no-repeat}
.tog{width:46px;height:26px;border-radius:13px;background:#3a3f46;position:relative;cursor:pointer;border:0;flex:none}
.tog::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;transition:left .2s}
.tog.on{background:var(--sam)}.tog.on::after{left:23px}
.busy{opacity:.5;pointer-events:none}
.row.pend .num{border-color:var(--sam)}
.flash{animation:fl 1.2s}@keyframes fl{0%{background:#17324a}100%{background:transparent}}
.tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.tools .sp{flex:1}
.q{height:40px;border-radius:9px;border:1px solid var(--line);background:var(--card);padding:0 14px;min-width:240px;outline:none}
.q:focus{border-color:var(--sam)}
.chip{height:34px;padding:0 14px;border-radius:17px;border:1px solid var(--line);background:var(--card);cursor:pointer;font-size:13px;font-weight:600;color:var(--k)}
.chip.on{background:var(--sam);border-color:var(--sam);color:#fff}
/* Protokoll */
.logs{font-size:13px}
.lr{display:flex;gap:12px;padding:7px 0;border-top:1px solid var(--sep);line-height:1.35}
.lr:first-child{border-top:0}
.lr .lt{color:var(--dim);flex:none;font-variant-numeric:tabular-nums}
.lr .ll{flex:none;width:58px;font-weight:700;font-size:11px;padding-top:1px}
.lr .lm{min-width:0;word-break:break-word;white-space:pre-wrap;user-select:text}
.lr .ln{display:block;color:var(--lbl);font-size:11px}
.lr.WARNING .ll{color:var(--warn)}.lr.ERROR .ll,.lr.CRITICAL .ll{color:var(--err)}.lr.ERROR .lm{color:#f3b0aa}.lr.INFO .ll{color:#7fb8ec}.lr.DEBUG .ll,.lr.DEBUG .lm{color:var(--dim)}
.pkt{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;padding:3px 0;border-top:1px solid var(--sep);word-break:break-all;user-select:text}
.empty{color:var(--dim);padding:30px 0;text-align:center}
/* Heizstab */
.htop{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:4px 0 14px}
.hid{display:flex;align-items:center;gap:14px;flex:1;min-width:220px}.hid .t2{font-size:22px;font-weight:700}.hid .ts{color:var(--dim);font-size:13px}
.hico{width:52px;height:52px;border-radius:50%;background:#262d38;display:flex;align-items:center;justify-content:center;color:var(--dim);flex:none}
.hico svg{width:28px;height:28px}.hico.on{background:#3a1d12;color:#ff8a3d}.hico.ok{background:#14301d;color:#7fd99a}
.hpills{display:flex;gap:6px;flex-wrap:wrap}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--in);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:0}
.tile .tl{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--lbl)}
.tile .tv{font-size:20px;font-weight:700;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .ts{font-size:12px;color:var(--dim);margin-top:2px}.tile.hot .tv{color:#ff8a3d}
.subnav{display:flex;gap:8px;margin:18px 0;flex-wrap:wrap}.subnav .chip{height:40px;padding:0 18px;font-size:14px}
.subnav.top{margin:0 0 18px}a.chip{display:inline-flex;align-items:center;text-decoration:none;white-space:nowrap}
/* ---- Handy (iPhone/Android): Zeilen umbrechen statt quetschen ---- */
.row .k small{word-break:normal;overflow-wrap:anywhere}
@media (max-width:640px){
  body{padding-bottom:env(safe-area-inset-bottom)}
  .wrap{padding:12px 10px 28px}.card{padding:12px 14px 4px;border-radius:14px}.card.pad{padding-bottom:12px}
  .grid,.grid.g3{gap:12px}.col{gap:12px}.mas{columns:1}.mas .card{margin-bottom:12px}
  .row{flex-wrap:wrap;row-gap:8px;min-height:52px}
  .row .k{flex:1 1 120px;min-width:0;font-size:15px}.num button{width:38px}
  .row>.ctl,.row>.tog,.row>.v{margin-left:auto}
  .row>select{flex:1 1 100%;max-width:none;width:100%;height:44px}
  .row .q{width:100%!important;flex:1 1 auto;height:44px!important}
  .row>.ctl:has(.q){flex:1 1 100%}.row>.ctl:has(.q) .btn{flex:none}
  .row>input.q{flex:1 1 100%}
  .num input{width:64px}
  .htop{gap:10px;padding-bottom:10px}.hid{min-width:0;flex:1 1 100%}.hpills{flex:1 1 100%}.hid .t2{font-size:20px}.hico{width:44px;height:44px}
  .hbt,.hbt .btn{width:100%}
  .tiles{grid-template-columns:1fr 1fr;gap:8px}.tile{padding:9px 11px}.tile .tv{font-size:18px}
  .subnav{position:sticky;top:48px;z-index:4;background:var(--bg);margin:12px -10px;padding:8px 10px;flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}
  .subnav::-webkit-scrollbar{display:none}.subnav .chip{flex:none;height:42px}
  .subnav.top{margin:0 -10px 12px}
  .tools{gap:8px}.tools .q{min-width:0;flex:1 1 100%}
  .lr{flex-wrap:wrap;gap:4px 10px}.lr .lm{flex:1 1 100%}
  .btns{grid-template-columns:1fr 1fr}
}
@media (max-width:380px){ .btns{grid-template-columns:1fr} .tiles{grid-template-columns:1fr 1fr} .row .k{flex-basis:100%} .row>.ctl{margin-left:0} }

/* ---- Handy / Tablet (iPhone, Android) ---- */
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{overscroll-behavior-y:contain;-webkit-tap-highlight-color:transparent}
.btns>*{min-width:0}.btn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media (max-width:440px){.btns{grid-template-columns:1fr}}
.tabs{-webkit-overflow-scrolling:touch;scroll-snap-type:x proximity}.tabs a{scroll-snap-align:start}
@media (pointer:coarse){
  .tabs a{padding:13px 16px 14px}
  .btn,.btn.sm{height:44px;font-size:15px}
  .chip{height:40px;font-size:14px}
  .num button{width:42px;height:42px}.num input{height:42px;font-size:16px}
  .tog{width:52px;height:32px;border-radius:16px}.tog::after{width:26px;height:26px}
  .tog.on::after{left:23px}
  input,select,textarea{font-size:16px}
  .cap{font-size:12px}
}
/* ---- Reiter „<Hersteller> EHS“ (ESP-Bus-Adapter) – Aufbau wie die Adapter-Oberfläche ---- */
.bnav .chip{display:inline-flex;align-items:center;gap:8px}.bnav .chip svg{width:18px;height:18px;flex:none}
.pills{display:flex;gap:6px;flex-wrap:wrap;padding:0 0 12px}
.hint{display:flex;gap:12px;align-items:center;padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:var(--card);margin-bottom:10px}
.hint.err{border-color:#5a2222;background:#1c1012}.hint.warn{border-color:#4a3c10;background:#19160b}.hint.info{border-color:#1d3a5a;background:#0e1824}.hint.okm{border-color:#1f4a2c;background:#0d1a12}
.hint .hi{flex:none;font-size:18px;line-height:1.2}.hint .ht{flex:1;min-width:0;font-size:14px;color:var(--k)}.hint .ht small{display:block;color:var(--dim);font-size:12px;margin-top:2px}.hint .btn{flex:none}
.tiles.t6{grid-template-columns:repeat(6,1fr)}@media (max-width:1100px){.tiles.t6{grid-template-columns:repeat(3,1fr)}}
.tile .tl{display:flex;align-items:center;gap:4px;min-height:20px}.tile .tl .ibx{margin:-4px -6px -4px auto}
.tile.bad .tv{color:#f19a9a}.tile.good .tv{color:#7fd99a}.tile.lnk{cursor:pointer}.tile.lnk:active{filter:brightness(1.2)}
.ibx{width:24px;height:24px;border-radius:50%;border:0;background:transparent;color:var(--sam);display:inline-flex;align-items:center;justify-content:center;cursor:pointer;padding:0;flex:none;vertical-align:middle}
.ibx svg{width:18px;height:18px}.ibx:active{background:#13263a}
.cap.capi{display:flex;align-items:center;gap:6px;min-height:24px}.cap .n{text-transform:none;letter-spacing:0;opacity:.7}
.row .k:has(.kt){display:flex;flex-wrap:wrap;align-items:center;column-gap:6px}.row .k:has(.kt) small{flex-basis:100%}
.seg{display:flex;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:4px;gap:4px;margin-bottom:12px}
.seg button{flex:1;height:38px;border:0;border-radius:9px;background:transparent;color:var(--k);font-weight:600;font-size:14px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.seg button.on{background:var(--sam);color:#fff}
.q.bq{width:100%;min-width:0;height:42px}
.accs{gap:10px}
.acc{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.acc>.ah{display:flex;align-items:center;gap:12px;width:100%;padding:14px 16px;border:0;background:transparent;cursor:pointer;text-align:left;min-height:62px;color:var(--txt)}
.acc>.ah .ai{width:36px;height:36px;border-radius:10px;background:#1a2230;color:var(--sam);display:flex;align-items:center;justify-content:center;flex:none}
.acc>.ah .ai svg{width:19px;height:19px}
.acc>.ah .at{flex:1;min-width:0}.acc>.ah b{display:block;font-size:16px}.acc>.ah small{display:block;color:var(--dim);font-size:12px;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.acc>.ah .cv{display:flex;width:20px;height:20px;color:var(--dim);transition:transform .2s;flex:none}.acc>.ah .cv svg{width:20px;height:20px}.acc.open>.ah .cv{transform:rotate(180deg)}
.acc>.ab{display:none;padding:0 16px 8px;border-top:1px solid var(--sep)}.acc.open>.ab{display:block}
.acc .ainfo{display:flex;justify-content:flex-end;margin:6px -4px -8px 0}
.dlg{position:fixed;inset:0;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;z-index:30;padding:16px}
.dlg .card{width:min(520px,100%);max-height:86vh;overflow-y:auto;padding:18px 20px 6px}
.dlg h3{margin:0 0 8px;font-size:18px;display:flex;align-items:center;gap:8px}.dlg h3 svg{width:22px;height:22px;color:var(--sam);flex:none}
.dlg .dtx p{margin:0 0 10px;line-height:1.55;color:var(--k)}.dlg .dtx ul{margin:0 0 10px;padding-left:20px;line-height:1.55;color:var(--k)}
.ok{color:#7fd99a}.bad{color:#f19a9a}
@media (max-width:640px){
  .tiles.t6{grid-template-columns:1fr 1fr}
  .bnav{gap:4px}.bnav .chip{flex:1 1 0;min-width:0;flex-direction:column;justify-content:center;height:auto!important;min-height:54px;padding:6px 2px;font-size:11px;gap:3px;border-radius:12px}
  .bnav .chip svg{width:21px;height:21px}
  .row .k:has(.kt){flex-basis:100px}.num input{width:52px}
  .acc>.ab{padding:0 14px 6px}
  .dlg{align-items:flex-end;padding:0}.dlg .card{width:100%;border-radius:18px 18px 0 0;max-height:80vh;padding-bottom:calc(8px + env(safe-area-inset-bottom))}
  .hint{flex-wrap:wrap}.hint .ht{flex:1 1 calc(100% - 40px)}.hint .btn{margin-left:30px}
  .seg button{font-size:13px}
}

.wrap{padding-top:14px}
/*__WPA_CSS__*/</style></head>
<body><div class="wrap" id="main"><div class="empty">Lade …</div></div>
<script>
/* EMS-ESP Manager – WP-Auswertung (Bosch / Buderus über EMS-ESP), Ansicht im Dashboard „EMS-ESP“ */
window.WA_VENDOR='__VENDOR__';
const $=s=>document.querySelector(s);
function H(){ for(const w of [window.parent, window.parent&&window.parent.parent, window.top]){ try{ const ha=w&&w!==window&&w.document.querySelector('home-assistant'); if(ha&&ha.hass) return ha.hass; }catch(e){} } return null; }
const ws=m=>H().connection.sendMessagePromise(m);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const nf=(v,d)=>v==null||isNaN(v)?'–':Number(v).toLocaleString('de-DE',{minimumFractionDigits:d||0,maximumFractionDigits:d||0});
const row=(k,v,sub)=>`<div class="row"><span class="k">${esc(k)}</span><span class="v">${v}${sub?`<small>${esc(sub)}</small>`:''}</span></div>`;
const hRow=(k,ctl,sub)=>`<div class="row"><span class="k">${esc(k)}${sub?`<small>${esc(sub)}</small>`:''}</span>${ctl}</div>`;
const tile=(l,v,sub,cls)=>`<div class="tile ${cls||''}"><div class="tl">${esc(l)}</div><div class="tv">${v}</div>${sub?`<div class="ts">${esc(sub)}</div>`:''}</div>`;
const I={
  re:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/></svg>',
  dl:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 4v12M7 11l5 5 5-5M4 20h16"/></svg>',
};
let TAB='ssystem', BSUB='', ST=null;
function vLog(){ return ''; } function loadLog(){} function loadBLog(){ return Promise.resolve(); } function bLogRefresh(){}
/*__WPA_JS__*/
SYSV='wpa';
function render(){ const y=window.scrollY; $('#main').innerHTML=vWpa(); window.scrollTo(0,y); }
document.addEventListener('click',ev=>{ const b=ev.target.closest('button'); if(b) waClick(b); });
(function wait(n){ const h=H(); if(h&&h.connection){ render(); return; }
  if(n>100){ $('#main').innerHTML='<div class="empty">Diese Seite funktioniert nur innerhalb von Home Assistant (Dashboard „EMS-ESP“ → WP-Auswertung).</div>'; return; }
  setTimeout(()=>wait(n+1),100); })(0);
</script></body></html>
"""


def inject(html: str) -> str:
    """Auswertung in eine Manager-Seite einsetzen."""
    return html.replace("/*__WPA_CSS__*/", WPA_CSS).replace("/*__WPA_JS__*/", WPA_JS)


class WpaAppView(HomeAssistantView):
    """GET /api/ems_esp_manager/wpa – Seite für das iframe im Dashboard „EMS-ESP“ (Bosch).

    Enthält keine Daten; die Werte holt die Seite über die HA-Verbindung des Frontends."""

    url = URL_WPA_APP
    name = f"api:{DOMAIN}:wpa"
    requires_auth = False

    async def get(self, request):
        from aiohttp import web

        return web.Response(
            text=inject(WPA_PAGE).replace("__VENDOR__", "bosch"),
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
