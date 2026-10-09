"""First-run lifecycle and user-facing SUMO readiness tests."""
from fastapi.testclient import TestClient

from app import api, sumo_jobs, sumo_runner


def test_startup_automatically_schedules_real_network_setup(monkeypatch):
    monkeypatch.delenv("SALARIA_AUTO_SETUP", raising=False)
    submitted = []
    monkeypatch.setattr(sumo_runner, "status", lambda: {"netconvert": True, "available": False})
    monkeypatch.setattr(sumo_jobs, "submit", lambda kind: submitted.append(kind))
    with TestClient(api.app) as client:
        assert client.get("/health").status_code == 200
    assert submitted == ["setup"]


def test_startup_skips_when_network_ready(monkeypatch):
    monkeypatch.delenv("SALARIA_AUTO_SETUP", raising=False)
    submitted = []
    monkeypatch.setattr(sumo_runner, "status", lambda: {"netconvert": True, "available": True})
    monkeypatch.setattr(sumo_jobs, "submit", lambda kind: submitted.append(kind))
    with TestClient(api.app) as client:
        assert client.get("/health").status_code == 200
    assert submitted == []


def test_startup_can_be_disabled_for_offline_smoke_tests(monkeypatch):
    monkeypatch.setenv("SALARIA_AUTO_SETUP", "0")
    submitted = []
    monkeypatch.setattr(sumo_runner, "status", lambda: {"netconvert": True, "available": False})
    monkeypatch.setattr(sumo_jobs, "submit", lambda kind: submitted.append(kind))
    with TestClient(api.app) as client:
        assert client.get("/health").status_code == 200
    assert submitted == []


def test_setup_request_reuses_existing_running_job(monkeypatch):
    fake_job = {"id": "existing", "kind": "setup", "status": "running", "message": "Working"}
    monkeypatch.setattr(sumo_jobs, "_JOBS", {"existing": fake_job})
    monkeypatch.setattr(sumo_jobs._POOL, "submit", lambda *args: (_ for _ in ()).throw(
        AssertionError("An import job is already running")))
    assert sumo_jobs.submit("setup")["id"] == "existing"


def test_status_displays_import_progress(monkeypatch):
    monkeypatch.setattr(sumo_runner, "status", lambda: {"available": False, "netconvert": True})
    monkeypatch.setattr(sumo_jobs, "latest_setup", lambda: {"status": "running", "progress": .15})
    response = TestClient(api.app).get("/api/sumo/status")
    assert response.status_code == 200
    assert response.json()["mode"] == "preparing"
    assert response.json()["setup"]["progress"] == .15
