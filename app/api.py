
"""FastAPI service. /api/refresh-osm and weather endpoint access public external sources."""
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx

from app.engine import simulate
from app.models import SimulationRequest
from app.network import ROOT, current_network, refresh_osm, subset_network

app = FastAPI(title="Rome Mobility | Salaria Lab", version="0.1.0")
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


@app.get("/api/sumo/status")
def sumo_status():
    from app.sumo_runner import status
    return status()


@app.get("/api/sumo/network")
def sumo_network(segment: str = Query(default="full", pattern="^(full|south|north)$")):
    from app.sumo_runner import load_network
    try:
        return load_network(segment)
    except RuntimeError as exc:
        raise HTTPException(status_code=503,detail=str(exc)) from exc


@app.post("/api/sumo/setup",status_code=202)
def sumo_setup():
    from app.sumo_jobs import submit
    from app.sumo_runner import status
    ready=status()
    if not ready["netconvert"]:
        raise HTTPException(status_code=503,detail="netconvert unavailable; install SUMO before importing")
    try:
        return submit("setup")
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


@app.post("/api/sumo/jobs",status_code=202)
def sumo_job(request: SimulationRequest):
    from app.sumo_jobs import submit
    from app.sumo_runner import status
    ready=status()
    if not ready["available"]:
        raise HTTPException(status_code=503,detail=_missing_sumo_components(ready))
    try:
        return submit("run",request)
    except RuntimeError as exc:
        raise HTTPException(status_code=429,detail=str(exc)) from exc


@app.post("/api/sumo/compare/jobs",status_code=202)
def sumo_compare_job(request: SimulationRequest):
    from app.sumo_jobs import submit
    from app.sumo_runner import status
    ready = status()
    if not ready["available"]:
        raise HTTPException(status_code=503,detail=_missing_sumo_components(ready))
    try:
        return submit("compare",request)
    except RuntimeError as exc:
        raise HTTPException(status_code=429,detail=str(exc)) from exc


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
