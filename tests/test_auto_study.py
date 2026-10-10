"""One-click batch optimization safety, completeness, split seeds and export."""
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import json

from fastapi.testclient import TestClient

from app.models import SimulationRequest
from app.auto_study import (
    AutoStudyRequest, plan, policies, study_periods, model_score,
    run_auto, report_zip, select_winners,
)
from app.api import app


def design():
    return AutoStudyRequest(
        scenario=SimulationRequest(
            day=date(2026, 10, 10), area="salaria", seed=77, duration_min=3),
        api_key="TEST_SECRET_NOT_FOR_STORAGE", workers=2,
    )


def trial(_base, spec, _path):
    policy=spec["policy"]["id"]
    # Fake signals favour outbound coordination; controlled completed trips.
    delay=100 if policy=="baseline" else 50 if policy.startswith("wave_outbound") else 80
    return {
        "period":spec["period"]["id"],"date":spec["period"]["date"],
        "hour":spec["period"]["hour"],"day_type":spec["period"]["type"],
        "policy":policy,"seed":spec["seed"],
        "metrics":{"avg_delay_s":delay,"avg_travel_s":delay+75,
                   "p95_travel_s":delay+200,"max_queued_vehicles":15,
                   "mean_stops_per_trip":1,"completion_rate":.96,
                   "inserted":100,"completed":96,"still_on_road":4,
                   "upstream_waiting":0,"controlled_lights":3},
        "warnings":[],
    }


def test_exactly_500_matched_period_policy_seed_specs():
    req=design()
    specs=plan(req.scenario)
    assert len(specs)==500
    assert len(study_periods(req.scenario))==10
    assert len(policies())==10
    assert len({(x["period"]["id"],x["policy"]["id"],x["seed"])
                for x in specs})==500
    assert sum(x["policy"]["id"]=="baseline" for x in specs)==50
    assert len({x["seed"] for x in specs})==5


def test_quality_gate_disqualifies_incomplete_or_uncontrolled_runs():
    typical={"avg_delay_s":12,"max_queued_vehicles":6,
             "mean_stops_per_trip":2,"completion_rate":.9,
             "controlled_lights":3}
    assert model_score(typical) is not None
    assert model_score({**typical,"completion_rate":.65}) is None
    assert model_score({**typical,"controlled_lights":0}) is None
    assert model_score({**typical,"avg_delay_s":None}) is None


def test_full_study_exports_all_results_and_no_credentials(tmp_path):
    net=tmp_path/"salaria.net.xml"
    net.write_text("<net>test only</net>")
    req=design()
    expected=[{"period":"weekday_07","direction":"outbound",
               "historical_typical_s":1000,"freeflow_s":700,
               "length_m":8000,"prediction_departure":"2026-10-12T07:00:00+02:00",
               "classification":"PROVIDER_ROUTE_PREDICTION_NOT_TRAFFIC_COUNT"}]
    progress=[]
    report=run_auto(req,runner=trial,tomtom=(expected,[]),net_path=net,
                    progress=lambda p,msg:progress.append(p))
    assert report["simulations"]==500
    assert report["tomtom_success"]==1
    assert report["quality"]["accepted_periods"]==10
    assert report["selection"]["training_seeds"]==[77,78,79]
    assert report["selection"]["validation_seeds"]==[80,81]
    assert all(x["recommended"].startswith("wave_outbound")
               for x in report["selection"]["decisions"])
    assert progress[-1]>=.98
    blob=report_zip(report)
    with ZipFile(BytesIO(blob)) as z:
        assert set(z.namelist())=={
            "report.html","report.md","runs.csv","policy_rankings.csv",
            "recommended_schedule.csv","tomtom_reference.csv","study.json"
        }
        assert len(z.read("runs.csv").decode().splitlines())==501
        assert len(z.read("policy_rankings.csv").decode().splitlines())==101
        assert len(z.read("recommended_schedule.csv").decode().splitlines())==11
        assert "SIMULATED" in z.read("report.md").decode()
        assert "TEST_SECRET_NOT_FOR_STORAGE" not in repr(z.namelist())
        assert "TEST_SECRET_NOT_FOR_STORAGE" not in z.read("study.json").decode()
        assert "TEST_SECRET_NOT_FOR_STORAGE" not in z.read("report.html").decode()
        assert json.loads(z.read("study.json"))["simulations"]==500


def test_post_study_requires_tomtom_key_and_sumo_ready(monkeypatch):
    client=TestClient(app)
    from app import sumo_runner
    monkeypatch.setattr(sumo_runner,"status",
                        lambda area="salaria":{"available":False})
    response=client.post("/api/auto-study/jobs",
                         json={"scenario":{},"api_key":"LOCAL_TEST_KEY","workers":2})
    assert response.status_code==503
    wrong=client.post("/api/auto-study/jobs",json={"scenario":{},"workers":2})
    assert wrong.status_code==422


def test_report_download_endpoint_never_uses_path_traversal():
    client=TestClient(app)
    resp=client.get("/api/auto-study/reports/%2e%2e/download")
    assert resp.status_code in (404,422)
