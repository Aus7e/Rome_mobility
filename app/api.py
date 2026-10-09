
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


@app.get("/api/sources")
def sources():
    return {"osm":"https://www.openstreetmap.org/copyright",
            "overpass":"https://wiki.openstreetmap.org/wiki/Overpass_API",
            "weather":"https://open-meteo.com/en/docs/historical-weather-api",
            "rome":"https://romamobilita.it/sistemi-e-tecnologie/open-data/",
            "sumo":"https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html",
            "traffic":"https://www.tomtom.com/products/traffic-stats/"}
