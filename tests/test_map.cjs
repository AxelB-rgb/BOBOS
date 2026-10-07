const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const elements = new Map();
const element = id => { if (!elements.has(id)) elements.set(id,{value:id==='floor'?'N0':id==='from'||id==='to'?'2026-01-12':'',addEventListener(){},replaceChildren(){},textContent:'',innerHTML:''});return elements.get(id); };
const anomalies = [{id:'shared',type:'empty_zone_hvac',mapping:'shared',severity:'low',served_rooms:[{code:'A'},{code:'B'}]}, {id:'dedicated',type:'empty_room_hvac',room:'A',severity:'low'}];
const svg = {localName:'svg',attributes:[],removeAttribute(){},querySelectorAll(){return [];}};
const context = vm.createContext({document:{getElementById:element},location:{search:''},URLSearchParams,DOMParser:class {parseFromString(){return {documentElement:svg,querySelector(){return null;}};}},fetch:async url=> {
  if(url==='/api/metadata')return new Promise(()=>{});
  return {ok:true,json:async()=>url.startsWith('/api/insights/')?{anomalies,summary:{anomaly_count:2,truncated:false}}:{rooms:[{code:'A',observed_hours:24},{code:'B',observed_hours:0}]},text:async()=>'<svg/>'};
}});
vm.runInContext(fs.readFileSync('Backend/static/map.js','utf8'),context);
(async()=>{
 await vm.runInContext('load()',context);
 assert.equal(vm.runInContext("state.byRoom.get('A').length",context),2);
 assert.equal(vm.runInContext("state.byRoom.get('B').length",context),1);
 assert.equal(vm.runInContext("state.byRoom.get('A')[0]===state.byRoom.get('B')[0]",context),true);
 element('from').value='2026-02-01';element('to').value='2026-01-01';
 await vm.runInContext('load()',context);
 assert.match(element('map').textContent,/date de fin/);
 console.log('Carte : départ partagé, références communes et dates inversées vérifiés.');
})().catch(error=>{console.error(error);process.exitCode=1;});
