
# Rome Mobility / Salaria Lab

An experimental traffic-signal research playground for **Via Salaria, Prati Fiscali → GRA (Rome)**.
Python FastAPI backend, animated Three.js 3D client, configurable traffic and signal scenarios.
**This is a scientific starter prototype, not live traffic or a validated digital twin.**

## ▶ Quick start

Requires Python **3.10+** and Internet access for the Three.js CDN. SUMO is **not** required for the web demo.

1. Clone the repository and enter it.
2. Create an environment: python -m venv .venv
3. Activate it: macOS/Linux source .venv/bin/activate; Windows .venv\Scripts\activate
4. Install: pip install -e ".[dev]"
5. Start: uvicorn app.api:app --reload
6. Open **http://127.0.0.1:8000**

In the browser:

- Select day/date, hour, rain intensity, base demand and speed.
- Set signal cycle and green duration; configure individual offsets (manual) or an automatically calculated green wave towards GRA or central Rome.
- Click **Avvia simulazione** to animate vehicles and traffic lights in 3D.
- Scrub the timeline, orbit/zoom the camera, focus a light, watch KPI and queues.
- Click **Confronta con baseline** to evaluate your scenario with identical input demand and random seed against a hypothetical all-zero-offset setup.
- Export the scenario and output as JSON.
- Click **Importa geometria e semafori OSM** to fetch OpenStreetMap geometry via Overpass and replace the illustrative corridor; results are cached locally.
- Optionally load precipitation mm/h from Open-Meteo for a past date/hour.

## Reproducible API

- GET /health
- GET /api/network — return cached OSM corridor if available, otherwise synthetic demo
- POST /api/refresh-osm — download/update actual OSM source and extract a candidate Salaria axis
- POST /api/simulate — deterministic lightweight traffic micro-model with time frames
- POST /api/compare — same seed, baseline vs selected parameters
- GET /api/weather/history?day=2026-09-15 — Open-Meteo historical hourly precipitation
- GET /api/sources
- GET /docs — interactive FastAPI OpenAPI docs

Example experiment (the data are HYPOTHETICAL):

    curl -X POST http://127.0.0.1:8000/api/simulate \
      -H "Content-Type: application/json" \
      -d '{"day":"2026-10-09","hour":8,"rain_mm_h":3,"speed_kmh":50,"demand_vph":500,"cycle_s":90,"green_s":45,"duration_min":12,"seed":42,"mode":"wave_outbound"}'

## Study fidelity, important

The first-run fallback includes **4 invented demonstration lights** and an approximate polyline.
They are not asserted to exist at those locations. If OSM import succeeds, the axis and annotated signals are crowdsourced geodata, not audited traffic-controller inventory.

This demo currently simulates **two independent traffic directions with two lanes each** (car-following and deceleration toward red indications). There is no junction turning, pedestrian phase, actual side-road traffic, or calibrated demand. Signal programs assume a common two-way main-road green with 3 seconds amber; remainder of cycle is red. Minimum green and clearance bounds are validated but do not certify safe use on streets. Green waves are derived from travel-time offsets, not an optimization algorithm.

**The simulation outputs are not congestion forecasts or a recommended real-world signal setting.**

The backend drives trajectories and metrics; Three.js renders an **illustrative, vertically exaggerated 3D model** over the candidate real road centerline. Buildings are procedural and NOT GIS building footprints.

See [data and validation register](docs/DATA_SOURCES.md) before interpreting experiments.

## SUMO / advanced research lane

A separate reproducible road network importer is provided. It requires the SUMO command-line tool netconvert and an Overpass connection:

    python scripts/bootstrap_sumo.py

Optional download without SUMO:

    python scripts/bootstrap_sumo.py --download-only

This saves OSM XML in data/raw and creates data/sumo/salaria.net.xml.
You must add calibrated origin/destination demand, actual signal programs and validation before scientific inference. The web service currently uses a standalone research-preview engine and **does not claim to run SUMO**.

SUMO docs: https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html

## Development and tests

    pip install -e ".[dev]"
    pytest -q

Project map:

    app/models.py        # parameter bounds
    app/network.py       # Overpass parsing + demo fallback
    app/engine.py        # deterministic lightweight vehicle model
    app/api.py           # FastAPI endpoints
    app/static/          # Three.js viewport + dashboard controls
    scripts/             # SUMO import helper
    tests/               # regression tests
    docs/DATA_SOURCES.md # provenance and calibration work list

Contributions welcome: especially field inventories, legitimate count datasets, network testing and reproducible calibration. Never publish proprietary traffic data or personal vehicle traces.

License: code MIT (see LICENSE). OSM data remain under ODbL.


## Select a section

The client supports the full Prati Fiscali–GRA candidate axis, the first 45% or the remaining 55%, using *distance fractions*, not confirmed neighbourhood boundaries. The model and list of semaphores update when the selection changes.
