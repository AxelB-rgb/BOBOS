'use strict';
const $ = id => document.getElementById(id);
const state = {byRoom:new Map(),rooms:new Map(),svg:null,selected:null,revision:0,floorRevision:0,raw:null};
const esc = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const typeLabel = {empty_room_hvac:'CVC actif pendant une absence',empty_zone_hvac:'CVC partagé actif pendant une absence',empty_room_lighting:'Éclairage actif pendant une absence',empty_zone_lighting:'Éclairage partagé actif pendant une absence'};
async function api(url){const response=await fetch(url);if(!response.ok){const data=await response.json();throw Error(typeof data.detail==='string'?data.detail:'Erreur serveur');}return response.json();}
function dates(){return new URLSearchParams({from:$('from').value,to:$('to').value,limit:'1000'});}
async function initialize(){
  const floors=['N0','N1','N2','N3','N4','N5','S1'];
  $('floor').innerHTML=floors.map(f=>`<option value="${f}">${f}</option>`).join('');
  const metadata=await api('/api/metadata'),query=new URLSearchParams(location.search);
  for(const id of ['from','to']){const value=query.get(id);$(id).min=metadata.from;$(id).max=metadata.to;$(id).value=value&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&value>=metadata.from&&value<=metadata.to?value:metadata.default_date;}
  if(floors.includes(query.get('floor')))$('floor').value=query.get('floor');
  await load();
}
async function load(){
  const revision=++state.revision;
  ++state.floorRevision;
  state.svg=null;state.raw=null;state.selected=null;
  $('map').textContent='Chargement…';$('room-panel').textContent='Sélectionnez une salle sur le plan.';
  $('refresh').disabled=true;
  try{
    if($('to').value<$('from').value)throw Error('La date de fin doit suivre la date de début.');
    const [raw,dashboard]=await Promise.all([api('/api/insights/raw?'+dates()),api('/api/dashboard?'+dates())]);
    if(revision!==state.revision)return;
    state.raw=raw;state.rooms=new Map(dashboard.rooms.map(room=>[room.code,room]));state.byRoom=new Map();
    for(const anomaly of raw.anomalies){
      const rooms=anomaly.served_rooms?.map(room=>room.code)||(anomaly.room?[anomaly.room]:[]);
      for(const room of new Set(rooms)){if(!state.byRoom.has(room))state.byRoom.set(room,[]);state.byRoom.get(room).push(anomaly);}
    }
    $('map-notice').textContent=raw.summary.truncated?`Échantillon : ${raw.summary.returned_count} anomalies affichées sur ${raw.summary.anomaly_count}. Une salle sans couleur ne garantit pas l’absence d’anomalie.`:'Les départs partagés sont affichés sur toutes leurs salles desservies. Leur consommation reste collective.';
    $('back').href='/?'+new URLSearchParams({from:$('from').value,to:$('to').value});
    await renderSvg();
  }catch(error){if(revision===state.revision){$('map').textContent=error.message;$('map-count').textContent='';}}
  finally{if(revision===state.revision)$('refresh').disabled=false;}
}
async function renderSvg(){
  if(!state.raw)return;
  const revision=++state.floorRevision,dataRevision=state.revision,floor=$('floor').value;
  state.svg=null;state.selected=null;$('map').textContent='Chargement du plan…';$('room-panel').textContent='Sélectionnez une salle sur le plan.';
  try{
    const response=await fetch('/exports/'+encodeURIComponent(floor)+'.svg');
    if(!response.ok)throw Error('Plan indisponible pour cet étage.');
    const text=await response.text();
    if(revision!==state.floorRevision||dataRevision!==state.revision)return;
    const doc=new DOMParser().parseFromString(text,'image/svg+xml');
    if(doc.querySelector('parsererror')||doc.documentElement.localName!=='svg')throw Error('Plan SVG invalide.');
    const svg=doc.documentElement;svg.removeAttribute('width');svg.removeAttribute('height');
    svg.querySelectorAll('script,foreignObject').forEach(el=>el.remove());
    for(const element of [svg,...svg.querySelectorAll('*')])for(const attribute of [...element.attributes]){
      if(attribute.name.toLowerCase().startsWith('on')||(/href$/i.test(attribute.name)&&!attribute.value.startsWith('#')))element.removeAttribute(attribute.name);
    }
    const floorRooms=new Set();
    svg.querySelectorAll('[data-selectable="true"]').forEach(element=>{
      const room=element.getAttribute('data-room-code')||element.getAttribute('data-code');if(!room)return;
      floorRooms.add(room);element.dataset.mapRoom=room;
      const items=state.byRoom.get(room)||[],known=state.rooms.get(room)?.observed_hours>0;
      const severity=items.some(a=>a.severity==='high')?'high':items.some(a=>a.severity==='medium')?'medium':items.length?'low':known?'none':'unknown';
      element.classList.add('map-'+severity);element.setAttribute('tabindex','0');element.setAttribute('role','button');
      element.setAttribute('aria-label',`${room} · ${known?items.length+' anomalie(s)':'présence non mesurée'}`);
      element.addEventListener('click',()=>selectRoom(room));
      element.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectRoom(room);}});
    });
    $('map').replaceChildren(svg);state.svg=svg;$('floor-title').textContent='Plan '+floor;
    const ids=new Set([...floorRooms].flatMap(room=>(state.byRoom.get(room)||[]).map(a=>a.id)));
    $('map-count').textContent=`${ids.size} anomalie(s) sur cet étage`;
  }catch(error){if(revision===state.floorRevision)$('map').textContent=error.message;}
}
function selectRoom(room){
  state.selected=room;state.svg.querySelectorAll('.selected').forEach(el=>el.classList.remove('selected'));
  const shapes=state.svg.querySelectorAll(`[data-map-room="${CSS.escape(room)}"]`);shapes.forEach(el=>el.classList.add('selected'));
  const metadata=state.rooms.get(room),name=metadata?.name||shapes[0]?.getAttribute('data-room-name')||room;
  const items=state.byRoom.get(room)||[],known=metadata?.observed_hours>0;
  $('room-panel').className='room-panel';
  $('room-panel').innerHTML=`<h2>${esc(name)}</h2><div class="room-meta">${esc(room)} · ${esc($('floor').value)}</div><p>${known?'Présence mesurée · couverture '+esc(metadata.coverage_pct)+' %':'Présence non mesurée sur cette période. L’absence d’anomalie ne prouve pas que la salle est vide.'}</p>`+
    (items.length?items.map(a=>`<article class="anomaly ${esc(a.severity)}"><div class="anomaly-title">${esc(typeLabel[a.type]||a.type)}</div><p>${esc(a.circuit_id||'')} · ${esc(a.duration_hours)} h · ${esc(a.energy_kwh)} kWh<br>${esc(a.start_at)} → ${esc(a.end_at)}</p><p>${esc(a.evidence)}</p>${a.mapping==='shared'?'<p>Consommation du départ partagé, non attribuée à cette seule salle.</p>':''}</article>`).join(''):`<p>${state.raw.summary.truncated?'Aucune anomalie dans l’échantillon affiché.':known?'Aucune anomalie détectée pour cette salle selon les données et seuils sélectionnés.':'Les mesures de présence sont insuffisantes pour conclure.'}</p>`)+
    (items.length?'<div class="advice"><strong>Vérification préalable</strong><p>Vérifier la présence et les contraintes techniques. Pour le CVC, contrôler le confort, la sécurité et le hors-gel avant de proposer un mode éco. Pour l’éclairage, vérifier les besoins de sécurité avant une extinction.</p></div>':'');
}
$('refresh').addEventListener('click',load);
$('floor').addEventListener('change',renderSvg);
for(const id of ['from','to'])$(id).addEventListener('change',load);
initialize().catch(error=>{$('map').textContent=error.message;});
