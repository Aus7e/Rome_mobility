"""Two explicitly different SUMO study extents: narrow Salaria and NE Rome.

The 16 x 12 km regional grid is much larger than the old corridor; it
does NOT model all Rome, and all traffic demand is synthetic unless documented.
"""
from pathlib import Path

from app.network import ROOT

AREAS = {
    "salaria": {
        "name": "Via Salaria · Prati Fiscali – GRA",
        "bbox": (41.936, 12.487, 42.006, 12.537),
        "description": "Lightweight Salaria corridor",
    },
    "nord_est": {
        "name": "Roma Nord-Est · Salaria, Nomentana, Tiburtina",
        "bbox": (41.904, 12.460, 42.038, 12.616),
        "description": "Broader connected-road research region (not all Rome)",
    },
}


def network_path(area: str) -> Path:
    if area not in AREAS:
        raise ValueError("Unknown SUMO study area")
    filename = "salaria.net.xml" if area == "salaria" else "roma_nord_est.net.xml"
    return ROOT / "data" / "sumo" / filename


def osm_path(area: str) -> Path:
    if area not in AREAS:
        raise ValueError("Unknown SUMO study area")
    filename = "salaria.osm.xml" if area == "salaria" else "roma_nord_est.osm.xml"
    return ROOT / "data" / "raw" / filename


def area_from_network(path) -> str:
    return "nord_est" if Path(path).name == "roma_nord_est.net.xml" else "salaria"


def query_for_bbox(bbox, *, regional=False):
    coords = ",".join(f"{n:.6f}" for n in bbox)
    road_filter = (
        'way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|'
        'primary_link|secondary|secondary_link|tertiary|tertiary_link|'
        'residential|unclassified|living_street|service)$"]'
        if regional else 'way["highway"]'
    )
    return (f'[out:xml][timeout:240];('
            f'{road_filter}({coords});'
            f'node["highway"="traffic_signals"]({coords});'
            f'node["crossing"="traffic_signals"]({coords});'
            ');(._;>;);out body;')


def regional_queries():
    south, west, north, east = AREAS["nord_est"]["bbox"]
    midlat = (south + north) / 2
    midlon = (west + east) / 2
    # Four moderate requests: no single very large public-Overpass query.
    return [query_for_bbox(bbox, regional=True) for bbox in (
        (south, west, midlat, midlon),
        (south, midlon, midlat, east),
        (midlat, west, north, midlon),
        (midlat, midlon, north, east),
    )]
