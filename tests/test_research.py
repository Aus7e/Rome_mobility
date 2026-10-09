"""Scientific report invariants: paired seeds, coverage warnings, exports."""
import io
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from app.api import app
from app.models import SimulationRequest
from app.research import ResearchRequest, make_zip, run_study, summarize


def fake_runner(request, frames=False):
    assert frames is False
    # Seed-dependent, same demand; a modest artificial benefit under wave policy.
    improvement = 8 if request.mode == "wave_outbound" else 0
    travel = 145 + request.seed % 5 - improvement
    return {
        "metrics": {
            "avg_delay_s": travel - 80, "avg_travel_s": travel,
            "p95_travel_s": travel + 20,
            "mean_stops_per_trip": 2.0 if not improvement else 1.5,
            "max_queued_vehicles": 24 if not improvement else 20,
            "inserted": 100, "completed": 93, "still_on_road": 7,
            "upstream_waiting": 0, "controlled_lights": 2
        }, "warnings": []
    }


def test_research_runs_ordered_matched_seeds_and_bundle(tmp_path):
    design = ResearchRequest(
        scenario=SimulationRequest(mode="wave_outbound", demand_vph=550),
        seeds=[5, 6, 7],
    )
    network = tmp_path / "salaria.net.xml"
    network.write_text("<net/>")
    report = run_study(design, simulate=fake_runner, net_path=network)
    assert report["evidence_level"] == "SIMULATED_UNCALIBRATED"
    assert len(report["pairs"]) == 3
    assert [x["seed"] for x in report["pairs"]] == [5, 6, 7]
    assert report["summary"]["avg_delay_s"]["mean_delta"] == -8
    assert report["summary"]["completion_rate"]["mean_delta"] == 0
    assert report["network_sha256"] is not None
    with ZipFile(io.BytesIO(make_zip(report))) as archive:
        assert set(archive.namelist()) == {
            "report.md", "experiment.json", "runs.csv", "paired_differences.csv"
        }
        assert len(archive.read("runs.csv").decode().splitlines()) == 7
        assert len(archive.read("paired_differences.csv").decode().splitlines()) == 4
        inside = json.loads(archive.read("experiment.json"))
        assert inside["scenario"]["demand_vph"] == 550
        assert "NOT the observed Rome" in inside["baseline_definition"]
        assert "SIMULATED" in archive.read("report.md").decode()


def test_incomplete_trips_get_warning_and_zero_completion_is_not_silent():
    def stalled(request, frames=False):
        return {"metrics": {"inserted": 100, "completed": 2, "still_on_road": 98,
                            "avg_delay_s": 0, "controlled_lights": 0}}
    report = run_study(ResearchRequest(scenario=SimulationRequest(), seeds=[1, 2]),
                       simulate=stalled)
    assert any("trip averages may be biased" in w for w in report["warnings"])
    assert any("no signals" in w for w in report["warnings"])
    assert any("may equal baseline" in w for w in report["warnings"])
    assert report["summary"]["avg_travel_s"]["mean_delta"] is None


def test_seed_input_bounded_unique():
    with pytest.raises(ValidationError):
        ResearchRequest(seeds=[5, 5], scenario=SimulationRequest())
    with pytest.raises(ValidationError):
        ResearchRequest(seeds=[3], scenario=SimulationRequest())
    with pytest.raises(ValidationError):
        ResearchRequest(seeds=list(range(201)), scenario=SimulationRequest())


def test_research_job_requires_ready_sumo(monkeypatch):
    from app import sumo_runner
    monkeypatch.setattr(sumo_runner, "status", lambda: {
        "installed": True, "netconvert": True,
        "python_modules": True, "network_ready": False, "available": False
    })
    response = TestClient(app).post("/api/research/jobs", json={
        "scenario": {"mode": "wave_outbound"}, "seeds": [5, 6]
    })
    assert response.status_code == 503


def test_export_endpoint_rejects_other_job_kinds(monkeypatch):
    from app import sumo_jobs
    monkeypatch.setattr(sumo_jobs, "info", lambda job_id: {
        "kind": "run", "status": "complete"
    })
    response = TestClient(app).get("/api/research/jobs/some-id/download")
    assert response.status_code == 404


def test_export_endpoint_streams_zip(monkeypatch):
    from app import sumo_jobs
    report = run_study(
        ResearchRequest(scenario=SimulationRequest(mode="wave_outbound"), seeds=[1, 2]),
        simulate=fake_runner,
    )
    monkeypatch.setattr(sumo_jobs, "info", lambda job_id: {
        "kind": "research", "status": "complete"
    })
    monkeypatch.setattr(sumo_jobs, "result", lambda job_id: report)
    response = TestClient(app).get("/api/research/jobs/some-id/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert "runs.csv" in archive.namelist()



def test_hundreds_of_seeds_allowed_but_concurrency_bounded(monkeypatch):
    import app.research as research
    design = ResearchRequest(
        scenario=SimulationRequest(mode="wave_outbound"),
        seeds=list(range(200)),
        workers=8,
    )
    assert len(design.seeds) == 200
    monkeypatch.setenv("ROME_RESEARCH_MAX_WORKERS", "3")
    monkeypatch.setattr(research.os, "cpu_count", lambda: 12)
    assert research.allowed_workers(design.workers) == 3
    monkeypatch.setenv("ROME_RESEARCH_MAX_WORKERS", "invalid")
    assert research.allowed_workers(8) == 4
    monkeypatch.setattr(research.os, "cpu_count", lambda: 1)
    assert research.allowed_workers(8) == 1


def test_parallel_pairs_run_and_return_in_requested_order(monkeypatch, tmp_path):
    """Fake executor tests scheduling without requiring SUMO on the Python job."""
    from concurrent.futures import Future
    import app.research as research

    submitted = []
    class FakePool:
        def __init__(self, *, max_workers, mp_context):
            assert max_workers == 3
            assert mp_context.get_start_method() == "spawn"
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def submit(self, fn, scenario, seed, simulator, net_path):
            submitted.append(seed)
            future = Future()
            future.set_result(research._pair_for_seed(scenario, seed, fake_runner))
            return future

    monkeypatch.setattr(research, "ProcessPoolExecutor", FakePool)
    monkeypatch.setattr(research, "allowed_workers", lambda requested: 3)
    design = ResearchRequest(
        scenario=SimulationRequest(mode="wave_outbound"),
        seeds=[21, 22, 23], workers=3,
    )
    progress = []
    # Explicitly select the production simulator identity so the parallel
    # scheduling branch runs, while replacing only its worker executor.
    report = research.run_study(
        design, progress=lambda p, msg: progress.append((p, msg)),
        net_path=tmp_path / "missing.xml"
    )
    assert submitted == [21, 22, 23]
    assert report["parallel_workers"] == 3
    assert [p["seed"] for p in report["pairs"]] == [21, 22, 23]
    assert progress[-1][0] == 1



def test_research_exports_traffic_source_explicitly():
    from app.research import ResearchRequest, run_study
    from app import observations as data
    from app.models import SimulationRequest
    original = data.demand_for
    import pytest
    from unittest.mock import patch
    def known(day,hour):
        return {"csv_sha256":"known-hash","outbound_vph":400,"inbound_vph":700,
                "provenance":{"outbound":{"source":"survey"},"inbound":{"source":"survey"}}}
    def stub(request,frames=False):
        return {"metrics":{"inserted":4,"completed":4,"avg_travel_s":10,
                           "avg_delay_s":1,"controlled_lights":1}}
    with patch.object(data,"demand_for",known):
        result=run_study(ResearchRequest(scenario=SimulationRequest(
            traffic_source="hourly_counts"),seeds=[1,2]),simulate=stub)
    assert result["observation_dataset_sha256"]=="known-hash"
    assert result["observation_provenance"]["inbound_vph"]==700
