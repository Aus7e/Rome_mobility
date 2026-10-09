
"""OSM-backed corridor extraction. Demo geometry is explicitly NOT surveyed GIS."""
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from heapq import heappop, heappush
import json
import math
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "salaria_overpass.json"
# Geographic area is intentionally limited to the Salaria / Prati Fiscali - GRA study.
BBOX = (41.936, 12.487, 42.006, 12.537)  # south, west, north, east
START = (41.9414, 12.5088)  # indicative point near Prati Fiscali, NOT surveyed junction
END = (41.9990, 12.5130)  # approximate area near the GRA
DEMO_POINTS = [
    [41.9414,12.5088], [41.948,12.5079], [41.952,12.5071],
    [41.958,12.5061], [41.964,12.5063], [41.971,12.5068],
    [41.978,12.5077], [41.984,12.5088], [41.990,12.5108], [41.999,12.513]
]


def meters(a, b):
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0]-b[0]) * 111_195, (a[1]-b[1]) * 111_195 * math.cos(lat))


def lengths(points):
    total = 0.
    out = [0.]
    for a, b in zip(points, points[1:]):
        total += meters(a, b)
        out.append(total)
    return out


def closest_fraction(point, poly):
    # Project a point onto the segments instead of the vertices.
    cum = lengths(poly)
    lat0 = math.radians(point[0])
    scale_x = 111_195 * math.cos(lat0)
    scale_y = 111_195
    px, py = point[1] * scale_x, point[0] * scale_y
    best = (float("inf"), 0.0)
    for i, (a, b) in enumerate(zip(poly, poly[1:])):
        ax, ay = a[1]*scale_x, a[0]*scale_y
        bx, by = b[1]*scale_x, b[0]*scale_y
        vx, vy = bx-ax, by-ay
        t = max(0., min(1., ((px-ax)*vx+(py-ay)*vy)/(vx*vx+vy*vy or 1.)))
        d = math.hypot(px-(ax+t*vx),py-(ay+t*vy))
        if d < best[0]:
            best = (d, cum[i] + t*(cum[i+1]-cum[i]))
    return best


def demo_network():
    cum = lengths(DEMO_POINTS)
    total = cum[-1]
    located = []
    for i, f in enumerate([0.17, 0.38, 0.61, 0.83]):
        target = total * f
        j = next(k for k in range(len(cum)-1) if cum[k+1] >= target)
        w = (target-cum[j]) / (cum[j+1]-cum[j])
        latitude = DEMO_POINTS[j][0]*(1-w) + DEMO_POINTS[j+1][0]*w
        longitude = DEMO_POINTS[j][1]*(1-w) + DEMO_POINTS[j+1][1]*w
        located.append({"id": "DEMO-0"+str(i+1), "s_m": round(target,1),
                        "lat": round(latitude,7), "lon": round(longitude,7),
                        "source": "synthetic_demo", "label": "Semaforo dimostrativo "+str(i+1)})
    return {
        "source": "synthetic_demo", "quality": "Illustrative geometry, signals and traffic: not measured or validated",
        "points": DEMO_POINTS, "length_m": round(total, 1),
        "signals": located,
        "updated_at": None,
        "attribution": "Synthetic illustrative corridor, not a measured map"
    }


def extract_osm(raw):
    elements = raw.get("elements", [])
    nodes = {}
    graph = defaultdict(list)
    for el in elements:
        if el.get("type") != "way" or not el.get("geometry"):
            continue
        ids = el.get("nodes", [])
        geo = el["geometry"]
        if len(ids) != len(geo):
            continue
        for nid, loc in zip(ids, geo):
            nodes[nid] = [loc["lat"], loc["lon"]]
        for u, v in zip(ids, ids[1:]):
            if u != v:
                d = meters(nodes[u], nodes[v])
                graph[u].append((v, d))
                graph[v].append((u, d))

    if not graph:
        raise ValueError("No usable Via Salaria highway geometry found")
    visited = set()
    candidates = []
    for root in graph:
        if root in visited:
            continue
        stack = [root]
        visited.add(root)
        component = []
        while stack:
            u = stack.pop()
            component.append(u)
            for v, _ in graph[u]:
                if v not in visited:
                    visited.add(v)
                    stack.append(v)
        if len(component) < 8:
            continue
        south = min(component, key=lambda n: meters(nodes[n], START))
        north = min(component, key=lambda n: meters(nodes[n], END))
        score = meters(nodes[south], START) + meters(nodes[north], END)
        candidates.append((score, south, north))
    if not candidates:
        raise ValueError("Could not find a connected segment of Via Salaria")
    _, origin, dest = min(candidates)
    queue = [(0., origin)]
    dist = {origin: 0.}
    parent = {}
    while queue:
        cost, u = heappop(queue)
        if cost != dist[u]:
            continue
        if u == dest:
            break
        for v, w in graph[u]:
            nd = cost + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                parent[v] = u
                heappush(queue, (nd, v))
    if dest not in dist:
        raise ValueError("No continuous OSM path between selected endpoints")
    ids = [dest]
    while ids[-1] != origin:
        ids.append(parent[ids[-1]])
    points = [nodes[n] for n in reversed(ids)]
    if lengths(points)[-1] < 2_000 or points[-1][0]-points[0][0] < 0.02:
        raise ValueError("OSM import returned an implausibly short corridor; demo preserved")
    signal_nodes = [e for e in elements if e.get("type") == "node" and e.get("tags", {}).get("highway") == "traffic_signals"]
    found = []
    for signal in signal_nodes:
        distance, s = closest_fraction([signal["lat"], signal["lon"]], points)
        if distance <= 38 and s > 60 and s < lengths(points)[-1]-60:
            found.append({
                "id": "OSM-"+str(signal["id"]), "s_m": round(s, 1),
                "lat": signal["lat"], "lon": signal["lon"],
                "source": "osm_unverified", "label": "Segnale OSM "+str(signal["id"]),
                "distance_from_axis_m": round(distance, 1)
            })
    # OSM may tag separate physical heads of the same intersection; group within 60m.
    found.sort(key=lambda x: x["s_m"])
    merged = []
    for s in found:
        if merged and s["s_m"]-merged[-1]["s_m"] < 60:
            continue
        merged.append(s)
    return {
        "source": "openstreetmap", "quality": "OSM crowdsourced geometry/signals, NOT an official timing inventory",
        "points": points, "length_m": round(lengths(points)[-1], 1),
        "signals": merged, "updated_at": raw.get("_downloaded_at"),
        "attribution": "© OpenStreetMap contributors (ODbL)",
        "warnings": ["Signs are OSM annotations, not checked on site.",
                     "SUMO/intersection control may contain more or fewer signal groups."]
    }


def current_network():
    if CACHE.exists():
        try:
            return extract_osm(json.loads(CACHE.read_text(encoding="utf-8")))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass
    return demo_network()


async def refresh_osm():
    query = (
        '[out:json][timeout:70];('
        'way["highway"]["name"~"Salaria",i](41.936,12.487,42.006,12.537);'
        'node["highway"="traffic_signals"](41.936,12.487,42.006,12.537);'
        ');out geom;'
    )
    endpoints = ["https://overpass.kumi.systems/api/interpreter", "https://overpass-api.de/api/interpreter"]
    last_error = None
    for endpoint in endpoints:
        try:
            async with httpx.AsyncClient(timeout=85, follow_redirects=False) as client:
                response = await client.post(endpoint, data={"data": query})
                response.raise_for_status()
                raw = response.json()
            raw["_downloaded_at"] = datetime.now(timezone.utc).isoformat()
            network = extract_osm(raw)  # Validate BEFORE replacing cached data.
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            return network
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            last_error = str(exc)
    raise RuntimeError("Overpass unavailable/unsuitable data: "+str(last_error))


def subset_network(net, section="full"):
    """Experimental selection by corridor length, not confirmed neighbourhood boundaries."""
    bounds={"full":(0.,1.),"south":(0.,.45),"north":(.45,1.)}
    if section not in bounds:
        raise ValueError("Unknown network section")
    if section=="full":
        return {**net,"section":"full"}
    f0,f1=bounds[section]
    cum=lengths(net["points"])
    lo,hi=cum[-1]*f0,cum[-1]*f1

    def point_at(distance):
        for i in range(len(cum)-1):
            if distance<=cum[i+1]:
                t=(distance-cum[i])/(cum[i+1]-cum[i] or 1)
                a,b=net["points"][i],net["points"][i+1]
                return [a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t]
        return net["points"][-1]

    points=[point_at(lo)]
    points += [net["points"][i] for i in range(1,len(cum)-1) if lo<cum[i]<hi]
    points.append(point_at(hi))
    signals=[{**s,"s_m":round(s["s_m"]-lo,1)}
             for s in net["signals"] if lo+30<s["s_m"]<hi-30]
    return {**net,"points":points,"length_m":round(lengths(points)[-1],1),
            "signals":signals,"section":section}
