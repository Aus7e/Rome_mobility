/* Map-first Rome Mobility view: Leaflet is bundled locally.
   Street tiles are © OpenStreetMap contributors; no live-traffic overlay. */
let map, roadLayer, signalLayer, carLayer, startMarker, endMarker;
let markerBySignal = new Map();
let signalVisible = true;
let currentNetwork = null;
let carMarkers = new Map();
let tiled = false;
let canvasRenderer;

const $ = (id) => document.getElementById(id);
const L = () => window.L;

function latLonAt(distance) {
  const pts = currentNetwork?.points;
  if (!pts || pts.length === 0) return [41.971, 12.509];
  if (pts.length === 1) return pts[0];
  const length = currentNetwork.length_m || 1;
  let previous = 0;
  const clamped = Math.max(0, Math.min(1, distance / length));
  const lat0=pts[0][0];
  const cos=Math.cos(lat0 * Math.PI / 180);
  const cumulative = [0];
  for(let i=1;i<pts.length;i++) {
    const a=pts[i-1], b=pts[i];
    cumulative.push(cumulative[i-1]+Math.hypot((b[0]-a[0])*111195,(b[1]-a[1])*111195*cos));
  }
  const target=clamped*cumulative.at(-1);
  for(let i=1;i<cumulative.length;i++) {
    if(target<=cumulative[i]) {
      const fraction=(target-cumulative[i-1])/Math.max(1e-8,cumulative[i]-cumulative[i-1]);
      return [pts[i-1][0]+(pts[i][0]-pts[i-1][0])*fraction,
              pts[i-1][1]+(pts[i][1]-pts[i-1][1])*fraction];
    }
  }
  return pts.at(-1);
}

function labelIcon(text, className) {
  return L().divIcon({className:"place-marker "+className,
    html:'<span class="place-marker-dot"></span><span class="place-marker-text">'+text+'</span>',
    iconAnchor:[10,12]});
}
function signalDetails(signal, match, inventory) {
  const box=document.createElement("div");
  box.className="map-popup";
  const title=document.createElement("strong");
  title.textContent=signal.label || signal.id;
  box.append(title);
  const note=document.createElement("p");
  note.textContent=match ? "Abbinamento per distanza, non verificato sul campo" :
    "Controllore generato da SUMO / non verificato";
  box.append(note);
  if(match) {
    const record=inventory?.osm_candidates?.find(p=>p.id===match.osm_id);
    if(record?.source_url) {
      const link=document.createElement("a");
      link.href=record.source_url;
      link.target="_blank";link.rel="noopener noreferrer";
      link.textContent="Apri nodo OpenStreetMap ↗";
      box.append(link);
    }
  }
  return box;
}

export function setupStreetMap(onSignalClick) {
  if(!window.L) {
    $("mapTileStatus").textContent="Mappa non disponibile: libreria cartografica non caricata.";
    return;
  }
  map=L().map("streetMap",{
    center:[41.970,12.509], zoom:13, minZoom:11, maxZoom:19,
    zoomControl:false, preferCanvas:true, scrollWheelZoom:true,
    attributionControl:true
  });
  canvasRenderer=L().canvas({padding:0.25});
  const url="https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  const tiles=L().tileLayer(url,{
    maxZoom:19, maxNativeZoom:19,
    attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
    referrerPolicy:"strict-origin-when-cross-origin"
  }).addTo(map);
  tiles.on("tileload",()=>{
    if(!tiled){tiled=true;$("mapTileStatus").hidden=true;}
  });
  tiles.on("tileerror",()=>{
    if(!tiled){
      $("mapTileStatus").hidden=false;
      $("mapTileStatus").textContent="Sfondo cartografico non raggiungibile: tracciato e semafori restano visibili.";
    }
  });
  roadLayer=L().layerGroup().addTo(map);
  signalLayer=L().layerGroup().addTo(map);
  carLayer=L().layerGroup().addTo(map);
  $("mapZoomIn").addEventListener("click",()=>map.zoomIn());
  $("mapZoomOut").addEventListener("click",()=>map.zoomOut());
  $("fitRouteBtn").addEventListener("click",fitStreetRoute);
  $("signalsToggleBtn").addEventListener("click",()=>{
    signalVisible=!signalVisible;
    if(signalVisible) signalLayer.addTo(map);
    else map.removeLayer(signalLayer);
    $("signalsToggleBtn").setAttribute("aria-pressed",String(signalVisible));
    $("signalsToggleBtn").textContent=signalVisible?"🚦 Semafori":"🚦 Mostra semafori";
  });
  map.on("click",()=>{ $("searchResults").hidden=true; });
  map.on("popupclose",()=>{});
  map.__onSignalClick=onSignalClick;
}

export function updateStreetNetwork(network, inventory) {
  currentNetwork=network;
  if(!map || !network?.points?.length)return;
  roadLayer.clearLayers();
  signalLayer.clearLayers();
  carLayer.clearLayers();
  markerBySignal.clear();
  carMarkers.clear();
  const line=network.points.map(p=>[p[0],p[1]]);
  const isApproximate=network.source==="synthetic_demo";
  const halo=L().polyline(line,{color:isApproximate?"#dda154":"#ffffff",
    weight:13,opacity:0.88,lineJoin:"round",interactive:false});
  const route=L().polyline(line,{color:isApproximate?"#e5a154":"#3478f6",
    weight:7,opacity:0.96,lineJoin:"round"});
  roadLayer.addLayer(halo);roadLayer.addLayer(route);
  route.bindTooltip(isApproximate?"Percorso indicativo, non verificato":"Via Salaria · asse da OSM/SUMO",
    {sticky:true});
  // Sparse cross-street context already exists in SUMO network.
  for(const road of (network.roads || []).slice(0,network.regional?4500:450)){
    if(!road.points || road.points.length<2)continue;
    roadLayer.addLayer(L().polyline(road.points,{
      color:"#8799ac",weight:Math.min(5,Math.max(2,road.width_m/4)),
      opacity:0.48,interactive:false
    }));
  }
  if(startMarker)roadLayer.removeLayer(startMarker);
  if(endMarker)roadLayer.removeLayer(endMarker);
  startMarker=L().marker(line[0],{icon:labelIcon("Prati Fiscali","start")}).addTo(roadLayer);
  endMarker=L().marker(line.at(-1),{icon:labelIcon("GRA","end")}).addTo(roadLayer);
  const matching=inventory?.matches||[];
  for(const signal of network.signals||[]) {
    if(!Number.isFinite(signal.lat) || !Number.isFinite(signal.lon))continue;
    const match=matching.find(m=>m.sumo_tls_id===signal.id);
    const marker=L().circleMarker([signal.lat,signal.lon],{
      radius:10,weight:3,color:"#ffffff",
      fillColor:match?"#18a15f":"#f59b36",fillOpacity:1,
      renderer:canvasRenderer
    }).addTo(signalLayer);
    marker.bindPopup(signalDetails(signal,match,inventory));
    marker.bindTooltip(signal.label||"Semaforo",{direction:"top"});
    marker.on("click",()=>{
      map.__onSignalClick?.(signal);
    });
    markerBySignal.set(signal.id,marker);
  }
  fitStreetRoute();
  $("routeDistance").textContent=(network.length_m/1000).toFixed(1)+" km";
  $("routeSource").textContent=network.regional?"Roma Nord-Est · rete OSM e traffico simulato non calibrato":isApproximate?
    "Tracciato indicativo · non è una mappa dei semafori reali":
    "Tracciato OSM · semafori non certificati sul campo";
}

export function showRegionalStudyExtent(){
  if(!map)return;
  roadLayer.clearLayers();
  signalLayer.clearLayers();
  carLayer.clearLayers();
  currentNetwork=null;
  carMarkers.clear();
  markerBySignal.clear();
  const bounds=L().latLngBounds([[41.904,12.460],[42.038,12.616]]);
  roadLayer.addLayer(L().rectangle(bounds,{color:"#2470d8",weight:2,fillColor:"#4f9cf5",
                                       fillOpacity:0.08,dashArray:"6 5"}));
  map.fitBounds(bounds.pad(0.08),{animate:false});
  $("routeDistance").textContent="Area estesa";
}
export function fitStreetRoute(){
  if(!map||!currentNetwork?.points?.length)return;
  const bounds=L().latLngBounds(currentNetwork.points.map(p=>[p[0],p[1]]));
  if(currentNetwork.regional && currentNetwork.area_bbox){
    const [south,west,north,east]=currentNetwork.area_bbox;
    map.fitBounds([[south,west],[north,east]],{animate:false,padding:[25,25]});
    return;
  }
  if(bounds.isValid())map.fitBounds(bounds.pad(0.15),{
    paddingTopLeft:[30,130],paddingBottomRight:[30,90],maxZoom:15,
    animate:false
  });
}

export function focusStreetSignal(signal){
  if(!map)return;
  map.setView([signal.lat,signal.lon],Math.max(16,map.getZoom()),{animate:true});
  const marker=markerBySignal.get(signal.id);
  if(marker && signalVisible)marker.openPopup();
}

export function searchRoutePoints(query){
  if(!currentNetwork)return [];
  const needle=query.trim().toLocaleLowerCase("it");
  if(!needle)return [];
  const entries=[
    {type:"start",label:"Prati Fiscali · inizio percorso",point:currentNetwork.points[0]},
    {type:"end",label:"Grande Raccordo Anulare · fine percorso",point:currentNetwork.points.at(-1)}
  ];
  for(const signal of currentNetwork.signals||[]){
    entries.push({type:"signal",label:signal.label||signal.id,signal,
      point:[signal.lat,signal.lon]});
  }
  return entries.filter(x=>(x.label+" "+(x.signal?.id||"")).toLocaleLowerCase("it")
    .includes(needle)).slice(0,8);
}
export function goToSearchResult(result){
  if(!map)return;
  if(result.signal)focusStreetSignal(result.signal);
  else map.setView(result.point,16,{animate:true});
}
export function invalidateStreetMap(){
  if(map)setTimeout(()=>map.invalidateSize({pan:false}),50);
}
export function renderStreetFrame(frame, engine){
  if(!map||!frame||!currentNetwork)return;
  const seen=new Set();
  // Canvas-based car circles. Cap visualized cars; metrics still use ALL cars.
  const selection=frame.cars.length<=350?frame.cars:frame.cars.filter((_,i)=>i%Math.ceil(frame.cars.length/350)===0);
  for(const row of selection){
    const id=row[0];seen.add(id);
    let point,speed;
    if(engine==="sumo"){
      // SUMO rows: [vehicleId, lon, lat, speedKmH, bearing]
      point=[row[2],row[1]];speed=row[3];
    }else{
      // Demo rows: [id, direction, lane, pathDistance, speed]
      const distance=row[1]===0?row[3]:currentNetwork.length_m-row[3];
      point=latLonAt(distance);speed=row[4];
    }
    if(!point.every(Number.isFinite))continue;
    const color=speed<8?"#df5b5b":speed<24?"#e9a63f":"#2176ff";
    let marker=carMarkers.get(id);
    if(!marker){
      marker=L().circleMarker(point,{
        radius:3.2,weight:1,color:"#ffffff",fillColor:color,fillOpacity:0.95,
        interactive:false,renderer:canvasRenderer
      }).addTo(carLayer);
      carMarkers.set(id,marker);
    }else{marker.setLatLng(point);marker.setStyle({fillColor:color});}
  }
  for(const [id,marker] of carMarkers){
    if(!seen.has(id)){carLayer.removeLayer(marker);carMarkers.delete(id);}
  }
}
