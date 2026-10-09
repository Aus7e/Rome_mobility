"""OSM mapped points are evidence records, not certified SUMO controllers."""
import json
import pytest
from app.signal_inventory import extract_osm_candidates,compare_controllers,inventory

AXIS=[[41.9410,12.5080],[41.9430,12.5080]]
OSM=b'''<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
<node id="101" lat="41.9411" lon="12.5080"><tag k="highway" v="traffic_signals"/><tag k="traffic_signals:direction" v="forward"/></node>
<node id="102" lat="41.9412" lon="12.5080"><tag k="crossing" v="traffic_signals"/></node>
<node id="103" lat="41.9800" lon="12.6000"><tag k="highway" v="traffic_signals"/></node>
<way id="7"><nd ref="101"/><nd ref="102"/><tag k="highway" v="primary"/></way>
</osm>
'''


def test_real_osm_nodes_are_source_linked(tmp_path):
    osm=tmp_path/"raw.osm.xml"
    osm.write_bytes(OSM)
    signals=extract_osm_candidates(osm,AXIS)
    assert {s["osm_node_id"] for s in signals}=={"101","102"}
    assert signals[0]["direction"]=="forward"
    assert signals[0]["source_url"]=="https://www.openstreetmap.org/node/101"


def test_proximity_match_has_clear_qualification(tmp_path):
    osm=tmp_path/"raw.osm.xml"
    osm.write_bytes(OSM)
    archive=tmp_path/"arch.json"
    archive.write_text(json.dumps({"source":"historical","results":[{"id":"old"}]}))
    net={"source":"sumo_osm","geo_valid":True,"points":AXIS,"signals":[
        {"id":"tls_near","lat":41.9411,"lon":12.5080},
        {"id":"tls_far","lat":41.9430,"lon":12.5080}
    ]}
    result=inventory(osm_path=osm,archive_path=archive,sumo_net=net)
    assert result["counts"]["osm_nodes"]==2
    assert result["counts"]["matched_pairs"]==1
    assert result["sumo_controllers"][1]["verification"]=="generated_not_osm_verified"
    assert result["historical_archive_2020"][0]["evidence"]=="ARCHIVE_2020_NOT_CURRENT"
    assert "NOT_AUDITED" in result["evidence_level"]


def test_proximity_only_matches_one_mapping_record():
    osm=[{"id":"osm_1","lat":41.9411,"lon":12.5080}]
    tls=[{"id":"t1","lat":41.9411,"lon":12.5080},
         {"id":"t2","lat":41.94112,"lon":12.5080}]
    assert len(compare_controllers(osm,tls))==1
    with pytest.raises(ValueError,match="Georeferenced"):
        inventory(sumo_net={"source":"synthetic_demo","geo_valid":False})
