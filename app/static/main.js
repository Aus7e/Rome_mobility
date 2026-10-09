
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const $ = (id) => document.getElementById(id);
const ids = ["hour","rain","speed","demand","sideTraffic"];
const state = { engineChosen:false, promoted:false, network:null, simulation:null, comparison:null, overrides:{}, playing:true, frame:0, lastTick:0, renderer:null, scene:null, controls:null, camera:null, cars:new Map(), lights:[], road:null, path:null, rain:null };
const SCALE = 8;
const palette = [0x63d7c2,0x92b6fc,0xf1c370,0xd0dce9,0xdd858d,0x95c9dd];

function flash(msg, error=false){$("message").textContent=msg;$("message").classList.toggle("error",error);}
function syncLabels(){
  $("hourValue").textContent=String($("hour").value).padStart(2,"0")+":00";
  $("rainValue").textContent=Number($("rain").value).toFixed(1)+" mm/h";
  $("speedValue").textContent=$("speed").value+" km/h";
  $("demandValue").textContent=$("demand").value+" veicoli/h";
  $("sideTrafficValue").textContent=$("sideTraffic").value+"%";
  const day=new Date($("day").value+"T12:00:00");
  const weekend=day.getDay()===0||day.getDay()===6, h=+$("hour").value;
  const factor=weekend?(h<6?.46:(h>=11&&h<=20?.78:.60)):(h<6?.30:((h>=7&&h<=9)?1.65:(h>=17&&h<=19)?1.5:([6,10,16,20].includes(h)?1.08:.88)));
  $("estimatedDemand").textContent="Domanda modellata: "+Math.round($("demand").value*factor)+" veicoli/h per direzione · fattore "+factor.toFixed(2)+" (ipotetico)";
}
ids.forEach(id=>$(id).addEventListener("input",syncLabels));
$("day").addEventListener("change",syncLabels);

function params(){
  return {
    day:$("day").value, hour:+$("hour").value,
    rain_mm_h:+$("rain").value,speed_kmh:+$("speed").value,
    demand_vph:+$("demand").value, cycle_s:+$("cycle").value,
    green_s:+$("green").value, duration_min:+$("duration").value,
    seed:+$("seed").value,mode:$("mode").value,segment:$("segment").value,
    side_traffic_share:+$("sideTraffic").value/100,overrides:state.overrides
  };
}
async function api(url,opts={}){
  const response=await fetch(url,{...opts,headers:{"Content-Type":"application/json",...(opts.headers||{})}});
  const payload=await response.json();
  if(!response.ok)throw new Error(typeof payload.detail==="string"?payload.detail:JSON.stringify(payload.detail||payload));
  return payload;
}
async function getNetwork(){
  state.network=await api(($("engine").value==="sumo"?"/api/sumo/network":"/api/network")+"?segment="+encodeURIComponent($("segment").value));
  $("sourceChip").textContent=$("engine").value==="sumo"?"SUMO · OSM generato":state.network.source==="openstreetmap"?"OSM · non verificato":"Demo sintetica";
  $("networkNote").textContent=state.network.quality+" · "+Math.round(state.network.length_m/100)/10+" km";
  state.overrides={};
  renderSettings();
  drawNetwork();
}
function makeEl(tag,text,cls){
  const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;
}
function renderSettings(){
  const root=$("signalSettings");root.replaceChildren();
  if(!state.network.signals.length){root.append(makeEl("p","Nessun segnale OSM individuato vicino al percorso: non ne inventiamo.","hint"));return;}
  for(const [i,s] of state.network.signals.entries()){
    const box=makeEl("div",undefined,"signal-item");
    const head=makeEl("div",undefined,"signal-item-head");
    head.append(makeEl("strong",(i+1)+". "+s.label));
    const focus=makeEl("button","Localizza ↗");focus.type="button";focus.addEventListener("click",()=>focusSignal(s));
    head.append(focus);box.append(head);
    const field=makeEl("div",undefined,"field");
    const label=makeEl("label","Offset manuale");
    const value=makeEl("output","0 s");label.append(value);field.append(label);
    const input=makeEl("input");input.type="range";input.min=0;input.max=239;input.step=1;input.value=0;
    input.setAttribute("aria-label","Offset "+s.id);
    input.addEventListener("input",()=>{
      state.overrides[s.id]={...state.overrides[s.id],offset_s:+input.value};
      value.textContent=input.value+" s";
    });
    field.append(input);box.append(field);
    const greenField=makeEl("div",undefined,"field");
    const greenLabel=makeEl("label","Verde individuale");
    const greenOutput=makeEl("output","come generale");
    greenLabel.append(greenOutput);greenField.append(greenLabel);
    const greenInput=makeEl("input");greenInput.type="range";greenInput.min="10";greenInput.max="180";greenInput.step="1";greenInput.value=$("green").value;
    greenInput.setAttribute("aria-label","Verde "+s.id);
    greenInput.addEventListener("input",()=>{
      state.overrides[s.id]={...state.overrides[s.id],offset_s:state.overrides[s.id]?.offset_s||0,green_s:+greenInput.value};
      greenOutput.textContent=greenInput.value+" s";
    });
    greenField.append(greenInput);box.append(greenField);
    const small=makeEl("small","Km "+(s.s_m/1000).toFixed(2)+" · "+(s.source==="synthetic_demo"?"dimostrativo":"OSM, non validato"));
    box.append(small);root.append(box);
  }
}
function positionAt(distance){
  const p=state.path, points=p.points, s=Math.max(0,Math.min(p.total,distance));
  let low=0,high=p.cumulative.length-1;
  while(low+1<high){const mid=Math.floor((low+high)/2);if(p.cumulative[mid]<s)low=mid;else high=mid;}
  const len=p.cumulative[high]-p.cumulative[low]||1;
  const t=(s-p.cumulative[low])/len;
  const a=points[low],b=points[high];
  const tangent=new THREE.Vector3(b.x-a.x,0,b.z-a.z).normalize();
  return {point:a.clone().lerp(b,t),tangent};
}
function worldPath(){
  const points=state.network.points;
  const lat0=points[0][0],lon0=points[0][1], cos=Math.cos(lat0*Math.PI/180);
  const world=points.map(([lat,lon])=>new THREE.Vector3((lon-lon0)*111195*cos/SCALE,0,-(lat-lat0)*111195/SCALE));
  const cumulative=[0];for(let i=1;i<points.length;i++){
    cumulative.push(cumulative[i-1]+Math.hypot((points[i][0]-points[i-1][0])*111195,(points[i][1]-points[i-1][1])*111195*Math.cos((points[i][0]+points[i-1][0])/2*Math.PI/180)));
  }
  return {points:world,cumulative,total:cumulative.at(-1)};
}
function rectangleRoad(a,b,width,material,y=0){
  const length=a.distanceTo(b);if(length<.05)return;
  const mesh=new THREE.Mesh(new THREE.BoxGeometry(width,.10,length),material);
  mesh.position.copy(a).add(b).multiplyScalar(.5);mesh.position.y=y;
  mesh.rotation.y=Math.atan2(b.x-a.x,b.z-a.z);
  state.road.add(mesh);
}
function rand(seed){const x=Math.sin(seed*127.1+0.5)*43758.5453;return x-Math.floor(x);}
function makeLabel(txt,pos){
  const canvas=document.createElement("canvas");canvas.width=400;canvas.height=66;
  const ctx=canvas.getContext("2d");ctx.fillStyle="#18364af0";ctx.fillRect(0,0,400,66);
  ctx.font="bold 25px sans-serif";ctx.fillStyle="#d7f4ec";ctx.textAlign="center";ctx.fillText(txt,200,42);
  const tex=new THREE.CanvasTexture(canvas);
  const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:tex,depthWrite:false}));
  sprite.scale.set(29,4.9,1);sprite.position.copy(pos);sprite.position.y=10;
  state.road.add(sprite);
}
function lightModel(sig){
  const mat=new THREE.MeshStandardMaterial({color:0x21354a,roughness:.65});
  const group=new THREE.Group();
  const pole=new THREE.Mesh(new THREE.CylinderGeometry(.15,.18,4,7),mat);pole.position.y=2;group.add(pole);
  const head=new THREE.Mesh(new THREE.BoxGeometry(.9,2.5,.8),mat);head.position.y=5;group.add(head);
  const bulbs=[];
  for(const [i,color] of [0xe2575a,0xf2c267,0x47cf9b].entries()){
    const b=new THREE.Mesh(new THREE.SphereGeometry(.24,10,8),new THREE.MeshStandardMaterial({color,emissive:0x080808,emissiveIntensity:0}));
    b.position.set(0,5.76-i*.72,.43);group.add(b);bulbs.push(b);
  }
  const loc=positionAt(sig.s_m);
  const sideways=new THREE.Vector3(-loc.tangent.z,0,loc.tangent.x);
  group.position.copy(loc.point).addScaledVector(sideways,4.1);
  state.road.add(group);
  return bulbs;
}
function drawNetwork(){
  if(!state.scene)return;
  if(state.road)state.scene.remove(state.road);
  state.road=new THREE.Group();state.scene.add(state.road);
  state.path=worldPath();state.lights=[];
  const asphalt=new THREE.MeshStandardMaterial({color:0x303c49,roughness:1});
  const verge=new THREE.MeshStandardMaterial({color:0x3d675c,roughness:1});
  const stripe=new THREE.MeshBasicMaterial({color:0xc5d3b9});
  for(let i=1;i<state.path.points.length;i++){
    const a=state.path.points[i-1],b=state.path.points[i];
    rectangleRoad(a,b,11,verge,-.17);rectangleRoad(a,b,8.8,asphalt,0);
    rectangleRoad(a,b,.1,stripe,.08);
  }
  for(let meter=0;meter<state.network.length_m;meter+=70){
    const a=positionAt(meter).point,b=positionAt(Math.min(meter+22,state.network.length_m)).point;
    for(const off of [-2.2,2.2]){
      const aa=a.clone(),bb=b.clone();
      const tangent=positionAt(meter).tangent;
      const cross=new THREE.Vector3(-tangent.z,0,tangent.x);
      aa.addScaledVector(cross,off);bb.addScaledVector(cross,off);
      rectangleRoad(aa,bb,.09,stripe,.09);
    }
  }
  // The SUMO road layer includes cross streets and junctions from the imported network.
  if(state.network.roads){
    const sideRoad=new THREE.MeshStandardMaterial({color:0x476271,roughness:1});
    const first=state.network.points[0], latitude=first[0],longitude=first[1];
    const project=(p)=>new THREE.Vector3(
       (p[1]-longitude)*111195*Math.cos(latitude*Math.PI/180)/SCALE,0,
       -(p[0]-latitude)*111195/SCALE
    );
    for(const road of state.network.roads){
      for(let j=1;j<road.points.length;j++)
        rectangleRoad(project(road.points[j-1]),project(road.points[j]),
          Math.max(.45,road.width_m/SCALE),sideRoad,.11);
    }
  }
  for(let i=0;i<state.network.signals.length;i++)state.lights.push(lightModel(state.network.signals[i]));
  makeLabel("PRATI FISCALI",positionAt(0).point);
  makeLabel("GRA",positionAt(state.network.length_m).point);
  const bMats=[0x243f4a,0x2c5061,0x315366,0x3b4d67].map(color=>new THREE.MeshStandardMaterial({color,roughness:1}));
  for(let i=0;i<(state.network.roads?0:110);i++){
    const s=(i+.5)/110*state.network.length_m;
    const {point,tangent}=positionAt(s);
    const cross=new THREE.Vector3(-tangent.z,0,tangent.x);
    const off=(i%2?1:-1)*(12+rand(i+17)*25);
    const h=4+rand(i+4)*12,w=4+rand(i+80)*6;
    const building=new THREE.Mesh(new THREE.BoxGeometry(w,h,w*1.1),bMats[i%bMats.length]);
    building.position.copy(point).addScaledVector(cross,off);
    building.position.y=h/2-.5;state.road.add(building);
  }
  const middle=positionAt(state.network.length_m/2).point;
  state.controls.target.copy(middle);
  state.camera.position.copy(middle).add(new THREE.Vector3(120,185,220));
  state.camera.near=.1;state.camera.far=5000;state.camera.updateProjectionMatrix();state.controls.update();
  for(const c of state.cars.values())state.scene.remove(c);state.cars.clear();
}
function focusSignal(sig){
  if(!state.controls)return;
  const pos=positionAt(sig.s_m).point;state.controls.target.copy(pos);
  state.camera.position.copy(pos).add(new THREE.Vector3(35,45,55));state.controls.update();
}
function setup3D(){
  try{
    const scene=new THREE.Scene();scene.background=new THREE.Color(0x15263a);scene.fog=new THREE.FogExp2(0x15263a,.00065);
    const host=$("scene");
    const camera=new THREE.PerspectiveCamera(50,1,.1,5000);
    const renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:"high-performance"});
    renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.setSize(host.clientWidth,host.clientHeight);host.append(renderer.domElement);
    const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.maxPolarAngle=Math.PI/2.06;
    controls.minDistance=10;controls.maxDistance=1800;
    scene.add(new THREE.HemisphereLight(0xd3e9ff,0x193d37,2.2));
    const sun=new THREE.DirectionalLight(0xffffff,2.3);sun.position.set(-50,150,60);scene.add(sun);
    const ground=new THREE.Mesh(new THREE.PlaneGeometry(9000,9000),new THREE.MeshStandardMaterial({color:0x19362f,roughness:1}));
    ground.rotation.x=-Math.PI/2;ground.position.y=-.38;scene.add(ground);
    const drops=new Float32Array(1100*3);
    for(let i=0;i<1100;i++){
      drops[i*3]=(rand(i+400)-.5)*115;
      drops[i*3+1]=rand(i+811)*65;
      drops[i*3+2]=(rand(i+1200)-.5)*115;
    }
    const dropsGeo=new THREE.BufferGeometry();dropsGeo.setAttribute("position",new THREE.BufferAttribute(drops,3));
    const dropsMesh=new THREE.Points(dropsGeo,new THREE.PointsMaterial({color:0x82bce3,size:.32,transparent:true,opacity:.65,depthWrite:false}));
    scene.add(dropsMesh);state.rain=dropsMesh;
    state.scene=scene;state.camera=camera;state.renderer=renderer;state.controls=controls;
    const resize=()=>{if(!host.clientWidth||!host.clientHeight)return;renderer.setSize(host.clientWidth,host.clientHeight);camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();};
    new ResizeObserver(resize).observe(host);resize();
    requestAnimationFrame(tick);
  }catch(error){$("renderError").hidden=false;flash("Renderer 3D non disponibile: "+error.message,true);}
}
function makeCar(id,direction){
  const group=new THREE.Group();
  const color=palette[id%palette.length];
  const body=new THREE.Mesh(new THREE.BoxGeometry(.76,.44,1.33),new THREE.MeshStandardMaterial({color,metalness:.12,roughness:.55}));
  body.position.y=.37;group.add(body);
  const roof=new THREE.Mesh(new THREE.BoxGeometry(.64,.28,.72),new THREE.MeshStandardMaterial({color:0x21364a,roughness:.4}));
  roof.position.y=.72;group.add(roof);
  state.scene.add(group);return group;
}
function showFrame(i){
  const sim=state.simulation;if(!sim||!state.path)return;
  const frame=sim.frames[i];if(!frame)return;
  const present=new Set();
  for(const row of frame.cars){
    const id=row[0], direction=row[1], lane=row[2], distance=row[3];
    present.add(id);
    let mesh=state.cars.get(id);
    if(!mesh){mesh=makeCar(id,direction);state.cars.set(id,mesh);}
    if(sim.engine==="sumo"){
      // TraCI native x/y projected to geographic coordinates by sumolib.
      const [lon,lat,kmh,bearing]=row.slice(1);
      const origin=state.network.points[0], cos=Math.cos(origin[0]*Math.PI/180);
      mesh.position.set((lon-origin[1])*111195*cos/SCALE,0,
                        -(lat-origin[0])*111195/SCALE);
      mesh.rotation.y=Math.PI-bearing*Math.PI/180;
    }else{
      const s=direction===0?distance:state.network.length_m-distance;
      const {point,tangent}=positionAt(s);
      const cross=new THREE.Vector3(-tangent.z,0,tangent.x);
      const offset=direction===0?-1.1-lane*2.15:1.1+lane*2.15;
      mesh.position.copy(point).addScaledVector(cross,offset);
      mesh.rotation.y=Math.atan2(tangent.x,tangent.z)+(direction===0?0:Math.PI);
    }
  }
  for(const [id,mesh] of state.cars)if(!present.has(id)){state.scene.remove(mesh);state.cars.delete(id);}
  frame.signals.forEach((aspect,index)=>{
    const lights=state.lights[index];if(!lights)return;
    for(let j=0;j<3;j++)lights[j].material.emissiveIntensity=j===({red:0,amber:1,green:2}[aspect])?1.8:0;
  });
  $("timeline").value=i;
  $("clock").textContent=String(Math.floor(frame.t/60)).padStart(2,"0")+":"+String(frame.t%60).padStart(2,"0");
}
function tick(now){
  requestAnimationFrame(tick);
  if(state.simulation && state.playing && now-state.lastTick>=1000/Number($("playSpeed").value)){
    state.frame+=1;
    if(state.frame>=state.simulation.frames.length){state.playing=false;state.frame=state.simulation.frames.length-1;}
    showFrame(state.frame);state.lastTick=now;
  }
  if(state.rain){
    state.rain.visible=Number($("rain").value)>0;
    if(state.rain.visible){
      state.rain.position.copy(state.controls.target);
      const pos=state.rain.geometry.attributes.position;
      for(let i=0;i<pos.count;i++){
        const y=pos.array[i*3+1]-(.55+Number($("rain").value)*.065);
        pos.array[i*3+1]=y<0?65:y;
      }
      pos.needsUpdate=true;
    }
  }
  state.controls.update();state.renderer.render(state.scene,state.camera);
}
function formatSeconds(x){return x==null?"—":Math.round(x)+" s";}
function metricsUI(m){
  $("travelKpi").textContent=formatSeconds(m.avg_travel_s);
  $("delayKpi").textContent=formatSeconds(m.avg_delay_s);
  $("queueKpi").textContent=String(m.max_queued_vehicles);
  $("stopsKpi").textContent=m.mean_stops_per_trip==null?"—":String(m.mean_stops_per_trip);
  $("completionLabel").textContent=m.completed+"/"+m.inserted+" veicoli completati · "+m.effective_speed_kmh+" km/h";
}
function queueChart(){
  const canvas=$("queueChart"),ctx=canvas.getContext("2d");
  const w=canvas.clientWidth,h=canvas.clientHeight;
  canvas.width=w*devicePixelRatio;canvas.height=h*devicePixelRatio;ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle="#345064";ctx.lineWidth=1;
  for(let i=1;i<=3;i++){ctx.beginPath();ctx.moveTo(34,i*(h-30)/4);ctx.lineTo(w-12,i*(h-30)/4);ctx.stroke();}
  if(!state.simulation)return;
  const data=state.simulation.queue_series;
  if(!data.length)return;
  const max=Math.max(4,...data.map(x=>x[1]));
  ctx.beginPath();ctx.strokeStyle="#64e1bd";ctx.lineWidth=2;
  data.forEach(([t,n],i)=>{const x=34+t/state.simulation.frames.at(-1).t*(w-52),y=8+(1-n/max)*(h-31);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();
  ctx.fillStyle="#879cad";ctx.font="11px sans-serif";ctx.fillText(String(max),6,15);ctx.fillText("0",15,h-20);ctx.fillText("Tempo →",w-76,h-5);
}
async function waitForJob(job){
  while(true){
    const snapshot=await api("/api/sumo/jobs/"+encodeURIComponent(job.id));
    flash((snapshot.message||"In esecuzione")+" · "+Math.round(100*snapshot.progress)+"%");
    if(snapshot.status==="complete")
      return api("/api/sumo/jobs/"+encodeURIComponent(job.id)+"/result");
    if(snapshot.status==="failed")throw new Error(snapshot.message);
    await new Promise(resolve=>setTimeout(resolve,1100));
  }
}
async function refreshSumoStatus(){
  const s=await api("/api/sumo/status");
  const job=s.setup, preparing=job&&["queued","running"].includes(job.status);
  if(s.available){
    $("sumoStatus").textContent="● SUMO pronto · rete importata automaticamente";
  }else if(preparing){
    $("sumoStatus").textContent="◌ Preparazione automatica SUMO · "+(job.message||"in corso")+" · "+Math.round(100*job.progress)+"%";
  }else if(job?.status==="failed"){
    $("sumoStatus").textContent="⚠ Importazione fallita: "+job.message.slice(-240)+" · Premi «Riprova»; la demo resta disponibile.";
  }else if(!s.installed||!s.python_modules||!s.netconvert){
    $("sumoStatus").textContent="Anteprima disponibile · per SUMO avvia la versione Docker";
  }else{
    $("sumoStatus").textContent="La rete SUMO non è ancora pronta · premi «Riprova»";
  }
  $("setupSumoBtn").disabled=Boolean(preparing);
  // A usable preview loads immediately; upgrade to real SUMO only once it is ready.
  if(s.available && !state.engineChosen && !state.promoted && !$("runBtn").disabled){
    state.promoted=true;
    $("engine").value="sumo";
    try{
      await getNetwork();
      await run();
    }catch(error){
      $("engine").value="preview";
      state.engineChosen=true;
      flash("SUMO non caricabile: "+error.message+". Puoi continuare con la demo.",true);
      await getNetwork();
    }
  }
  return s;
}
async function setupSumo(){
  $("setupSumoBtn").disabled=true;
  try{
    const job=await api("/api/sumo/setup",{method:"POST",body:"{}"});
    await waitForJob(job);await refreshSumoStatus();
    flash("Rete SUMO costruita da OSM; verifica semafori e OD prima di usare i KPI.");
  }catch(e){flash(e.message,true);}
  finally{$("setupSumoBtn").disabled=false;}
}
async function run(){
  $("runBtn").disabled=true;flash("Calcolo dei veicoli, delle code e degli stati semaforici…");
  try{
    const sim=$("engine").value==="sumo"
      ?await waitForJob(await api("/api/sumo/jobs",{method:"POST",body:JSON.stringify(params())}))
      :await api("/api/simulate",{method:"POST",body:JSON.stringify(params())});
    state.simulation=sim;state.frame=0;state.playing=true;state.lastTick=0;
    $("timeline").max=Math.max(0,sim.frames.length-1);
    metricsUI(sim.metrics);queueChart();showFrame(0);
    flash("Simulazione completata · "+sim.metrics.inserted+" veicoli inseriti. Modello ipotetico, non traffico live.");
  }catch(e){flash(e.message,true);}
  finally{$("runBtn").disabled=false;}
}
async function compare(){
  $("compareBtn").disabled=true;flash("Confronto baseline / scenario in corso…");
  try{
    const c=$("engine").value==="sumo"
      ?await waitForJob(await api("/api/sumo/compare/jobs",{method:"POST",body:JSON.stringify(params())}))
      :await api("/api/compare",{method:"POST",body:JSON.stringify(params())});
    state.comparison=c;
    const fields=[["Tempo medio", "avg_travel_s"," s"],["Ritardo medio","avg_delay_s"," s"],["Coda massima","max_queued_vehicles",""],["Fermate per viaggio","mean_stops_per_trip",""]];
    const root=$("comparison");root.replaceChildren();
    for(const [label,key,unit] of fields){
      const row=makeEl("div",undefined,"compare-row");
      const a=c.baseline[key],b=c.experiment[key],diff=a==null||b==null?"n.d.":((b-a)>0?"+":"")+(b-a).toFixed(1)+unit;
      row.append(makeEl("span",label+" · "+(a??"n.d.")+" → "+(b??"n.d.")),makeEl("strong",diff));root.append(row);
    }
    root.append(makeEl("p",c.note,"hint"));
    flash("Confronto completato. Numeri sperimentali, non valutazioni reali della rete.");
  }catch(e){flash(e.message,true);}
  finally{$("compareBtn").disabled=false;}
}
async function lookupTypicalTraffic(){
  const button=$("typicalTrafficBtn");
  button.disabled=true;
  $("typicalTrafficStatus").textContent="Recupero della previsione di traffico tipico...";
  try{
    const direction=$("mode").value==="wave_inbound"?"inbound":"outbound";
    const query=new URLSearchParams({day:$("day").value,hour:String($("hour").value),direction});
    const result=await api("/api/traffic/typical?"+query.toString());
    const m=(result.travel_time_typical_s/60).toFixed(1);
    const free=result.travel_time_freeflow_s==null?"n.d.":(result.travel_time_freeflow_s/60).toFixed(1);
    $("typicalTrafficStatus").textContent="TomTom · "+(direction==="inbound"?"verso Centro":"verso GRA")+
      " · stima tipica "+m+" min (senza congestione "+free+" min) · partenza "+
      result.prediction_departure+" · Itinerario del provider; NON è una misura dei veicoli e NON coincide con le OD miste di SUMO.";
  }catch(e){
    $("typicalTrafficStatus").textContent="TomTom non disponibile: "+e.message+" · I risultati SUMO restano utilizzabili.";
  }finally{button.disabled=false;}
}
async function runResearch(){
  $("researchBtn").disabled=true;
  state.engineChosen=true;
  $("researchStatus").textContent="Preparazione studio scientifico...";
  try{
    const status=await api("/api/sumo/status");
    if(!status.available)throw new Error("Motore SUMO non pronto: attendi la preparazione automatica");
    if($("mode").value==="manual" && !Object.keys(state.overrides).length){
      throw new Error("Scegli Onda verde → GRA o Onda verde → Centro, oppure imposta offset manuali");
    }
    const first=+$("seed").value;
    if(first<0)throw new Error("Il seed iniziale non può essere negativo");
    const runs=Number($("researchRuns").value);
    const workers=Number($("researchWorkers").value);
    if(!Number.isInteger(runs)||runs<2||runs>200)throw new Error("Numero di seed richiesto: da 2 a 200");
    if(!Number.isInteger(workers)||workers<1||workers>8)throw new Error("Numero di worker richiesto: da 1 a 8");
    if(first+runs-1>1000000)throw new Error("Riduci il primo seed: massimo 1.000.000");
    const experiment={scenario:params(),seeds:Array.from({length:runs},(_,i)=>first+i),workers};
    const job=await api("/api/research/jobs",{method:"POST",body:JSON.stringify(experiment)});
    const output=await waitForJob(job);
    const delay=output.summary.avg_delay_s;
    $("researchStatus").textContent="Studio completato · "+output.seeds.length+" coppie / "+output.parallel_workers+" processi · differenza ritardo medio: "+
      (delay.mean_delta===null?"n.d.":delay.mean_delta+" s")+" · "+
      (output.warnings.length?output.warnings.length+" avvertenze":"nessuna avvertenza")+
      " · Risultato teorico, non misurato sul campo.";
    const link=document.createElement("a");
    link.href="/api/research/jobs/"+encodeURIComponent(job.id)+"/download";
    link.download="salaria-research.zip";
    document.body.append(link);link.click();link.remove();
    flash("Studio multi-seed completato · ZIP con CSV, JSON e relazione metodologica.");
  }catch(e){
    $("researchStatus").textContent="Studio non eseguito: "+e.message;
    flash(e.message,true);
  }finally{$("researchBtn").disabled=false;}
}
function saveReport(){
  if(!state.simulation){flash("Esegui prima una simulazione.",true);return;}
  const report={parameters:params(),network:{source:state.network.source,quality:state.network.quality,length_m:state.network.length_m,signals:state.network.signals},results:state.simulation.metrics,comparison:state.comparison,limitations:state.simulation.limitations};
  const link=document.createElement("a");
  link.href=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:"application/json"}));
  link.download="salaria-experiment.json";link.click();setTimeout(()=>URL.revokeObjectURL(link.href),2000);
}
async function refreshOSM(){
  $("osmBtn").disabled=true;flash("Scaricamento OSM da Overpass; potrebbe essere temporaneamente non disponibile.");
  try{await api("/api/refresh-osm",{method:"POST"});await getNetwork();state.simulation=null;flash("OSM importato. Controlla percorso e semafori: dati non certificati.");await run();}
  catch(e){flash("Import non riuscito: "+e.message+". La demo resta disponibile.",true);}
  finally{$("osmBtn").disabled=false;}
}
async function getWeather(){
  try{
    const day=$("day").value, hour=Number($("hour").value);
    const data=await api("/api/weather/history?day="+encodeURIComponent(day));
    const entry=data.hours.findIndex(x=>x.slice(11,13)===String(hour).padStart(2,"0"));
    if(entry<0||data.precipitation_mm[entry]==null)throw new Error("Precipitazioni non presenti per l'ora selezionata");
    const rain=Number(data.precipitation_mm[entry]);
    $("rain").value=Math.min(20,rain);syncLabels();
    flash("Precipitazioni storiche da Open-Meteo: "+rain+" mm/h. Il traffico è ancora sintetico.");
  }catch(e){flash(e.message,true);}
}
function init(){
  const now=new Date();
  $("day").value=[now.getFullYear(),String(now.getMonth()+1).padStart(2,"0"),String(now.getDate()).padStart(2,"0")].join("-");
  syncLabels();setup3D();
  $("engine").addEventListener("change",async()=>{
    state.engineChosen=true;
    try{
      if($("engine").value==="sumo"){
        const status=await api("/api/sumo/status");
        if(!status.available){
          $("engine").value="preview";
          flash("La rete SUMO si sta preparando oppure non è disponibile. Usa la demo e riprova.",true);
          return;
        }
      }
      await getNetwork();
      await run();
    }catch(error){
      $("engine").value="preview";
      flash(error.message+" · Continua con la demo.",true);
      await getNetwork().catch(()=>{});
    }
  });
  $("setupSumoBtn").addEventListener("click",setupSumo);
  // Poll import readiness without requiring a manual refresh or terminal access.
  window.setInterval(()=>refreshSumoStatus().catch(e=>{
    $("sumoStatus").textContent="Verifica SUMO non disponibile: "+e.message;
  }),3000);
  $("segment").addEventListener("change",()=>getNetwork().then(run).catch(e=>flash(e.message,true)));
  $("runBtn").addEventListener("click",run);
  $("compareBtn").addEventListener("click",compare);
  $("researchBtn").addEventListener("click",runResearch);
  $("typicalTrafficBtn").addEventListener("click",lookupTypicalTraffic);
  $("osmBtn").addEventListener("click",refreshOSM);
  $("weatherBtn").addEventListener("click",getWeather);
  $("exportBtn").addEventListener("click",saveReport);
  $("playPause").addEventListener("click",()=>{state.playing=!state.playing;$("playPause").textContent=state.playing?"Ⅱ":"▶";});
  $("timeline").addEventListener("input",()=>{state.frame=+$("timeline").value;state.playing=false;$("playPause").textContent="▶";showFrame(state.frame);});
  window.addEventListener("resize",queueChart);
  getNetwork().then(run).then(()=>refreshSumoStatus()).catch(err=>flash(err.message,true));
}
init();
