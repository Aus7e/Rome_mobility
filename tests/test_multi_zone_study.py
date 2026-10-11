"""Batch-optimization tests without invoking 5,500 actual SUMO runs."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import json

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.areas import AREAS
from app.auto_study import AutoStudyRequest
from app.multi_zone_study import select_study_areas, run_area_batch


def design(scope="all_prepared"):
    return AutoStudyRequest(
        scope=scope,api_key="PRIVATE_TEST_KEY_NEVER_PERSIST",
        scenario={"area":"centro","day":"2026-10-11","duration_min":3},
        workers=1,
    )


def mock_zone(request, *, progress=None, tomtom_max_periods=None):
    assert tomtom_max_periods == 2
    if progress:progress(.5,"Mock test scenario")
    return {
        "area":request.scenario.area,
        "simulations":500,
        "quality":{"accepted_periods":4},
        "tomtom_success":2,
        "tomtom_requested":4,
        "network_sha256":"mock_"+request.scenario.area,
    }


def mock_zip(report):
    mem=BytesIO()
    with ZipFile(mem,"w",ZIP_DEFLATED) as out:
        out.writestr("report.html","Zone "+report["area"])
        out.writestr("study.json",json.dumps({"area":report["area"]}))
    return mem.getvalue()


def test_ready_areas_support_all_eleven_without_inventing_readiness():
    assert len(AREAS)==11
    readiness={area:True for area in AREAS}
    assert select_study_areas("all_prepared","centro",readiness)==list(AREAS)
    readiness["litorale"]=False
    assert len(select_study_areas("all_prepared","centro",readiness))==10
    assert select_study_areas("selected","centro",readiness)==["centro"]
    with pytest.raises(ValueError):
        select_study_areas("selected","litorale",readiness)
    with pytest.raises(ValueError):
        select_study_areas("selected","missing_area",readiness)


def test_500_per_area_combined_report_complete_and_no_key():
    req=design()
    req.resolved_areas=["salaria","centro","est"]
    seen=[]
    out=run_area_batch(req,run_zone=mock_zone,make_zip=mock_zip,
                       progress=lambda p,msg:seen.append((p,msg)))
    assert out["total_runs"]==1500
    assert out["tomtom_success"]==6
    assert out["tomtom_requested"]==12
    m=out["manifest"]
    assert m["planned_simulations"]==1500
    assert m["completed_areas"]==3
    assert m["failed_areas"]==0
    assert seen[-1][0]==1
    with ZipFile(BytesIO(out["download_bytes"])) as z:
        assert {"manifest.json","riepilogo.csv","relazione_generale.html"}<=set(z.namelist())
        assert len(z.read("riepilogo.csv").decode().splitlines())==4
        for area in req.resolved_areas:
            assert f"aree/{area}/report.html" in z.namelist()
            assert json.loads(z.read(f"aree/{area}/study.json"))["area"]==area
        for name in z.namelist():
            assert "PRIVATE_TEST_KEY_NEVER_PERSIST" not in z.read(name).decode()


def test_failed_area_retained_as_warning_and_other_zone_still_reported():
    req=design()
    req.resolved_areas=["salaria","centro"]
    def failing(request,**kw):
        if request.scenario.area=="salaria":raise RuntimeError("SUMO routing disconnected")
        return mock_zone(request,**kw)
    out=run_area_batch(req,run_zone=failing,make_zip=mock_zip)
    assert out["total_runs"]==500
    assert out["manifest"]["failed_areas"]==1
    with ZipFile(BytesIO(out["download_bytes"])) as z:
        assert "aree/centro/report.html" in z.namelist()
        assert "aree/salaria/report.html" not in z.namelist()
        assert "SUMO routing disconnected" in z.read("manifest.json").decode()


def test_all_failures_must_not_claim_success():
    req=design()
    req.resolved_areas=["centro"]
    def error(*args,**kwargs):raise RuntimeError("Broken")
    with pytest.raises(RuntimeError,match="No Rome zone"):
        run_area_batch(req,run_zone=error,make_zip=mock_zip)


def test_api_freezes_prepared_areas_and_does_not_trust_client_list(monkeypatch):
    from app import sumo_runner,sumo_jobs
    monkeypatch.setattr(sumo_runner,"status",
        lambda area="salaria":{"available":area in {"salaria","centro","est"}})
    seen=[]
    def capture(kind,req):
        seen.append(req)
        return {"id":"mock","areas":req.resolved_areas,
                "total_runs":500*len(req.resolved_areas)}
    monkeypatch.setattr(sumo_jobs,"submit",capture)
    client=TestClient(app)
    body={"scope":"all_prepared","api_key":"PRIVATE_TEST_KEY_NEVER_PERSIST",
          "scenario":{"area":"centro"},"resolved_areas":["litorale"]}
    response=client.post("/api/auto-study/jobs",json=body)
    assert response.status_code==202
    assert response.json()["areas"]==["salaria","centro","est"]
    assert response.json()["total_runs"]==1500
    assert "PRIVATE_TEST_KEY_NEVER_PERSIST" not in response.text
    assert seen[0].resolved_areas==["salaria","centro","est"]
    body["scope"]="selected"
    result=client.post("/api/auto-study/jobs",json=body)
    assert result.status_code==202
    assert result.json()["areas"]==["centro"]
    body["scenario"]["area"]="litorale"
    assert client.post("/api/auto-study/jobs",json=body).status_code==503


def test_missing_observed_counts_in_another_zone_rejected(monkeypatch):
    from app import sumo_runner,observations
    monkeypatch.setattr(sumo_runner,"status",
        lambda area="salaria":{"available":area in {"centro","est"}})
    def lookup(day,hour,area):
        if area=="est":raise ValueError("Missing Est")
        return {"csv_sha256":"fake"}
    monkeypatch.setattr(observations,"demand_for_area",lookup)
    response=TestClient(app).post("/api/auto-study/jobs",json={
        "scope":"all_prepared","api_key":"PRIVATE_TEST_KEY_NEVER_PERSIST",
        "scenario":{"area":"centro","traffic_source":"hourly_counts"}})
    assert response.status_code==422
    assert "est:" in response.json()["detail"]


def test_visible_ui_explicit_selection_and_counts():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    html=(root/"app/static/index.html").read_text()
    js=(root/"app/static/main.js").read_text()
    assert 'id="autoStudyScope"' in html
    assert 'value="all_prepared"' in html
    assert 'id="autoStudyEstimate"' in html
    assert "500*count" in js
    assert "scope,api_key:key" in js
    assert "result.failed_areas" in js
