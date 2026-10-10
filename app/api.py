
"""FastAPI service. /api/refresh-osm and weather endpoint access public external sources."""
from contextlib import asynccontextmanager
from datetime import date
import io
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx

from app.engine import simulate
from app.research import ResearchRequest
from app.auto_study import AutoStudyRequest, study_periods
from app.models import SimulationRequest
from app.areas import AREAS, network_path, osm_path
from pydantic import BaseModel, Field, SecretStr
from app.network import ROOT, current_network, refresh_osm, subset_network

@asynccontextmanager
async def lifespan(_app):
    """Make the real SUMO network available without blocking the web UI."""
    from app.sumo_runner import status
    from app.sumo_jobs import submit
    ready = status()
    if os.environ.get("SALARIA_AUTO_SETUP", "1") == "1" and ready["netconvert"] and not ready["available"]:
        try:
            submit("setup")
        except RuntimeError:
            pass  # Preview remains available if the importer cannot be queued.
    yield


app = FastAPI(title="Rome Mobility | Salaria Lab", version="0.3.0", lifespan=lifespan)
STATIC = ROOT / "app" / "static"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "service": "salaria-lab"}


@app.get("/api/network")
def network(segment: str = Query(default="full", pattern="^(full|south|north)$")):
    return subset_network(current_network(), segment)


@app.post("/api/refresh-osm")
async def load_osm():
    try:
        return await refresh_osm()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/simulate")
def run_scenario(request: SimulationRequest):
    if request.traffic_source == "hourly_counts":
        raise HTTPException(status_code=422, detail="Hourly observed demand requires SUMO")
    network = subset_network(current_network(), request.segment)
    valid_ids = {signal["id"] for signal in network["signals"]}
    if not set(request.overrides).issubset(valid_ids):
        raise HTTPException(status_code=422, detail="Unknown signal id; refresh current network")
    try:
        return simulate(network, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/compare")
def compare(request: SimulationRequest):
    if request.traffic_source == "hourly_counts":
        raise HTTPException(status_code=422, detail="Hourly observed demand requires SUMO")
    network = subset_network(current_network(), request.segment)
    if not set(request.overrides).issubset({s["id"] for s in network["signals"]}):
        raise HTTPException(status_code=422, detail="Unknown signal id")
    baseline = request.model_copy(update={"mode":"manual","overrides":{}})
    try:
        base = simulate(network, baseline, include_frames=False)
        test = simulate(network, request, include_frames=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"baseline":base["metrics"], "experiment":test["metrics"],
            "note":"Same seed and hypothetical demand; baseline = all offsets zero, not observed real timings."}


@app.get("/api/weather/history")
async def weather_history(day: date = Query(...)):
    if day > date.today():
        raise HTTPException(status_code=422, detail="Historical date must not be in the future")
    params = {"latitude":41.972, "longitude":12.508, "start_date":day.isoformat(),
              "end_date":day.isoformat(), "hourly":"precipitation",
              "timezone":"Europe/Rome"}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            res = await client.get("https://archive-api.open-meteo.com/v1/archive", params=params)
            res.raise_for_status()
            payload = res.json()
        hourly = payload.get("hourly", {})
        if not hourly.get("time"):
            raise ValueError("Empty meteorological response")
        return {"date":day.isoformat(), "hours":hourly["time"],
                "precipitation_mm":hourly["precipitation"],
                "source":"Open-Meteo historical gridded model; not an on-road rain sensor"}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="Weather source unavailable: "+str(exc)) from exc


@app.get("/api/observations/status")
def observations_status():
    from app.observations import describe
    return describe()


@app.get("/api/observations/at")
def observations_at(day: date, hour: int = Query(ge=0, le=23)):
    from app.observations import demand_for
    try:
        return demand_for(day, hour)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/observations/import")
async def import_observations(request: Request):
    from app.observations import save_csv
    try:
        return save_csv(await request.body())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/signals/inventory")
def mapped_signals(area: str = Query(default="salaria", pattern="^(salaria|nord_est)$")):
    from app.sumo_runner import status, load_network
    from app.signal_inventory import inventory
    if not (status() if area=="salaria" else status(area))["available"]:
        raise HTTPException(status_code=503, detail="SUMO georeferenced OSM network not ready")
    try:
        return inventory(sumo_net=load_network("full",network_path(area)), osm_path=osm_path(area), region=area)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class TomTomOnScreen(BaseModel):
    day: date
    hour: int = Field(ge=0, le=23)
    direction: str = Field(pattern="^(outbound|inbound)$")
    api_key: SecretStr | None = None


class TomTomKeyCheck(BaseModel):
    api_key: SecretStr


@app.post("/api/traffic/check-key")
def tomtom_check_key(request: TomTomKeyCheck):
    """Verify Routing authorization. Credentials stay in this request only."""
    from app.traffic_reference import check_routing_key
    try:
        return check_routing_key(request.api_key.get_secret_value())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.post("/api/traffic/typical")
def typical_traffic_key_on_screen(request: TomTomOnScreen):
    """Key is provided per request in JSON body and never saved by backend."""
    from app.traffic_reference import get_typical_traffic
    from app.sumo_runner import load_network, status
    if not status("salaria")["available"]:
        raise HTTPException(status_code=503, detail="Salaria SUMO network not ready")
    try:
        info = load_network("full", network_path("salaria"))
        return get_typical_traffic(
            request.day, request.hour, request.direction,
            network=info, api_key=request.api_key.get_secret_value() if request.api_key else None)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/traffic/typical")
def typical_route_traffic(day: date, hour: int = Query(ge=0, le=23),
                          direction: str = Query(pattern="^(outbound|inbound)$")):
    """Optional traffic-time reference, never a counted or calibrated flow."""
    from app.traffic_reference import get_typical_traffic
    from app.sumo_runner import load_network, status
    if not status()["available"]:
        raise HTTPException(status_code=503, detail="SUMO OSM network not ready")
    try:
        info = load_network("full")
        return get_typical_traffic(day, hour, direction, network=info)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/sumo/areas")
def sumo_areas():
    from app.sumo_runner import status
    return {"areas":[{"id":area,"name":definition["name"],
        "bbox":definition["bbox"],"description":definition["description"],
        "ready":status(area)["available"]} for area, definition in AREAS.items()]}


@app.get("/api/sumo/status")
def sumo_status(area: str = Query(default="salaria", pattern="^(salaria|nord_est)$")):
    from app.sumo_runner import status
    from app.sumo_jobs import latest_setup
    ready = status() if area=="salaria" else status(area)
    job = latest_setup() if area=="salaria" else latest_setup(area)
    return {**ready, "setup": job, "mode": "ready" if ready["available"] else
            "preparing" if job and job["status"] in {"queued", "running"} else "demo"}


@app.get("/api/sumo/network")
def sumo_network(segment: str = Query(default="full", pattern="^(full|south|north)$"),
                 area: str = Query(default="salaria", pattern="^(salaria|nord_est)$")):
    from app.sumo_runner import load_network
    try:
        return load_network(segment, network_path(area))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503,detail=str(exc)) from exc


@app.post("/api/sumo/setup",status_code=202)
def sumo_setup(area: str = Query(default="salaria", pattern="^(salaria|nord_est)$")):
    from app.sumo_jobs import submit
    from app.sumo_runner import status
    ready=status() if area=="salaria" else status(area)
    if not ready["netconvert"]:
        raise HTTPException(status_code=503,detail="netconvert unavailable; install SUMO before importing")
    try:
        return submit("setup",area)
    except RuntimeError as exc:
        raise HTTPException(status_code=429,detail=str(exc)) from exc


def _missing_sumo_components(ready: dict) -> str:
    missing = []
    if not ready.get("installed"):
        missing.append("eseguibile SUMO")
    if not ready.get("python_modules"):
        missing.append("librerie Python traci/sumolib")
    if not ready.get("network_ready"):
        missing.append("rete OSM/SUMO della Salaria")
    return ("SUMO non pronto: manca " + ", ".join(missing)
            + ". Premi «Prepara rete reale SUMO» per scaricare la mappa, "
              "oppure esegui python scripts/bootstrap_sumo.py nel container.")


def _ensure_hourly_data(request: SimulationRequest):
    if request.traffic_source == "hourly_counts":
        from app.observations import demand_for
        try:
            demand_for(request.day, request.hour)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/sumo/jobs",status_code=202)
def sumo_job(request: SimulationRequest):
    _ensure_hourly_data(request)
    from app.sumo_jobs import submit
    from app.sumo_runner import status
    ready=status() if request.area=="salaria" else status(request.area)
    if not ready["available"]:
        raise HTTPException(status_code=503,detail=_missing_sumo_components(ready))
    try:
        return submit("run",request)
    except RuntimeError as exc:
        raise HTTPException(status_code=429,detail=str(exc)) from exc


@app.post("/api/sumo/compare/jobs",status_code=202)
def sumo_compare_job(request: SimulationRequest):
    _ensure_hourly_data(request)
    from app.sumo_jobs import submit
    from app.sumo_runner import status
    ready = status(request.area)
    if not ready["available"]:
        raise HTTPException(status_code=503,detail=_missing_sumo_components(ready))
    try:
        return submit("compare",request)
    except RuntimeError as exc:
        raise HTTPException(status_code=429,detail=str(exc)) from exc


@app.post("/api/research/jobs", status_code=202)
def research_job(design: ResearchRequest):
    _ensure_hourly_data(design.scenario)
    from app.sumo_jobs import submit
    from app.sumo_runner import status
    ready = status() if design.scenario.area=="salaria" else status(design.scenario.area)
    if not ready["available"]:
        raise HTTPException(status_code=503, detail=_missing_sumo_components(ready))
    try:
        return submit("research", design)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@app.post("/api/auto-study/jobs",status_code=202)
def start_auto_study(request: AutoStudyRequest):
    from app.sumo_runner import status
    from app.sumo_jobs import submit
    for area in ("salaria",request.scenario.area):
        if not status(area)["available"]:
            raise HTTPException(status_code=503,detail=f"Prepare SUMO area {area} first")
    if request.scenario.traffic_source=="hourly_counts":
        from app.observations import demand_for
        missing=[]
        for period in study_periods(request.scenario):
            try:
                demand_for(date.fromisoformat(period["date"]),period["hour"])
            except ValueError:
                missing.append(period["id"])
        if missing:
            raise HTTPException(status_code=422,
                detail="Missing hourly input traffic for: "+", ".join(missing))
    try:
        return submit("auto",request)
    except RuntimeError as exc:
        raise HTTPException(status_code=429,detail=str(exc)) from exc


@app.get("/api/auto-study/current")
def auto_study_current():
    from app.sumo_jobs import latest_auto_study
    job=latest_auto_study()
    return {"active":bool(job and job["status"] in {"queued","running"}),
            "job":job}


@app.get("/api/auto-study/reports/latest")
def latest_auto_study_report():
    directory=ROOT/"data"/"reports"
    reports=sorted(directory.glob("automatic-*.zip"),
                   key=lambda p:p.stat().st_mtime,reverse=True) if directory.is_dir() else []
    if not reports:return {"available":False}
    name=reports[0].stem.removeprefix("automatic-")
    return {"available":True,"download_url":"/api/auto-study/reports/"+name+"/download"}


@app.get("/api/auto-study/reports/{job_id}/download")
def auto_study_report_download(job_id: str):
    from uuid import UUID
    try:
        if str(UUID(job_id))!=job_id:raise ValueError("Invalid UUID")
    except ValueError as exc:
        raise HTTPException(status_code=404,detail="Report not found") from exc
    path=ROOT/"data"/"reports"/("automatic-"+job_id+".zip")
    if not path.is_file():
        raise HTTPException(status_code=404,detail="Report not ready")
    return FileResponse(path,media_type="application/zip",
                        filename="rome-mobility-500-simulations.zip")


@app.get("/api/research/jobs/{job_id}/download")
def research_download(job_id: str):
    from app.research import make_zip
    from app.sumo_jobs import info, result
    try:
        job = info(job_id)
        if job["kind"] != "research":
            raise HTTPException(status_code=404, detail="Not a research job")
        data = result(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Research job not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StreamingResponse(io.BytesIO(make_zip(data)), media_type="application/zip",
                             headers={"Content-Disposition":
                                      'attachment; filename="salaria-research.zip"'})


@app.get("/api/sumo/jobs/{job_id}")
def sumo_job_status(job_id: str):
    from app.sumo_jobs import info
    try:
        return info(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,detail="Job not found") from exc


@app.get("/api/sumo/jobs/{job_id}/result")
def sumo_job_result(job_id: str):
    from app.sumo_jobs import result
    try:
        return result(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404,detail="Job not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/api/sources")
def sources():
    return {"osm":"https://www.openstreetmap.org/copyright",
            "overpass":"https://wiki.openstreetmap.org/wiki/Overpass_API",
            "weather":"https://open-meteo.com/en/docs/historical-weather-api",
            "rome":"https://romamobilita.it/sistemi-e-tecnologie/open-data/",
            "sumo":"https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html",
            "traffic":"https://www.tomtom.com/products/traffic-stats/"}
