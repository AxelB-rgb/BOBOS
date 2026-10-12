'use strict';
const $ = id => document.getElementById(id);
const state = {byRoom:new Map(),rooms:new Map(),svg:null,selected:null,revision:0,floorRevision:0,raw:null,circuits:[],circuit:null,assets:new Map()};
function mapFloor(room){const floor=room?.floor||'';if(/^N[0-5]$|^S1$/.test(floor))return floor;if(floor==='Sous-sol')return 'S1';if(floor==='RDC')return 'N0';if(floor==='Terrasse')return 'N5';const match=(room?.code||'').match(/(?:^|-)(0[0-5]|S1)-/);if(match)return match[1]==='S1'?'S1':'N'+Number(match[1]);const number=floor.match(/Etage ([1-5])/);return number?'N'+number[1]:null;}
const esc = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const typeLabel = {night_presence:'Présence nocturne · planning à vérifier',empty_room_hvac:'CVC actif pendant une absence',empty_zone_hvac:'CVC partagé actif pendant une absence',empty_room_lighting:'Éclairage actif pendant une absence',empty_zone_lighting:'Éclairage partagé actif pendant une absence'};
async function api(url){const response=await fetch(url);if(!response.ok){const data=await response.json();throw Error(typeof data.detail==='string'?data.detail:'Erreur serveur');}return response.json();}
function dates(){return new URLSearchParams({from:$('from').value,to:$('to').value,limit:'1000'});}
async function initialize(){
  const floors=['N0','N1','N2','N3','N4','N5','S1'];
  $('floor').innerHTML=floors.map(f=>`<option value="${f}">${f}</option>`).join('');
  const metadata=await api('/api/metadata'),query=new URLSearchParams(location.search);
  $('electrical-layer').checked=query.get('layer')==='electrical';
  $('circuit-browser').hidden=!$('electrical-layer').checked;
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
    validateDateWindow();
    if($('to').value<$('from').value)throw Error('La date de fin doit suivre la date de début.');
    const [raw,dashboard]=await Promise.all([api('/api/insights/raw?'+dates()),api('/api/map/data?'+dates())]);
    if(revision!==state.revision)return;
    state.circuits=dashboard.electrical?.circuits||[];
    renderCircuitList();
    const requested=new URLSearchParams(location.search);
    const focusedAlert=requested.get('alert')?await api('/api/alerts/'+encodeURIComponent(requested.get('alert'))):null;
    if(revision!==state.revision)return;
    if(focusedAlert?.can_locate){focusedAlert.type=focusedAlert.rule_id;if(['ENERGY','REG-01'].includes(focusedAlert.rule_id)){focusedAlert.energy_kwh=focusedAlert.evidence?.[0]?.energy_kwh;focusedAlert.duration_hours=focusedAlert.evidence?.[0]?.duration_hours;}raw.anomalies.push(focusedAlert);}
    const focus=raw.anomalies.find(a=>a.id===requested.get('anomaly'));
    const room=requested.get('room')||focus?.room||focus?.served_rooms?.[0]?.code;
    const target=dashboard.rooms.find(r=>r.code===room);
    if(target&&mapFloor(target))$('floor').value=mapFloor(target);
    state.focusRoom=room;state.focusAnomaly=focus;
    $('circuit').value=requested.get('circuit')||'';state.circuit=state.circuits.find(c=>c.circuit_id===$('circuit').value)||null;
    if(!room&&!requested.get('floor')&&state.circuit?.rooms.length){const floor=mapFloor(state.circuit.rooms[0]);if(floor)$('floor').value=floor;}
    state.raw=raw;state.rooms=new Map(dashboard.rooms.map(room=>[room.code,room]));state.byRoom=new Map();
    for(const anomaly of raw.anomalies){
      const rooms=anomaly.circuit_id&&anomaly.rule_id?state.circuits.find(c=>c.circuit_id===anomaly.circuit_id)?.rooms.map(r=>r.code)||[]:anomaly.served_rooms?.map(room=>room.code)||(anomaly.room?[anomaly.room]:[]);
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
      element.addEventListener('click',event=>{if(!state.mapDragged)selectRoom(room);else event.preventDefault();});
      element.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectRoom(room);}});
    });
    $('map').replaceChildren(svg);state.svg=svg;installMapViewport();$('floor-title').textContent='Plan '+floor;
    const ids=new Set([...floorRooms].flatMap(room=>(state.byRoom.get(room)||[]).map(a=>a.id)));
    $('map-count').textContent=`${ids.size} anomalie(s) sur cet étage`;
    highlightCircuit();
    const initial=(floorRooms.has(state.focusRoom)?state.focusRoom:null)||state.circuit?.rooms.find(r=>floorRooms.has(r.code))?.code||[...floorRooms].find(r=>state.byRoom.has(r));
    if(initial&&floorRooms.has(initial))selectRoom(initial);
  }catch(error){if(revision===state.floorRevision)$('map').textContent=error.message;}
}
function selectRoom(room){
  state.selected=room;state.svg.querySelectorAll('.selected').forEach(el=>el.classList.remove('selected'));
  const shapes=state.svg.querySelectorAll(`[data-map-room="${CSS.escape(room)}"]`);shapes.forEach(el=>el.classList.add('selected'));
  const metadata=state.rooms.get(room),name=metadata?.name||shapes[0]?.getAttribute('data-room-name')||room;
  const allItems=state.byRoom.get(room)||[],focusedId=new URLSearchParams(location.search).get('alert');
  const focused=allItems.find(a=>a.id===focusedId);
  const items=focused?[focused]:allItems,known=metadata?.observed_hours>0;
  $('room-panel').className='room-panel';
  $('room-panel').innerHTML=`<h2>${esc(name)}</h2><div class="room-meta">${esc(room)} · ${esc($('floor').value)}</div><p>${known?'Présence mesurée · couverture '+esc(metadata.coverage_pct)+' %':'Présence non mesurée sur cette période. L’absence d’anomalie ne prouve pas que la salle est vide.'}</p>`+
    (items.length?items.map(a=>`<article class="anomaly ${esc(a.severity)}"><div class="anomaly-title">${esc(typeLabel[a.type]||a.title||a.type)}</div><p>${esc(a.circuit_id||'')} · ${a.duration_hours!=null?esc(a.duration_hours)+' h':''}${a.energy_kwh!=null?' · '+esc(a.energy_kwh)+' kWh':''}<br>${esc(a.start_at||'Référentiel')} ${a.end_at?'→ '+esc(a.end_at):''}</p><p>${esc(a.reason||(a.type==='night_presence'?(a.rule||'Présence à un horaire inhabituel à vérifier.'):'Absence mesurée alors que ce départ consomme.'))}</p>${a.mapping==='shared'?'<p>Consommation du départ partagé, non attribuée à cette seule salle.</p>':''}</article>`).join(''):`<p>${state.raw.summary.truncated?'Aucune anomalie dans l’échantillon affiché.':known?'Aucune anomalie détectée pour cette salle selon les données et seuils sélectionnés.':'Les mesures de présence sont insuffisantes pour conclure.'}</p>`)+
    circuitPanel()+feedsPanel(room)+(items.length?'<div class="advice"><strong>Vérification préalable</strong><p>Vérifier la présence et les contraintes techniques. Pour le CVC, contrôler le confort, la sécurité et le hors-gel avant de proposer un mode éco. Pour l’éclairage, vérifier les besoins de sécurité avant une extinction.</p></div>':'');
}
const usageNames={Lighting:'Éclairage',Heating:'Chauffage',Cooling:'Climatisation',Sockets:'Prises',Distribution:'Distribution','Ventilation and Auxilaries':'Ventilation'};
function circuitPanel(){const c=state.circuit;if(!c)return '';const assets=state.assets.get(c.circuit_id);if(!assets)loadAssets(c.circuit_id);return `<h3>Salles alimentées par ${esc(c.circuit_id)}</h3><p>${c.rooms.map(r=>`<button class="feed-button" data-room="${esc(r.code)}">${esc(r.name||r.code)} · ${esc(r.code)} · ${esc(r.floor)}</button>`).join(' ')||'Aucune salle reliée dans le référentiel.'}</p><h3>Ce qui est alimenté</h3><div id="feed-assets">${assets?assets.length?'<ul>'+assets.map(a=>`<li>${esc(a.name||a.id)}${a.count>1?' × '+a.count:''}${a.room?' · '+esc(a.room):''}</li>`).join('')+'</ul>':'Aucun équipement renseigné.':'Chargement des équipements…'}</div>`;}
async function loadAssets(id){try{const data=await api('/api/energy/circuits/'+encodeURIComponent(id)+'/assets');state.assets.set(id,data.assets);if(state.circuit?.circuit_id===id&&$('feed-assets'))$('feed-assets').innerHTML=data.assets.length?'<ul>'+data.assets.map(a=>`<li>${esc(a.name||a.id)}${a.count>1?' × '+a.count:''}${a.room?' · '+esc(a.room):''}</li>`).join('')+'</ul>':'Aucun équipement renseigné.';}catch(error){if(state.circuit?.circuit_id===id&&$('feed-assets'))$('feed-assets').textContent=error.message;}}
async function openRoom(code){const room=state.rooms.get(code);if(!room)return;if(!mapFloor(room))return;if(mapFloor(room)!==$('floor').value){$('floor').value=mapFloor(room);state.focusRoom=code;await renderSvg();}if(state.svg)selectRoom(code);}
function feedsPanel(room){const feeds=state.circuits.filter(c=>c.rooms.some(r=>r.code===room));return '<h3>Départs alimentant cette salle</h3>'+ (feeds.length?feeds.map(c=>`<article class="anomaly"><button class="feed-button" data-feed="${esc(c.circuit_id)}">${esc(c.circuit_id)} · ${esc(usageNames[c.usage]||c.usage)}</button><p>${c.energy_kwh==null?'Consommation non mesurée':esc(c.energy_kwh)+' kWh sur la période'} · ${c.rooms.length} salle(s)${c.mapping==='shared'?' · consommation collective':''}${c.unmeasured?' · sans compteur mesuré':c.included?'':' · exclu des sommes'}</p></article>`).join(''):'<p>Aucun départ mesuré relié à cette salle.</p>');}
function highlightCircuit(){if(!state.svg)return;const rooms=new Set(state.circuit?.rooms.map(r=>r.code)||[]);state.svg.querySelectorAll('[data-map-room]').forEach(el=>{el.classList.toggle('feed-selected',rooms.has(el.dataset.mapRoom));el.classList.toggle('feed-dim',!!state.circuit&&!rooms.has(el.dataset.mapRoom));});if(state.circuit){const c=state.circuit;$('map-notice').textContent=`${c.circuit_id} · ${usageNames[c.usage]||c.usage} · ${c.energy_kwh??'—'} kWh · ${c.rooms.length} salle(s) desservie(s). Relations documentées dans le MSI 2022. ${c.mapping_conflicts?c.mapping_conflicts+' correspondance(s) MSI/CDE à vérifier. ':''}Les consommations partagées ne sont pas réparties par salle.`;}renderElectricalLayer();}
function roomFeeds(room){return state.circuits.filter(c=>c.rooms.some(r=>r.code===room));}
function electricalLabel(feeds){const ids=feeds.map(c=>c.circuit_id.replace(/^Depart_/, 'D'));return ids.slice(0,2).join(' · ')+(ids.length>2?' +'+(ids.length-2):'');}
function renderElectricalLayer(){
  $('circuit-browser').hidden=!$('electrical-layer').checked;
  if(!state.svg)return;
  state.svg.querySelectorAll('.electrical-overlay').forEach(e=>e.remove());
  const enabled=$('electrical-layer').checked;
  state.svg.classList?.toggle('electrical-view',enabled);
  $('electrical-layer-notice').hidden=!enabled;
  $('electrical-legend').hidden=!enabled;
  if(!enabled)return;
  const namespace='http://www.w3.org/2000/svg',group=document.createElementNS(namespace,'g');
  group.setAttribute('class','electrical-overlay');
  const shapes=new Map();
  state.svg.querySelectorAll('[data-map-room]').forEach(shape=>{
    const room=shape.dataset.mapRoom,box=shape.getBBox(),previous=shapes.get(room);
    if(!previous||box.width*box.height>previous.width*previous.height)shapes.set(room,box);
  });
  let linked=0;
  for(const [room,box] of shapes){
    const feeds=roomFeeds(room);
    if(feeds.length)linked++;
    if(state.circuit&&!feeds.some(c=>c.circuit_id===state.circuit.circuit_id))continue;
    if(box.width<=0||box.height<=0)continue;
    const label=state.circuit?electricalLabel([state.circuit]):feeds.length?electricalLabel(feeds):'Départ inconnu';
    const size=Math.min(box.height*.17,box.width/Math.max(label.length*.63,5));
    const text=document.createElementNS(namespace,'text');
    text.setAttribute('x',box.x+box.width/2);text.setAttribute('y',box.y+box.height*.64);
    text.setAttribute('font-size',size);text.setAttribute('text-anchor','middle');
    text.setAttribute('class',feeds.length?'electrical-label':'electrical-label unmapped');
    text.textContent=label;
    const title=document.createElementNS(namespace,'title');
    title.textContent=room+' : '+(feeds.map(c=>c.circuit_id+' · '+c.label).join('\n')||'Aucune relation renseignée');
    text.appendChild(title);group.appendChild(text);
  }
  state.svg.appendChild(group);
  $('electrical-layer-notice').textContent=state.circuit?`${state.circuit.circuit_id} : salles alimentées en bleu, salle sélectionnée en jaune. Les autres étages sont accessibles dans le panneau des salles.`:`${linked} salle(s) avec départ renseigné sur ${shapes.size}. D158 = Depart_158 ; +N = autres départs. Cliquez sur une salle pour voir tous ses départs, puis sur un départ pour afficher sa desserte. Relations du MSI, sans tracé de câblage supposé.`;
}
$('electrical-layer').addEventListener('change',()=>{renderElectricalLayer();const params=new URLSearchParams(location.search);if($('electrical-layer').checked)params.set('layer','electrical');else params.delete('layer');window.history.replaceState(null,'','/map?'+params);});
async function chooseCircuit(id){if(id)$('electrical-layer').checked=true;state.circuit=state.circuits.find(c=>c.circuit_id===id)||null;if(state.circuit&&!circuitMatches(state.circuit,$('circuit-search').value))$('circuit-search').value='';renderCircuitList();$('circuit').value=id;state.focusRoom=null;const first=state.circuit?.rooms.find(r=>mapFloor(r)===$('floor').value)||state.circuit?.rooms[0];if(first&&mapFloor(first)!==$('floor').value&&mapFloor(first)){$('floor').value=mapFloor(first);await renderSvg();}highlightCircuit();if(first&&state.svg)selectRoom(first.code);else if(state.circuit){state.svg?.querySelectorAll('.selected').forEach(el=>el.classList.remove('selected'));$('room-panel').innerHTML=circuitPanel()+'<p>Aucune salle identifiée pour ce départ.</p>';}else if(state.selected&&state.svg)selectRoom(state.selected);if(!state.circuit)$('map-notice').textContent='Sélectionnez une salle pour afficher ses départs électriques.';}
$('circuit').addEventListener('change',()=>chooseCircuit($('circuit').value));
$('room-panel').addEventListener('click',event=>{const button=event.target.closest('[data-feed]');if(button)chooseCircuit(button.dataset.feed);const room=event.target.closest('[data-room]');if(room)openRoom(room.dataset.room);});
$('clear-circuit').addEventListener('click',()=>chooseCircuit(''));
$('refresh').addEventListener('click',load);
$('floor').addEventListener('change',renderSvg);
for(const id of ['from','to'])$(id).addEventListener('change',()=>{limitDateWindow(id,$('from').min,$('from').max);load();});
initialize().catch(error=>{$('map').textContent=error.message;});

document.querySelectorAll('[data-dashboard-view]').forEach(a=>{const p=new URLSearchParams(location.search);a.href='/?'+new URLSearchParams({view:a.dataset.dashboardView,...(p.get('from')?{from:p.get('from')}:{}),...(p.get('to')?{to:p.get('to')}: {})});});

function circuitMatches(c,term){return [c.circuit_id,c.label,usageNames[c.usage]||c.usage,...c.rooms.flatMap(r=>[r.code,r.name])].join(' ').toLocaleLowerCase('fr').includes(term.trim().toLocaleLowerCase('fr'));}
function renderCircuitList(){const term=$('circuit-search').value;const items=state.circuits.filter(c=>circuitMatches(c,term)).sort((a,b)=>a.circuit_id.localeCompare(b.circuit_id,'fr',{numeric:true}));$('circuit').innerHTML='<option value="">Tous les départs</option>'+items.map(c=>`<option value="${esc(c.circuit_id)}" title="${esc(c.label)}">${esc(c.circuit_id)} · ${esc(usageNames[c.usage]||c.usage)} · ${c.rooms.length} salle(s)${c.unmeasured?' · sans compteur':''} — ${esc(c.label)}</option>`).join('');$('circuit').value=state.circuit?.circuit_id||'';$('circuit-list-count').textContent=items.length+' / '+state.circuits.length+' départs';}
$('circuit-search').addEventListener('input',renderCircuitList);
