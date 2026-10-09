"""No synthetic fallbacks when the user requests hourly observed demand."""
from datetime import date
from fastapi.testclient import TestClient
import pytest
from app.api import app
from app import observations
from app.models import SimulationRequest

HEADER="day_type,hour,direction,vehicles_per_hour,kind,source,observation_period,licence\n"
ROWS=(HEADER+
"weekday,8,outbound,780,observed_count,manual_survey,2026-09-10,owner_authorized\n"+
"weekday,8,inbound,1290,observed_count,manual_survey,2026-09-10,owner_authorized\n"+
"weekend,8,outbound,210,provider_estimate,licensed_provider,2026-09,restricted\n"+
"weekend,8,inbound,320,provider_estimate,licensed_provider,2026-09,restricted\n").encode()


def test_import_and_separate_directions_and_quality(tmp_path):
    path=tmp_path/"counts.json"
    info=observations.save_csv(ROWS,path=path)
    assert info["available"] and info["rows"]==4
    friday=observations.demand_for(date(2026,10,9),8,path=path)
    assert friday["outbound_vph"]==780
    assert friday["inbound_vph"]==1290
    saturday=observations.demand_for(date(2026,10,10),8,path=path)
    assert saturday["outbound_vph"]==210
    assert saturday["inbound_vph"]==320
    assert saturday["evidence_types"]==["provider_estimate"]
    assert "licence" in saturday["provenance"]["outbound"]
    with pytest.raises(ValueError,match="No traffic observations"):
        observations.demand_for(date(2026,10,9),10,path=path)


def test_invalid_csv_does_not_replace_existing(tmp_path):
    path=tmp_path/"counts.json"
    observations.save_csv(ROWS,path=path)
    original=path.read_bytes()
    cases=[
        HEADER+"weekday,8,inbound,NaN,observed_count,s,p,l\n",
        HEADER+"weekday,8,inbound,100.5,observed_count,s,p,l\n",
        HEADER+"weekday,24,inbound,100,observed_count,s,p,l\n",
        HEADER+"weekday,8,inbound,100,observed_count,,p,l\n",
        HEADER+"weekday,8,inbound,100,observed_count,s,p,l\n"
               "weekday,8,inbound,101,observed_count,s,p,l\n"
    ]
    for case in cases:
        with pytest.raises(ValueError):
            observations.save_csv(case.encode(),path=path)
        assert path.read_bytes()==original


def test_rest_upload_and_lookup(monkeypatch,tmp_path):
    path=tmp_path/"counts.json"
    save=observations.save_csv
    query=observations.demand_for
    original_desc=observations.describe
    monkeypatch.setattr(observations,"save_csv",lambda b: save(b,path=path))
    monkeypatch.setattr(observations,"demand_for",lambda d,h: query(d,h,path=path))
    monkeypatch.setattr(observations,"describe",lambda bundle=None: original_desc(bundle if bundle is not None else observations.load(path)))
    client=TestClient(app)
    assert client.get("/api/observations/status").json()["available"] is False
    assert client.post("/api/observations/import",content=ROWS,
                       headers={"Content-Type":"text/csv"}).status_code==200
    got=client.get("/api/observations/at",params={"day":"2026-10-09","hour":8})
    assert got.status_code==200 and got.json()["inbound_vph"]==1290
    assert client.get("/api/observations/at",params={"day":"2026-10-09","hour":21}).status_code==404


def test_sumo_requires_complete_observed_dataset(monkeypatch):
    monkeypatch.setattr(observations,"demand_for",
       lambda day,hour: (_ for _ in ()).throw(ValueError("Missing observed hour")))
    request={"traffic_source":"hourly_counts","mode":"wave_outbound"}
    client=TestClient(app)
    assert client.post("/api/sumo/jobs",json=request).status_code==422
    assert client.post("/api/research/jobs",json={"scenario":request,"seeds":[1,2]}).status_code==422
    assert client.post("/api/simulate",json=request).status_code==422
    assert SimulationRequest().traffic_source=="synthetic"
