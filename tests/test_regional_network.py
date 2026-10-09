"""Regional Overpass assembly and area identity without network access."""
import xml.etree.ElementTree as ET
from app.areas import AREAS, network_path, osm_path, regional_queries, query_for_bbox
from scripts.bootstrap_sumo import assemble_region, validate_osm


def test_region_extends_beyond_salaria_in_both_axes():
    s=AREAS["salaria"]["bbox"]
    e=AREAS["nord_est"]["bbox"]
    assert e[0]<s[0] and e[1]<s[1]
    assert e[2]>s[2] and e[3]>s[3]
    assert network_path("salaria") != network_path("nord_est")
    assert osm_path("salaria") != osm_path("nord_est")
    assert len(regional_queries())==4
    assert "traffic_signals" in regional_queries()[0]


def test_merging_four_overlapping_osm_tiles_deduplicates_ids():
    a=b"""<?xml version="1.0"?><osm version="0.6">
      <node id="1" lat="41.90" lon="12.50"/>
      <node id="2" lat="41.91" lon="12.50"/>
      <way id="20"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/></way>
      </osm>"""
    b=b"""<?xml version="1.0"?><osm version="0.6">
      <node id="2" lat="41.91" lon="12.50"/>
      <node id="3" lat="41.92" lon="12.50"/>
      <way id="20"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/></way>
      <way id="21"><nd ref="2"/><nd ref="3"/><tag k="highway" v="secondary"/></way>
      </osm>"""
    payload=assemble_region([a,b])
    validate_osm(payload)
    root=ET.fromstring(payload)
    ids=[(e.tag,e.get("id")) for e in root]
    assert len(ids)==len(set(ids))
    assert ("node","3") in ids
    assert ("way","21") in ids
