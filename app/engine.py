
"""Small reproducible corridor car-following demo, not a substitute for SUMO."""
import math
import random
import statistics

from app.models import SimulationRequest


def demand_factor(day, hour):
    """Explicitly hypothetical shape; not inferred from real Rome traffic counts."""
    weekend = day.weekday() >= 5
    if weekend:
        return 0.46 if hour < 6 else 0.78 if 11 <= hour <= 20 else 0.60
    if hour < 6:
        return 0.30
    if 7 <= hour <= 9:
        return 1.65
    if 17 <= hour <= 19:
        return 1.50
    if hour in (6, 10, 16, 20):
        return 1.08
    return 0.88


def timing_for(signal, request, length_m):
    override = request.overrides.get(signal["id"])
    green = override.green_s if override and override.green_s is not None else request.green_s
    if green + 5 > request.cycle_s:
        raise ValueError("A per-signal green exceeds the cycle minus 5 seconds")
    if request.mode == "wave_outbound":
        offset = int(round(-signal["s_m"] / (request.speed_kmh / 3.6))) % request.cycle_s
    elif request.mode == "wave_inbound":
        offset = int(round(-(length_m-signal["s_m"]) / (request.speed_kmh / 3.6))) % request.cycle_s
    else:
        offset = override.offset_s % request.cycle_s if override else 0
    return {"id":signal["id"], "s_m":signal["s_m"], "offset_s":offset,
            "green_s":green, "cycle_s":request.cycle_s, "label":signal.get("label",signal["id"])}


def aspect(signal, t):
    """Green, amber, red. Both Salaria travel directions share the main-line phase.
    Crossing street traffic is NOT yet represented in this demo."""
    phase = (t + signal["offset_s"]) % signal["cycle_s"]
    if phase < signal["green_s"]:
        return "green"
    if phase < signal["green_s"] + 3:
        return "amber"
    return "red"


def simulate(network, request: SimulationRequest, include_frames=True):
    if request.traffic_source != "synthetic":
        raise ValueError("Hourly observed demand requires SUMO, not preview")
    length = network["length_m"]
    signals = [timing_for(s, request, length) for s in network["signals"]]
    rng = random.Random(request.seed)
    hours_factor = demand_factor(request.day, request.hour)
    # Only speed/available deceleration respond to precipitation; rain-demand relation uncalibrated.
    rain_speed_factor = max(0.62, 1.0 - 0.035*request.rain_mm_h)
    vmax = request.speed_kmh / 3.6 * rain_speed_factor
    brake = max(1.6, 2.8 - 0.09*request.rain_mm_h)
    insertion_rate = min(0.8, request.demand_vph * hours_factor / 3600)
    travel_steps = request.duration_min * 60
    cars = []
    next_id = 0
    waiting = [0.,0.]
    frames = []
    completed_times = []
    completed_stops = []
    stopped_vehicle_seconds = 0
    max_queue = 0
    queue_series = []
    inserted = 0
    stop_counts = {s["id"]: 0 for s in signals}
    for t in range(travel_steps+1):
        for direction in (0, 1):
            waiting[direction] += insertion_rate
            if waiting[direction] >= 1:
                # Fractional vehicles preserve deterministic demand across steps.
                for _ in range(int(waiting[direction])):
                    lane_candidates = [lane for lane in (0,1) if not any(
                        c["dir"]==direction and c["lane"]==lane and c["pos"] < 11 for c in cars)]
                    if lane_candidates:
                        lane = rng.choice(lane_candidates)
                        cars.append({"id":next_id, "dir":direction, "lane":lane, "pos":0., "speed":vmax,
                                     "birth":t,"stops":0,"is_stopped":False})
                        next_id += 1
                        inserted += 1
                        waiting[direction] -= 1
                    else:
                        break
        queue = 0
        for direction in (0,1):
            for lane in (0,1):
                group = sorted((c for c in cars if c["dir"]==direction and c["lane"]==lane),
                               key=lambda c:c["pos"], reverse=True)
                lead_pos = float("inf")
                for car in group:
                    position = car["pos"]
                    # v^2 <= 2*a*d stopping envelope for both signals and preceding vehicles.
                    available = max(0, lead_pos - position - 7.)
                    obstacle = None
                    next_red = float("inf")
                    for sig in signals:
                        mark = sig["s_m"] if direction==0 else length-sig["s_m"]
                        if mark >= position and aspect(sig, t) != "green":
                            gap = mark - position - 4
                            if gap < next_red:
                                next_red = gap
                                obstacle = sig["id"]
                    available = min(available, max(0, next_red))
                    safe_speed = math.sqrt(2*brake*available) if available < float("inf") else vmax
                    speed = min(vmax, car["speed"]+1.65, safe_speed, max(0, available))
                    car["pos"] += max(0, speed)
                    car["speed"] = speed
                    now_stopped = speed < 0.8
                    if now_stopped:
                        queue += 1
                        stopped_vehicle_seconds += 1
                    if now_stopped and not car["is_stopped"]:
                        car["stops"] += 1
                        if obstacle is not None:
                            stop_counts[obstacle] += 1
                    car["is_stopped"] = now_stopped
                    lead_pos = car["pos"]
        survivors = []
        for c in cars:
            if c["pos"] >= length:
                completed_times.append(t-c["birth"]+1)
                completed_stops.append(c["stops"])
            else:
                survivors.append(c)
        cars = survivors
        max_queue = max(max_queue, queue)
        if t % 5 == 0:
            queue_series.append([t,queue])
        if include_frames and t % 2 == 0:
            frames.append({"t":t, "cars":[
                [c["id"], c["dir"], c["lane"], round(c["pos"],1), round(c["speed"]*3.6,1)]
                for c in cars
            ], "signals":[aspect(s,t) for s in signals]})
    avg_travel = statistics.mean(completed_times) if completed_times else None
    p95 = sorted(completed_times)[math.ceil(.95*len(completed_times))-1] if completed_times else None
    freeflow = length/vmax
    return {
        "network":network["source"], "length_m":length,
        "signals":signals, "frames":frames,
        "queue_series":queue_series,
        "metrics":{
            "inserted":inserted, "completed":len(completed_times),
            "still_on_road":len(cars), "upstream_waiting":round(sum(waiting),1),
            "avg_travel_s":round(avg_travel,1) if avg_travel is not None else None,
            "p95_travel_s":p95, "freeflow_s":round(freeflow,1),
            "avg_delay_s":round(avg_travel-freeflow,1) if avg_travel is not None else None,
            "mean_stops_per_trip":round(statistics.mean(completed_stops),2) if completed_stops else None,
            "stopped_vehicle_seconds":stopped_vehicle_seconds, "max_queued_vehicles":max_queue,
            "stops_by_signal":stop_counts, "demand_factor":round(hours_factor,2),
            "effective_speed_kmh":round(vmax*3.6,1)
        },
        "limitations":[
            "Hypothetical demand curves, not counts measured on Via Salaria.",
            "One-dimensional two-way model: no turning, side-road flows, pedestrians or lane changes.",
            "Same main-road green for both directions, not a verified field signal program.",
            "Rain changes assumed free-flow speed and braking only; not calibrated against observations.",
            "Metrics measure the demo model, not observed travel times or forecast conditions."
        ]
    }
