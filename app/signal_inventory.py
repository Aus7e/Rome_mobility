"""Source-aware signal inventory: OSM nodes vs simulated SUMO controllers.

Signals mapped by OSM volunteers are real *mapping records*, not guaranteed
current controller installations. Do not inject fabricated TLS into SUMO.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from app.network import ROOT, closest_fraction, meters

OSM_PATH = ROOT / "data" / "raw" / "salaria.osm.xml"
ARCHIVE_PATH = ROOT / "data" / "raw" / "signal_archive_2020.json"


def extract_osm_candidates(osm_path=OSM_PATH, axis=None, *, radius_m=130) -> list[dict]:
    if axis is None:
        raise ValueError("Provide verified map-derived road axis")
    if not Path(osm_path).is_file():
        return []
    nodes = []
    # iterparse avoids loading the whole OSM file into Python objects at once.
    for _, el in ET.iterparse(str(osm_path), events=("end",)):
        if el.tag == "node":
            tags = {tag.get("k"): tag.get("v") for tag in el.findall("tag")}
            if tags.get("highway") == "traffic_signals" or tags.get("crossing") == "traffic_signals":
                try:
                    point = [float(el.attrib["lat"]), float(el.attrib["lon"])]
                except (KeyError, ValueError):
                    el.clear()
                    continue
                distance, along = closest_fraction(point, axis)
                if distance <= radius_m:
                    osm_id = str(el.attrib.get("id"))
                    nodes.append({
                        "id": f"osm_node_{osm_id}", "osm_node_id": osm_id,
                        "lat": point[0], "lon": point[1],
                        "s_m": round(along, 1), "distance_from_axis_m": round(distance, 1),
                        "crossing": tags.get("crossing", "unknown"),
                        "direction": tags.get("traffic_signals:direction") or tags.get("direction"),
                        "name": tags.get("name"),
                        "evidence": "OSM_MAPPED_UNVERIFIED",
                        "source_url": f"https://www.openstreetmap.org/node/{osm_id}",
                    })
        if el.tag in {"node", "way"}:
            el.clear()
    return sorted(nodes, key=lambda x: x["s_m"])


def compare_controllers(osm_points: list[dict], sumo_signals: list[dict], *, match_radius_m=75):
    """1-to-1 proximity match for display. It does NOT certify controller identity."""
    candidate_pairs = []
    for signal in sumo_signals:
        for osm in osm_points:
            delta = meters([signal["lat"], signal["lon"]], [osm["lat"], osm["lon"]])
            if delta <= match_radius_m:
                candidate_pairs.append((delta, signal["id"], osm["id"]))
    used_tls, used_osm = set(), set()
    matches = []
    for distance, tls, osm in sorted(candidate_pairs):
        if tls in used_tls or osm in used_osm:
            continue
        used_tls.add(tls)
        used_osm.add(osm)
        matches.append({"sumo_tls_id": tls, "osm_id": osm,
                        "separation_m": round(distance, 1),
                        "match_method": "proximity_only_not_field_verified"})
    return matches


def load_archive(path=ARCHIVE_PATH):
    if not Path(path).is_file():
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        {**r, "evidence": "ARCHIVE_2020_NOT_CURRENT",
         "source_url": payload.get("source")}
        for r in payload.get("results", [])
    ]


def inventory(*, osm_path=OSM_PATH, archive_path=ARCHIVE_PATH, sumo_net=None, region="salaria"):
    if sumo_net is None or sumo_net.get("source") != "sumo_osm" or not sumo_net.get("geo_valid"):
        raise ValueError("Georeferenced OSM/SUMO network required to audit mapped signals")
    osm = extract_osm_candidates(osm_path, sumo_net["points"],
                                radius_m=14000 if region=="nord_est" else 130)
    tls = sumo_net["signals"]
    matches = compare_controllers(osm, tls)
    mapped = {x["osm_id"] for x in matches}
    joined = {x["sumo_tls_id"] for x in matches}
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_level": "GEOSPATIAL_MAPPING_NOT_AUDITED_INSTALLATIONS",
        "method": "OSM mapped traffic signal nodes near study axis or regional radius; "
                  "SUMO TLS proximity join ≤75 m; no controller identity inferred",
        "osm_candidates": [{**r, "matched_sumo_controller":
                            next((m["sumo_tls_id"] for m in matches if m["osm_id"] == r["id"]), None)}
                           for r in osm],
        "sumo_controllers": [{**s, "matched_osm_signal":
                              next((m["osm_id"] for m in matches if m["sumo_tls_id"] == s["id"]), None),
                              "verification": "mapped_proximity_only" if s["id"] in joined
                              else "generated_not_osm_verified"}
                             for s in tls],
        "matches": matches,
        "counts": {
            "osm_nodes": len(osm),
            "sumo_controllers_generated": len(tls),
            "matched_pairs": len(matches),
            "osm_unmatched": len(osm) - len(mapped),
            "sumo_unmatched": len(tls) - len(joined),
        },
        "historical_archive_2020": load_archive(archive_path),
        "limitations": [
            "OSM nodes can represent signal heads/stop lines at one junction, not controller installations.",
            "SUMO controllers and phase timings are inferred by netconvert and not field measured.",
            "A proximity match is not confirmation of an active signal or identical controller.",
            "2020 archival points, if imported, are historical and cannot establish present-day status.",
            "A field/municipal audit is required before claiming actual current inventory.",
        ],
    }
