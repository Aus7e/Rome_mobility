"""Bounded, independent OpenStreetMap/SUMO study zones across Rome.

These are approximate research viewports, not municipal or administrative
boundaries. No downloaded road graph proves measured demand or signal timing.
Large areas are prepared on demand and cached independently.
"""
from __future__ import annotations

from pathlib import Path
from app.network import ROOT

AREAS = {
    "salaria": {
        "name":"Via Salaria · Prati Fiscali – GRA",
        "bbox":(41.936,12.487,42.006,12.537),
        "focus_roads":("salaria",),
        "description":"Lightweight original road corridor",
    },
    "nord_est": {
        "name":"Roma Nord-Est · Salaria, Nomentana, Tiburtina",
        "bbox":(41.904,12.460,42.038,12.616),
        "focus_roads":("salaria","nomentana","tiburtina"),
        "description":"Northeast metropolitan road network",
    },
    "nord_ovest": {
        "name":"Roma Nord-Ovest · Cassia, Flaminia, Trionfale",
        "bbox":(41.928,12.376,42.042,12.492),
        "focus_roads":("cassia","flaminia","trionfale"),
        "description":"Northwest corridors and connecting junctions",
    },
    "nord": {
        "name":"Roma Nord · Flaminia, Salaria, Parioli",
        "bbox":(41.936,12.443,42.038,12.552),
        "focus_roads":("flaminia","salaria","viale parioli"),
        "description":"Northern cross-city study region",
    },
    "centro": {
        "name":"Centro · Prati, Termini, San Giovanni",
        "bbox":(41.865,12.442,41.926,12.525),
        "focus_roads":("nazio","corso vittorio","merulana","emanuele"),
        "description":"Dense central Rome road network",
    },
    "est": {
        "name":"Roma Est · Prenestina, Tiburtina, Casilina",
        "bbox":(41.865,12.528,41.952,12.651),
        "focus_roads":("prenestina","tiburtina","casilina"),
        "description":"Eastern urban arteries and feeder roads",
    },
    "ovest": {
        "name":"Roma Ovest · Aurelia, Boccea, Monteverde",
        "bbox":(41.852,12.365,41.944,12.484),
        "focus_roads":("aurelia","boccea","gregorio vii"),
        "description":"Western urban arteries and access roads",
    },
    "sud_est": {
        "name":"Roma Sud-Est · Tuscolana, Appia, Ardeatina",
        "bbox":(41.787,12.480,41.885,12.627),
        "focus_roads":("tuscolana","appia","ardeatina"),
        "description":"Southeastern urban arteries",
    },
    "sud_ovest": {
        "name":"Roma Sud-Ovest · EUR, Ostiense, Magliana",
        "bbox":(41.787,12.392,41.870,12.515),
        "focus_roads":("cristoforo colombo","ostiense","magliana"),
        "description":"Southwestern city and EUR approaches",
    },
    "sud": {
        "name":"Roma Sud · Appia, Laurentina, Colombo",
        "bbox":(41.780,12.447,41.867,12.579),
        "focus_roads":("appia","laurentina","cristoforo colombo"),
        "description":"Southern cross-city study zone",
    },
    "litorale": {
        "name":"Roma Litorale · Ostia, Acilia, Casal Palocco",
        "bbox":(41.717,12.237,41.803,12.390),
        "focus_roads":("via del mare","ostiense","cristoforo colombo"),
        "description":"Coastal municipality transport network, separate from central Rome",
    },
}


def require_area(area: str) -> dict:
    try:
        return AREAS[area]
    except KeyError as exc:
        raise ValueError(f"Unknown Rome study area: {area}") from exc


def network_path(area: str) -> Path:
    require_area(area)
    filename="salaria.net.xml" if area=="salaria" else f"roma_{area}.net.xml"
    return ROOT/"data"/"sumo"/filename


def osm_path(area: str) -> Path:
    require_area(area)
    filename="salaria.osm.xml" if area=="salaria" else f"roma_{area}.osm.xml"
    return ROOT/"data"/"raw"/filename


def area_from_network(path) -> str:
    # Compatibility: locally compiled test.net.xml is the Salaria test graph.
    name=Path(path).name
    for area in AREAS:
        if name==network_path(area).name:
            return area
    return "salaria"


def query_for_bbox(bbox, *, regional=False):
    coords=",".join(f"{n:.6f}" for n in bbox)
    road_filter=(
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


def regional_queries(area: str = "nord_est") -> list[str]:
    """Split into bounded requests instead of one huge public Overpass query."""
    definition=require_area(area)
    south,west,north,east=definition["bbox"]
    if area=="salaria":
        return [query_for_bbox(definition["bbox"],regional=False)]
    # Split each regional area into four subregions, reducing risk of an
    # overloaded query and keeping OSM IDs deduplicated during merging.
    lat_mid=(south+north)/2
    lon_mid=(west+east)/2
    return [query_for_bbox(bbox,regional=True) for bbox in (
        (south,west,lat_mid,lon_mid),
        (south,lon_mid,lat_mid,east),
        (lat_mid,west,north,lon_mid),
        (lat_mid,lon_mid,north,east),
    )]
