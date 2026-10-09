"""Real SUMO/TraCI integration smoke test using a local synthetic OSM crossroad.

Only executes in the dedicated GitHub Actions job. No Overpass requests or
claims of authentic Salaria traffic are needed to validate the engine.
"""
from datetime import date
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from app.models import SimulationRequest
from app.sumo_runner import load_network, run_sumo

pytestmark=pytest.mark.skipif(
    not shutil.which("sumo") or not shutil.which("netconvert"),
    reason="SUMO binary not installed"
)


def make_test_osm(path:Path):
    root=ET.Element("osm",version="0.6",generator="rome-mobility-test")
    lat0=41.94
    for i in range(13):
        tags={"highway":"traffic_signals"} if i in (3,6,9) else {}
        node=ET.SubElement(root,"node",id=str(i+1),lat=f"{lat0+i*.005:.6f}",
                           lon="12.508000",visible="true",version="1")
        for k,v in tags.items():
            ET.SubElement(node,"tag",k=k,v=v)
    # OSM XML readers expect all nodes before any ways.
    for j,i in enumerate((3,6,9)):
        for side,lon in enumerate((12.504,12.512)):
            nodeid=100+j*2+side
            ET.SubElement(root,"node",id=str(nodeid),lat=f"{lat0+i*.005:.6f}",
                          lon=f"{lon:.6f}",visible="true",version="1")
    # Three cross streets. Named Salaria axis remains bidirectional.
    for j,i in enumerate((3,6,9)):
        way=ET.SubElement(root,"way",id=str(500+j),version="1")
        for nid in (100+j*2, i+1,101+j*2):
            ET.SubElement(way,"nd",ref=str(nid))
        for k,v in {"highway":"secondary","name":"Via Laterale "+str(j)}.items():
            ET.SubElement(way,"tag",k=k,v=v)
    way=ET.SubElement(root,"way",id="300",version="1")
    for n in range(1,14):
        ET.SubElement(way,"nd",ref=str(n))
    for k,v in {"highway":"primary","name":"Via Salaria","maxspeed":"50"}.items():
        ET.SubElement(way,"tag",k=k,v=v)
    ET.ElementTree(root).write(path,encoding="utf-8",xml_declaration=True)


def test_real_sumo_traffic_pipeline(tmp_path):
    osm=tmp_path/"test-synthetic.osm.xml"
    net=tmp_path/"test.net.xml"
    make_test_osm(osm)
    subprocess.run(["netconvert","--osm-files",str(osm),"--output-file",str(net),
                    "--tls.guess-signals","--output.street-names","true",
                    "--no-warnings","true"],check=True,capture_output=True,text=True)
    import sumolib
    imported=sumolib.net.readNet(str(net))
    names=[(e.getID(),e.getName()) for e in imported.getEdges()]
    assert any("salaria" in (name or "").lower() for _,name in names), names[:40]
    load_network.cache_clear()
    scene=load_network("full",net)
    assert scene["geo_valid"],"OSM import must preserve geographic projection"
    assert scene["source"]=="sumo_osm"
    assert scene["length_m"]>4000
    assert len(scene["roads"])>=5
    request=SimulationRequest(day=date(2026,10,9),hour=8,duration_min=3,
                              demand_vph=700,speed_kmh=50,seed=4,mode="manual")
    output=run_sumo(request,net_path=net)
    assert output["engine"]=="sumo"
    assert output["metrics"]["inserted"]>0,output.get("warnings")
    assert output["frames"]
    assert any(frame["cars"] for frame in output["frames"])
    assert "warnings" in output
