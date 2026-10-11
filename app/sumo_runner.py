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
from app.simulation_quality import drain_deadline
from app.areas import AREAS, network_path, area_from_network
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


def _axis_from_sumo(net, area="salaria"):
    """Choose an OSM-derived focus corridor for the selected study zone.

    Regional movement is simulated on *all* imported vehicle edges, not
    only along this reference/visualization polyline.
    """
    geo=_real_geo(net)
    candidate_names=AREAS[area]["focus_roads"]
    named={}
    by_name={}
    for edge in net.getEdges():
        if edge.getFunction()=="internal" or not edge.allows("passenger"):
            continue
        coords=edge.getShape()
        if len(coords)<2:continue
        name=(edge.getName() or "").lower().strip()
        if not name:continue
        by_name.setdefault(name,[]).append(edge)
        for term in candidate_names:
            if term in name:named.setdefault(term,[]).append(edge)
    def build(edges,focus):
        elements=[]
        for edge in edges:
            coords=edge.getShape()
            ids=[edge.getFromNode().getID()]
            ids += [edge.getID()+":"+str(j) for j in range(1,len(coords)-1)]
            ids.append(edge.getToNode().getID())
            elements.append({
                "type":"way","nodes":ids,
                "geometry":[{"lat":lat,"lon":lon} for lat,lon in
                     (_to_latlon(net,x,y,geo) for x,y in coords)],
            })
        result=extract_osm({"elements":elements},min_component_nodes=2)
        result["quality"]="SUMO reference corridor derived from local OSM streets; timings unverified"
        result["source"]="sumo_osm"
        result["focus_road"]=focus
        return result
    for term in candidate_names:
        if term not in named:continue
        try:
            result=build(named[term],term)
            if result["length_m"]>100:return result
        except ValueError:
            continue
    # Never use cached Salaria geometry for an unrelated Rome zone.
    for name,edges in sorted(by_name.items(),
                            key=lambda item:sum(e.getLength() for e in item[1]),
                            reverse=True)[:15]:
        try:return build(edges,name)
        except ValueError:continue
    raise RuntimeError(f"No connected named road for the {area} SUMO network")


@lru_cache(maxsize=24)
def load_network(segment="full", net_path=NET_FILE):
    _, sumolib=_imports()
    if not Path(net_path).is_file():
        raise RuntimeError("SUMO network missing; run python scripts/bootstrap_sumo.py first")
    net=sumolib.net.readNet(str(net_path),withInternal=False)
    geo=_real_geo(net)
    area=area_from_network(net_path)
    if area != "salaria" and segment != "full":
        raise ValueError("Regional Rome zones require the entire area")
    axis=subset_network(_axis_from_sumo(net,area),segment)
    regional = area != "salaria"
    focus_roads=AREAS[area]["focus_roads"]
    focus=axis.get("focus_road",focus_roads[0])
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
        if regional and focus not in (edge.getName() or "").lower():
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
        if len(roadset)<(3200 if regional else 2000):
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
                        if focus in (inlane.getEdge().getName() or "").lower()})
        if regional and not indices:
            indices=sorted({linkidx for inlane,outlane,linkidx in connections})
        signals.append({"id":tls.getID(),"s_m":round(s,1),"lat":lat,"lon":lon,
                        "source":"sumo_imported_tls","label":"SUMO controller "+tls.getID(),
                        "main_link_indices":indices,
                        "distance_from_axis_m":round(near,1)})
    signals.sort(key=lambda x:x["s_m"])
    return {**axis,"source":"sumo_osm","area":area,
            "area_name":AREAS[area]["name"],"focus_roads":list(focus_roads),
            "area_bbox":AREAS[area]["bbox"],
            "geometry_role":"regional_reference_corridor" if regional else "salaria_main_axis",
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


def _is_salaria_approach(net, edge, axis):
    """Prefer a named Salaria edge; geometric alignment is qualified fallback."""
    if "salaria" in (edge.getName() or "").lower():
        return True
    shape=edge.getShape()
    if len(shape)<2 or edge.getLength()<8:
        return False
    # Cross roads may end right on the axis; require *both* endpoints near
    # the corridor AND sufficient displacement parallel to the axis.
    try:
        a=net.convertXY2LonLat(*shape[0])
        b=net.convertXY2LonLat(*shape[-1])
        near_a,sa=closest_fraction([a[1],a[0]],axis)
        near_b,sb=closest_fraction([b[1],b[0]],axis)
    except (TypeError,ValueError,IndexError):
        return False
    return max(near_a,near_b)<=25 and abs(sb-sa)>=.65*edge.getLength()


def _signal_offset(request, sig, length_m, actual_cycle):
    speed=request.speed_kmh/3.6
    override=request.overrides.get(sig["id"])
    if request.mode=="wave_outbound":
        return round(-sig["s_m"]/speed)%round(actual_cycle)
    if request.mode=="wave_inbound":
        return round(-(length_m-sig["s_m"])/speed)%round(actual_cycle)
    return (override.offset_s%round(actual_cycle)) if override else 0


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
                if not link:continue
                try:
                    incoming=conn.lane.getEdgeID(link[0])
                    edge=net.getEdge(incoming)
                    if info["regional"]:
                        if any(term in (edge.getName() or "").lower()
                               for term in info["focus_roads"]):
                            main_idx.add(idx)
                    elif _is_salaria_approach(net,edge,info["points"]):
                        main_idx.add(idx)
                except (KeyError,ValueError):
                    continue
        if not main_idx and info["regional"]:
            approaches={}
            for idx,linkset in enumerate(links):
                for link in linkset or ():
                    if not link:continue
                    try:
                        edge=net.getEdge(conn.lane.getEdgeID(link[0]))
                        if edge.allows("passenger"):
                            approaches.setdefault(edge.getID(),set()).add(idx)
                    except (KeyError,ValueError):pass
            if approaches:
                main_idx=max(approaches.values(),key=len)
        if not main_idx:
            warn.append("No controllable vehicle approach: "+tid);continue
        score=[sum(1 for i in main_idx if i<len(p.state) and p.state[i] in "gG")
               for p in logic.phases]
        primary=max(range(len(score)),key=lambda k:score[k])
        if score[primary]==0:
            warn.append("No green phase for Salaria approach: "+tid);continue
        override=request.overrides.get(tid)
        green=override.green_s if override and override.green_s else request.green_s
        durations=rescale_phases(logic.phases,primary,green,request.cycle_s)
        tuning_kind="cycle_green_offset"
        if durations is None:
            # Safe fallback: alter *only* the starting phase of the intact
            # imported controller. Never fabricate illegal green splits or
            # shorten clearance phases to force a requested cycle.
            durations=[float(p.duration) for p in logic.phases]
            tuning_kind="offset_only"
            if request.mode=="manual" and not (override and override.offset_s):
                warn.append("Requested timings infeasible; original schedule preserved at "+tid)
                continue
            if sum(durations)<=0:
                warn.append("Invalid imported phase durations: "+tid);continue
            warn.append("Cycle/green infeasible; adjusting offset ONLY at "+tid)
        else:
            # Preserve yellow, red, permitted movements and existing
            # phase ordering. Only feasible green durations are modified.
            phases=[traci.trafficlight.Phase(duration=d,state=p.state,
                                            minDur=d,maxDur=d) for d,p in zip(durations,logic.phases)]
            program=traci.trafficlight.Logic("rome_lab",0,0,phases)
            conn.trafficlight.setProgramLogic(tid,program)
        actual_cycle=sum(durations)
        offset=_signal_offset(request,sig,info["length_m"],actual_cycle)
        index,left=_offset_phase(durations,offset)
        conn.trafficlight.setPhase(tid,index)
        conn.trafficlight.setPhaseDuration(tid,left)
        configured.append({
            "id":tid,"s_m":sig["s_m"],
            "green_s":green if tuning_kind=="cycle_green_offset" else None,
            "requested_cycle_s":request.cycle_s,
            "cycle_s":round(actual_cycle,2),"offset_s":offset,
            "phase_count":len(durations),"label":sig["label"],
            "tuning_kind":tuning_kind,
        })
    return configured



def _route_catalog(conn, net, edgeinfo, length_m, seed, warnings, regional=False, focus_road="salaria"):
    rng=random.Random(seed)
    main=[e for e in edgeinfo if focus_road in e["name"].lower()
          and e["distance_m"]<150]
    side=[e for e in edgeinfo if focus_road not in e["name"].lower()
          and (regional or e["distance_m"]<320)]
    # Restrict origin/destination to opposite corridor ends for through traffic.
    low=[e for e in main if e["s_m"]<length_m*.28]
    high=[e for e in main if e["s_m"]>length_m*.72]
    near_side=[e for e in side if
               (regional and e["length_m"]>=30)
               or (not regional and length_m*.1<e["s_m"]<length_m*.9)]
    if regional and len(near_side)>400:
        near_side=random.Random(seed+819).sample(near_side,400)
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
    if request.area != "salaria" and request.segment != "full":
        raise ValueError("Regional Rome zones require the entire study area")
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
             # Teleportation makes a jam look like successfully completed journeys.
             # Disable it and use explicit end-of-run censoring instead.
             "--time-to-teleport","-1","--collision.action","warn"]
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
        routes=_route_catalog(conn,net,info["usable_edges"],info["length_m"],
                              request.seed,warnings,regional=info["regional"],
                              focus_road=info.get("focus_road","salaria"))
        if not any(routes.values()):
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
            from app.observations import demand_for_area
            observed = demand_for_area(request.day, request.hour, request.area)
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
        max_sim_second=drain_deadline(seconds,request.drain_max_s)
        actual_end_s=0
        # Inject for precisely 'seconds' steps, then drain the existing traffic.
        for tick in range(max_sim_second):
            actual_end_s=tick+1
            if tick < seconds:
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
                progress(min(.99,(tick+1)/max_sim_second),
                         f"SUMO {tick+1}/{max_sim_second} s · {depart_total} injected"
                         + (" · clearing remaining trips" if tick>=seconds else ""))
            if tick+1 >= seconds and not current and conn.simulation.getMinExpectedNumber()==0:
                break
        # Simulation completion is only measured after the drain phase.
        mean=statistics.mean(arrivals) if arrivals else None
        freeflow=statistics.mean([a-d for a,d in zip(arrivals,actual_delays)]) if arrivals else info["length_m"]/maxspeed
        metrics={
            "inserted":depart_total, "completed":len(arrivals),
            "injection_end_s":seconds,"simulation_end_s":actual_end_s,
            "drain_max_s":request.drain_max_s,
            "drain_used_s":max(0,actual_end_s-seconds),
            "drain_timed_out":bool(born and actual_end_s>=max_sim_second),
            "still_on_road":len(born),"upstream_waiting":waiting_queue,
            "avg_travel_s":round(mean,1) if mean is not None else None,
            "p95_travel_s":sorted(arrivals)[math.ceil(.95*len(arrivals))-1] if arrivals else None,
            "freeflow_s":round(freeflow,1),
            "avg_delay_s":round(statistics.mean(actual_delays),1) if arrivals else None,
            "mean_stops_per_trip":round(statistics.mean(stop_trips),2) if stop_trips else None,
            "stopped_vehicle_seconds":stopped_veh_s,"max_queued_vehicles":max_queue,
            "demand_factor":round(factor,2),"effective_speed_kmh":round(maxspeed*3.6,1),
            "controlled_lights":len(adjusted),"signals_total":len(info["signals"]),
            "timing_adjusted_lights":sum(x["tuning_kind"]=="cycle_green_offset" for x in adjusted),
            "offset_only_lights":sum(x["tuning_kind"]=="offset_only" for x in adjusted),
            "uncontrolled_lights":len(info["signals"])-len(adjusted),
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
                "Vehicle injection stops at injection_end_s; simulation continues until trips end or the drain cap is reached.",
                "Unfinished journeys are right-censored at simulation_end_s and must not be treated as completed.",
                "No teleportation is allowed to artificially improve completion rates.",
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
