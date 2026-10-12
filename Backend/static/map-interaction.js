'use strict';
function installMapViewport(){
  const svg=state.svg,v=svg?.viewBox?.baseVal;
  if(!v?.width||!v.height)return;
  const bounds=svg.getBBox?.();
  const drawing=bounds?.width>0&&bounds?.height>0?bounds:v;
  const margin=Math.max(drawing.width,drawing.height)*.02;
  state.mapBase={x:drawing.x-margin,y:drawing.y-margin,width:drawing.width+2*margin,height:drawing.height+2*margin};
  state.mapView={...state.mapBase};state.mapDragged=false;
  updateMapViewport();
  svg.addEventListener('wheel',event=>{event.preventDefault();const point=new DOMPoint(event.clientX,event.clientY).matrixTransform(svg.getScreenCTM().inverse());zoomMap(event.deltaY<0?1.2:1/1.2,point);},{passive:false});
  let drag=null;
  svg.addEventListener('pointerdown',event=>{if(event.button!==0)return;state.mapDragged=false;drag={x:event.clientX,y:event.clientY,view:{...state.mapView},id:event.pointerId};});
  svg.addEventListener('pointermove',event=>{if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(dx,dy)<4&&!state.mapDragged)return;state.mapDragged=true;svg.setPointerCapture(event.pointerId);const matrix=svg.getScreenCTM();state.mapView={...drag.view,x:drag.view.x-dx/matrix.a,y:drag.view.y-dy/matrix.d};updateMapViewport();});
  const stop=()=>{drag=null;};svg.addEventListener('pointerup',stop);svg.addEventListener('pointercancel',stop);
}
function updateMapViewport(){if(!state.mapView||!state.svg)return;const v=state.mapView;state.svg.setAttribute('viewBox',`${v.x} ${v.y} ${v.width} ${v.height}`);$('zoom-level').textContent=Math.round(state.mapBase.width/v.width*100)+' %';$('zoom-out').disabled=v.width>=state.mapBase.width;$('zoom-in').disabled=v.width<=state.mapBase.width/12;}
function zoomMap(factor,point){if(!state.mapView)return;const v=state.mapView,base=state.mapBase;const width=Math.max(base.width/12,Math.min(base.width,v.width/factor)),ratio=width/v.width;const p=point||{x:v.x+v.width/2,y:v.y+v.height/2};state.mapView={x:p.x-(p.x-v.x)*ratio,y:p.y-(p.y-v.y)*ratio,width,height:v.height*ratio};updateMapViewport();}
document.getElementById('zoom-in').addEventListener('click',()=>zoomMap(1.4));
document.getElementById('zoom-out').addEventListener('click',()=>zoomMap(1/1.4));
document.getElementById('zoom-reset').addEventListener('click',()=>{if(state.mapBase){state.mapView={...state.mapBase};updateMapViewport();}});
