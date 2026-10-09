"""SUMO/TraCI microscopic backend. No fabricated trajectories.

Requires a built OSM SUMO network and optional SUMO dependencies:
  pip install -e '.[sumo]'
  python scripts/bootstrap_sumo.py

All signal modifications retain existing R/Y/G conflict patterns and clearance
phases. Generated vehicle demand remains explicitly hypothetical.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
import math
from pathlib import Path
import random
import shutil
import statistics
import threading

from app.engine import demand_factor
from app.areas import network_path, area_from_network
from app.models import SimulationRequest
from app.network import ROOT, closest_fraction, current_network, extract_osm, lengths, meters, subset_network

NET_FILE = ROOT / "data" / "sumo" / "salaria.net.xml"
SUMO_RUN_LOCK = threading.Lock()


def _imports():
    try:
        import traci
        import sumolib
    except ImportError as exc:
        raise RuntimeError("SUMO Python modules missing: pip install -e '.[sumo]'") from exc
    return traci, sumolib


def status(area="salaria"):
    import importlib.util
    target = network_path(area)
    net_ready = target.is_file() and target.stat().st_size > 1024
    return {
        "installed": bool(shutil.which("sumo")),
        "netconvert": bool(shutil.which("netconvert")),
        "python_modules": bool(importlib.util.find_spec("traci") and importlib.util.find_spec("sumolib")),
        "network_ready": net_ready,
        "network_file": str(target),
        "area": area,
        "available": bool(shutil.which("sumo") and net_ready and importlib.util.find_spec("traci") and importlib.util.find_spec("sumolib")),
        "model": "SUMO microscopic TraCI (not calibrated against traffic counts)"
    }


def _real_geo(net):
    try:
        return net.getGeoProj() is not None and net.getLocationOffset() is not None
    except Exception:
        return False


def _to_latlon(net, x, y, geo):
    if geo:
        lon, lat = net.convertXY2LonLat(x, y)
        return [lat, lon]
    # Used ONLY for abstract/netgenerate smoke-test networks, never as real Rome coordinates.
    return [41.965 + y / 111_195, 12.505 + x/(111_195*.74)]


def _axis_from_sumo(net):
    """Construct an axis from name-tagged SUMO edges, retaining real edge shapes.

    Fallback to OSM cache only if SUMO graph extraction fails.
    """
    geo = _real_geo(net)
    elements=[]
    for edge in net.getEdges():
        if "salaria" not in (edge.getName() or "").lower():
            continue
        coords=edge.getShape()
        if len(coords)<2:
            continue
        # Endpoint nodes are connected across edge pieces; internal vertices are unique.
        ids=[edge.getFromNode().getID()]
        for j in range(1,len(coords)-1):
            ids.append(edge.getID()+":"+str(j))
        ids.append(edge.getToNode().getID())
        elements.append({"type":"way","nodes":ids,
                        "geometry":[{"lat":loc[0],"lon":loc[1]} for loc in
                                    (_to_latlon(net, x, y,geo) for x,y in coords)]})
    try:
        # netconvert collapses straight intermediate road nodes.
        path=extract_osm({"elements":elements},min_component_nodes=2)
        path["quality"]="SUMO roadway centerline derived from OSM import; signals from TLS controllers"
        path["source"]="sumo_osm"
        return path
    except ValueError:
        cached=current_network()
        if cached["source"]=="synthetic_demo":
            raise RuntimeError("SUMO network contains no continuous named Salaria axis; cannot overlay as real Rome")
        return {**cached,"source":"sumo_axis_from_cached_osm",
                "quality":"OSM cached corridor. SUMO roads/vehicles use network coordinates."}


@lru_cache(maxsize=12)
def load_network(segment="full", net_path=NET_FILE):
    _, sumolib=_imports()
    if not Path(net_path).is_file():
        raise RuntimeError("SUMO network missing; run python scripts/bootstrap_sumo.py first")
    net=sumolib.net.readNet(str(net_path),withInternal=False)
    geo=_real_geo(net)
    area=area_from_network(net_path)
    if area == "nord_est" and segment != "full":
        raise ValueError("For Roma Nord-Est select the full network")
    axis=subset_network(_axis_from_sumo(net),segment)
    regional = area == "nord_est"
    roadset=[]
    usable=[]
    for edge in net.getEdges():
        if edge.getFunction() == "internal":
            continue
        if not edge.allows("passenger"):
            continue
        shape=[_to_latlon(net,x,y,geo) for x,y in edge.getShape()]
        if not shape:
            continue
        if regional and "salaria" not in (edge.getName() or "").lower():
            # Avoid O(number of regional edges * axis vertices) GIS projection
            # on every local street: s is unused for regional cross-city OD.
            near, s = 1000.0, axis["length_m"] / 2
        else:
            near,s=closest_fraction(shape[len(shape)//2],axis["points"])
        if not regional and (near>340 or s<0 or s>axis["length_m"]):
            continue
        center = shape[len(shape)//2]
        usable.append({"id":edge.getID(),"s_m":s,"distance_m":near,
                       "lat":center[0],"lon":center[1],
                       "name":edge.getName() or "",
                       "length_m":edge.getLength()})
        if len(roadset)<(5500 if regional else 2000):
            roadset.append({"id":edge.getID(),"name":edge.getName() or "",
                            "points":shape, "width_m":max(3, min(18, 3*edge.getLaneNumber()))})
    signals=[]
    for tls in net.getTrafficLights():
        connections=tls.getConnections()
        if not connections:
            continue
        node=connections[0][0].getEdge().getToNode()
        lat,lon=_to_latlon(net,*node.getCoord(),geo)
        near,s=closest_fraction([lat,lon],axis["points"])
        if not regional and (near>130 or s<0 or s>axis["length_m"]):
            continue
        indices=sorted({linkidx for inlane,outlane,linkidx in connections
                        if "salaria" in (inlane.getEdge().getName() or "").lower()})
        signals.append({"id":tls.getID(),"s_m":round(s,1),"lat":lat,"lon":lon,
                        "source":"sumo_imported_tls","label":"SUMO controller "+tls.getID(),
                        "main_link_indices":indices,
                        "distance_from_axis_m":round(near,1)})
    signals.sort(key=lambda x:x["s_m"])
    return {**axis,"source":"sumo_osm","area":area,
            "area_bbox":(41.904,12.460,42.038,12.616) if regional else None,
            "regional":regional,
            "roads":roadset,"signals":signals,
            "geo_valid":geo,"usable_edges":usable,
            "quality":"SUMO net converted from OSM; signal programs are generated, not field-verified.",
            "warnings":["Traffic light controller locations and programs generated by netconvert; not official.",
                        "Demand, weekday factors and rain behaviour are hypothetical."]}


def rescale_phases(phases, main_index, green_s, cycle_s):
    """Return new phase durations or None if desired cycle cannot preserve clearances.

    Preserve *all states*, yellow/all-red durations and >=7 seconds for other
    green phases. The selected primary green gets exactly green_s seconds.
    """
    states=[p.state for p in phases]
    if not states or main_index<0 or main_index>=len(states):
        return None
    main=states[main_index]
    if "G" not in main and "g" not in main:
        return None
    other_green=[i for i,s in enumerate(states) if i!=main_index and ("G" in s or "g" in s)]
    fixed=[i for i in range(len(phases)) if i not in other_green and i!=main_index]
    # Safety envelope: preserve at least imported amber/all-red phase durations.
    fixed_total=sum(float(phases[i].duration) for i in fixed)
    remaining=cycle_s-green_s-fixed_total
    if not other_green:
        return None  # No alternative movement can absorb remainder safely.
    if remaining < 7*len(other_green):
        return None
    baseline=[max(7.,float(phases[i].duration)) for i in other_green]
    weights=[v/sum(baseline) for v in baseline]
    free=remaining-7*len(other_green)
    durations=[float(p.duration) for p in phases]
    durations[main_index]=float(green_s)
    for i,w in zip(other_green,weights):
        durations[i]=7.+free*w
    if any(v<=0 for v in durations):
        return None
    return durations


def _offset_phase(durations, offset_s):
    cycle=sum(durations)
    position=offset_s%cycle
    for i,d in enumerate(durations):
        if position<d:
            return i,max(.1,d-position)
        position-=d
    return 0,durations[0]


def _configure_lights(conn, net, request, info, warn):
    traci,_=_imports()
    configured=[]
    for sig in info["signals"]:
        tid=sig["id"]
        logic_list=conn.trafficlight.getAllProgramLogics(tid)
        active=conn.trafficlight.getProgram(tid)
        logic=next((l for l in logic_list if l.programID==active),None)
        if logic is None or not logic.phases:
            warn.append("No phase logic: "+tid);continue
        if any(getattr(p,"next",()) for p in logic.phases):
            warn.append("Non-linear phase graph kept unchanged: "+tid);continue
        links=conn.trafficlight.getControlledLinks(tid)
        main_idx=set()
        for idx,linkset in enumerate(links):
            for link in linkset or ():
                if not link:
                    continue
                try:
                    inedge=conn.lane.getEdgeID(link[0])
                    if "salaria" in (net.getEdge(inedge).getName() or "").lower():
                        main_idx.add(idx)
                except (KeyError, ValueError):
                    continue
        if not main_idx:
            warn.append("No identifiable Salaria approach: "+tid);continue
        score=[sum(1 for i in main_idx if i<len(p.state) and p.state[i] in "gG") for p in logic.phases]
        primary=max(range(len(score)),key=lambda k:score[k])
        if score[primary]==0:
            warn.append("No green phase for Salaria approach: "+tid);continue
        override=request.overrides.get(tid)
        green=override.green_s if override and override.green_s else request.green_s
        durations=rescale_phases(logic.phases,primary,green,request.cycle_s)
        if durations is None:
            warn.append("Requested timings infeasible; kept original at "+tid);continue
        phases=[traci.trafficlight.Phase(duration=d,state=p.state,
                                        minDur=d,maxDur=d) for d,p in zip(durations,logic.phases)]
        program=traci.trafficlight.Logic("rome_lab",0,0,phases)
        conn.trafficlight.setProgramLogic(tid,program)
        if request.mode=="wave_outbound":
            off=round(-sig["s_m"]/(request.speed_kmh/3.6))%request.cycle_s
        elif request.mode=="wave_inbound":
            off=round(-(info["length_m"]-sig["s_m"])/(request.speed_kmh/3.6))%request.cycle_s
        else:
            off=override.offset_s % request.cycle_s if override else 0
        index,left=_offset_phase(durations,off)
        conn.trafficlight.setPhase(tid,index)
        conn.trafficlight.setPhaseDuration(tid,left)
        configured.append({"id":tid,"s_m":sig["s_m"],"green_s":green,
                           "cycle_s":request.cycle_s,"offset_s":off,
                           "phase_count":len(phases),"label":sig["label"]})
    return configured


def _route_catalog(conn, net, edgeinfo, length_m, seed, warnings, regional=False):
    rng=random.Random(seed)
    main=[e for e in edgeinfo if "salaria" in e["name"].lower() and e["distance_m"]<110]
    side=[e for e in edgeinfo if "salaria" not in e["name"].lower() and e["distance_m"]<320]
    # Restrict origin/destination to opposite corridor ends for through traffic.
    low=[e for e in main if e["s_m"]<length_m*.28]
    high=[e for e in main if e["s_m"]>length_m*.72]
    near_side=[e for e in side if length_m*.1<e["s_m"]<length_m*.9]
    def discover(starts, ends, group, attempts=45):
        pairs=[]
        if not starts or not ends:
            return pairs
        for _ in range(attempts):
            a=rng.choice(starts)["id"];b=rng.choice(ends)["id"]
            if a==b:continue
            try:
                # Local Dijkstra routing avoids TraCI findRoute protocol
                # mismatches between older distro binaries and newer clients.
                route,cost=net.getShortestPath(net.getEdge(a),net.getEdge(b),
                                               vClass="passenger")
                if not route or cost < 220:
                    continue
                edges=tuple(edge.getID() for edge in route)
                if edges not in pairs:pairs.append(edges)
                if len(pairs)>=8:break
            except Exception:
                continue
        if not pairs:
            warnings.append("No routable OD pair found for "+group)
        result=[]
        for i,route in enumerate(pairs):
            rid=f"lab_{group}_{i}"
            conn.route.add(rid,list(route))
            result.append(rid)
        return result
    routes = {
        "outbound":discover(low,high,"outbound"),
        "inbound":discover(high,low,"inbound"),
        "side_in":discover(near_side,main,"side_in"),
        "side_out":discover(main,near_side,"side_out"),
        "side_cross":discover(near_side,near_side,"side_cross")
    }
    if regional:
        # Additional genuine cross-city SUMO routes. They are modelled OD,
        # not observed commuter counts or provider-measured trips.
        candidate=[e for e in edgeinfo if e.get("lat") is not None
                   and e["length_m"]>=30]
        def slice_by(name, reverse=False):
            vals=sorted(candidate,key=lambda e:e[name],reverse=reverse)
            return vals[:max(1,min(350,len(vals)//5))]
        south=slice_by("lat")
        north=slice_by("lat",True)
        west=slice_by("lon")
        east=slice_by("lon",True)
        routes.update({
            "regional_ns":discover(north,south,"regional_ns",attempts=85),
            "regional_sn":discover(south,north,"regional_sn",attempts=85),
            "regional_ew":discover(east,west,"regional_ew",attempts=85),
            "regional_we":discover(west,east,"regional_we",attempts=85),
        })
    return routes


def _states(conn,signals):
    result=[]
    for sig in signals:
        state=conn.trafficlight.getRedYellowGreenState(sig["id"])
        indices=sig.get("main_link_indices",[])
        # Show Salaria approach, not a simultaneous cross-street green phase.
        selected=[state[i] for i in indices if 0<=i<len(state)]
        if any(c in "gG" for c in selected):
            result.append("green")
        elif any(c in "yY" for c in selected):
            result.append("amber")
        else:
            result.append("red")
    return result


def _run(request: SimulationRequest, *, net_path=NET_FILE, frames=True, progress=None):
    if request.area == "nord_est" and request.segment != "full":
        raise ValueError("Roma Nord-Est requires the complete geographic region")
    traci,sumolib=_imports()
    info=load_network(request.segment,net_path)
    if not info["geo_valid"]:
        # Abstract netgenerate networks may be used for internal CI tests,
        # but must never be silently presented as real Rome.
        raise RuntimeError("SUMO network has no geographic OSM projection; import the real OSM road network.")
    if not set(request.overrides).issubset({s["id"] for s in info["signals"]}):
        raise ValueError("Signal IDs do not match current SUMO network")
    net=sumolib.net.readNet(str(net_path))
    command=[shutil.which("sumo") or "sumo", "-n",str(net_path),
             "--step-length","1","--seed",str(request.seed),
             "--no-step-log","true","--duration-log.disable","true",
             "--time-to-teleport","120","--collision.action","warn"]
    conn=None
    warnings=[]
    try:
        traci.start(command, label="salaria_lab")
        conn=traci.getConnection("salaria_lab")
        conn.vehicletype.copy("DEFAULT_VEHTYPE","lab_passenger")
        rainfactor=max(.62,1-.035*request.rain_mm_h)
        maxspeed=request.speed_kmh/3.6*rainfactor
        conn.vehicletype.setMaxSpeed("lab_passenger",maxspeed)
        conn.vehicletype.setDecel("lab_passenger",max(1.6,3.8-.1*request.rain_mm_h))
        conn.vehicletype.setMinGap("lab_passenger",2.5+.2*request.rain_mm_h)
        # Actual SUMO-calculated braking, junction conflicts, turns and queues.
        adjusted=_configure_lights(conn,net,request,info,warnings)
        routes=_route_catalog(conn,net,info["usable_edges"],info["length_m"],request.seed,warnings,regional=info["regional"])
        if not routes["outbound"] and not routes["inbound"] and not routes["side_in"]:
            raise RuntimeError("No routable trips found. Check OSM/SUMO road connectivity.")
        dt=1
        rng=random.Random(request.seed)
        vehicle_id=0
        born={}
        trip_freeflow={}
        actual_delays=[]
        old_stopped=set()
        stop_counter=Counter()
        arrivals=[]
        stop_trips=[]
        queue_data=[]
        captured=[]
        max_queue=0
        stopped_veh_s=0
        depart_total=0
        waiting_queue=0
        observed = None
        if request.traffic_source == "hourly_counts":
            from app.observations import demand_for
            observed = demand_for(request.day, request.hour)
            factor = 1.0
            out_rate = observed["outbound_vph"] / 3600
            in_rate = observed["inbound_vph"] / 3600
            warnings.append("Main corridor volumes derive from imported counts/estimates; side-road demand, turning shares and signal timing still hypothetical.")
        else:
            factor = demand_factor(request.day, request.hour)
            out_rate = in_rate = request.demand_vph * factor / 3600
        side_rate = (out_rate + in_rate) / 2
        rates={"outbound":out_rate,"inbound":in_rate,
               "side_in":side_rate*request.side_traffic_share*.5,
               "side_out":side_rate*request.side_traffic_share*.5,
               "side_cross":side_rate*request.side_traffic_share*.35}
        if info["regional"]:
            # Synthetic demand spread across four additional road directions;
            # not a faithful reconstruction of real regional OD matrices.
            rates.update({name: (out_rate+in_rate)*.18 for name in
                          ("regional_ns","regional_sn","regional_ew","regional_we")})
            warnings.append("Regional OD flows are synthetic, distributed across extra north/south/east/west routes.")
        budgets={k:0. for k in rates}
        seconds=request.duration_min*60
        for tick in range(seconds+1):
            for group,rate in rates.items():
                budgets[group]+=rate
                while budgets[group]>=1:
                    budgets[group]-=1
                    available=routes[group]
                    if not available:continue
                    rid=rng.choice(available)
                    vid="vehicle_"+str(vehicle_id)
                    vehicle_id+=1
                    try:
                        conn.vehicle.add(vid,rid,typeID="lab_passenger",depart="now",
                                         departLane="best",departSpeed="max")
                        born[vid]=tick
                        # Every OD route has its own free-flow benchmark.
                        route_edges=conn.route.getEdges(rid)
                        route_length=sum(net.getEdge(e).getLength() for e in route_edges
                                         if net.hasEdge(e))
                        trip_freeflow[vid]=route_length/maxspeed
                        depart_total+=1
                    except traci.exceptions.TraCIException:
                        waiting_queue+=1
            conn.simulationStep()
            current=set(conn.vehicle.getIDList())
            stopped=set()
            rows=[]
            for vid in current:
                speed=conn.vehicle.getSpeed(vid)
                if speed<.8:
                    stopped.add(vid)
                    stopped_veh_s+=1
                    if vid not in old_stopped:
                        stop_counter[vid]+=1
                if frames and tick%2==0 and len(rows)<1200:
                    x,y=conn.vehicle.getPosition(vid)
                    lon,lat=net.convertXY2LonLat(x,y)
                    numeric=int(vid.split("_")[-1])
                    rows.append([numeric,round(lon,7),round(lat,7),
                                 round(speed*3.6,1),round(conn.vehicle.getAngle(vid),1)])
            old_stopped=stopped
            for vid in conn.simulation.getArrivedIDList():
                if vid in born:
                    travel=tick-born[vid]+1
                    arrivals.append(travel)
                    actual_delays.append(max(0.,travel-trip_freeflow.pop(vid,travel)))
                    stop_trips.append(stop_counter.pop(vid,0))
                    born.pop(vid,None)
            max_queue=max(max_queue,len(stopped))
            if tick%5==0:
                queue_data.append([tick,len(stopped)])
            if frames and tick%2==0:
                captured.append({"t":tick,"cars":rows,"signals":_states(conn,info["signals"])})
            if progress and tick%20==0:
                progress(min(.99,tick/max(1,seconds)),f"SUMO {tick}/{seconds} s · {depart_total} departures")
        mean=statistics.mean(arrivals) if arrivals else None
        freeflow=statistics.mean([a-d for a,d in zip(arrivals,actual_delays)]) if arrivals else info["length_m"]/maxspeed
        metrics={
            "inserted":depart_total, "completed":len(arrivals),
            "still_on_road":len(born),"upstream_waiting":waiting_queue,
            "avg_travel_s":round(mean,1) if mean is not None else None,
            "p95_travel_s":sorted(arrivals)[math.ceil(.95*len(arrivals))-1] if arrivals else None,
            "freeflow_s":round(freeflow,1),
            "avg_delay_s":round(statistics.mean(actual_delays),1) if arrivals else None,
            "mean_stops_per_trip":round(statistics.mean(stop_trips),2) if stop_trips else None,
            "stopped_vehicle_seconds":stopped_veh_s,"max_queued_vehicles":max_queue,
            "demand_factor":round(factor,2),"effective_speed_kmh":round(maxspeed*3.6,1),
            "controlled_lights":len(adjusted),"signals_total":len(info["signals"]),
            "traffic_source":"imported_hourly_directional" if observed else "synthetic_hourly_factor",
            "demand_evidence":observed,
            "area":request.area,
            "main_outbound_vph":round(out_rate*3600,2),
            "main_inbound_vph":round(in_rate*3600,2)
        }
        return {
            "engine":"sumo","network":info["source"],"length_m":info["length_m"],
            "signals":adjusted,"all_signals":info["signals"],
            "frames":captured,"queue_series":queue_data,"metrics":metrics,"warnings":warnings,
            "limitations":[
                "Routes are generated from hypothetical O-D pairs, not measured origin-destination matrices.",
                "Imported main corridor counts do not verify side-street and turning shares.",
                "Traffic light programs are generated by netconvert, not measured control-room timing.",
                "Rain parameter changes driving behaviour by assumed speed, deceleration and gap rules.",
                "Turn movements and side-road conflicts are simulated only within the imported OSM/SUMO network.",
                "Trip KPI includes completed side-road trips; delays use each route's own length divided by assumed free-flow speed."
            ],
            "route_groups":{k:len(v) for k,v in routes.items()}
        }
    finally:
        if conn is not None:
            conn.close(False)


def run_sumo(request: SimulationRequest, *, net_path=None, frames=True, progress=None):
    if net_path is None:
        net_path=network_path(request.area)
    if not SUMO_RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("SUMO engine busy; a separate simulation is already running")
    try:
        return _run(request,net_path=net_path,frames=frames,progress=progress)
    finally:
        SUMO_RUN_LOCK.release()
