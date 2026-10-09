
# Rome Mobility / Salaria Lab

An experimental traffic-signal research playground for **Via Salaria, Prati Fiscali → GRA (Rome)**.
Python FastAPI backend, animated Three.js 3D client, and two scenario engines:
- **Preview** — fast standalone Python approximation, works without SUMO.
- **SUMO** — genuine TraCI microscopic vehicle simulations imported from OpenStreetMap, including turning traffic and side roads.

**This is a research prototype, not live traffic or a calibrated digital twin.**

## ▶ Quick start — plug & play (recommended)

Requires Docker Desktop with the Docker Compose plugin, plus Internet access
**during installation and the first OSM download**. From this repository:

```bash
docker compose up --build -d
```

Visit **http://localhost:8000**. The preview is available immediately.
In the background the backend downloads the roads from OpenStreetMap and
converts them with SUMO. The page shows import progress or an error; when the
network is ready it automatically switches to the **SUMO** simulator and
runs the real engine. If an Overpass provider is unavailable, the preview
remains usable, and **Riprova preparazione rete SUMO** retries the setup.

No manual Python installation, `SUMO_HOME` configuration, OSM bootstrap
command, CDN dependency, or manual network-mode switching is required.
The Docker image bundles Three.js and OrbitControls locally.

Restart without rebuilding using `docker compose up -d`. OSM and SUMO
networks are saved in `./data` and reused on the next startup.

For developers running without Docker: install SUMO and Python dependencies;
also provide the local frontend vendor assets under `app/static/vendor`
using Three.js 0.165.0. The supported one-command deployment is Docker.

In the browser:

- Select day/date, hour, rain intensity, base demand and speed.
- Set signal cycle and green duration; configure individual offsets (manual) or an automatically calculated green wave towards GRA or central Rome.
- Click **Avvia simulazione** to animate vehicles and traffic lights in 3D.
- Scrub the timeline, orbit/zoom the camera, focus a light, watch KPI and queues.
- Click **Confronta con baseline** to evaluate your scenario with identical input demand and random seed against a hypothetical all-zero-offset setup.
- Export the scenario and output as JSON.
- Click **Importa geometria e semafori OSM** to fetch OpenStreetMap geometry via Overpass and replace the illustrative corridor; results are cached locally.
- Optionally load precipitation mm/h from Open-Meteo for a past date/hour.

## 🚦 Full SUMO / TraCI mode (recommended)

Install Docker Desktop or Docker Engine with Compose. From the repository root:

    docker compose up --build

Open **http://localhost:8000**. Network import runs automatically in the
background (or is skipped when a usable cached network exists). SUMO is
selected as soon as the import is ready; run new experiments with
**Avvia simulazione**. The UI polls the background simulation job and replays
SUMO vehicle positions. You can select Preview at any time.

- Individual per-intersection greens and offsets apply through TraCI, preserving
  imported red/yellow/green conflicts; infeasible adjustments are skipped with warnings.
- Real driving behaviour, turns and side-street vehicles come from SUMO itself.
- Traffic demand, turning share, signal plan, rainfall response and the corridor
  endpoint definition remain **research assumptions** until independently calibrated.
- Access experiment metrics, comparison and JSON export from the browser.
- This is a **local app**; it is not deployed or secured for public Internet use.

Read [technical SUMO guide](docs/SUMO_MODE.md) and [source register](docs/DATA_SOURCES.md).
SUMO test coverage includes a synthetic-but-georeferenced OSM network with actual
netconvert and TraCI execution in GitHub Actions.

## If the map download shows HTTP 406

The initial Overpass import can be blocked by the provider. The downloader now
uses a clear User-Agent, alternative global OSM Overpass providers, XML
validation, and safe local caching. After updating your checkout:

    git pull origin main
    docker compose up --build -d
    # The app automatically imports the network; manual setup is optional.

Then reopen http://localhost:8000 and select SUMO. Read
[Overpass error troubleshooting](docs/OVERPASS_TROUBLESHOOTING.md).

## Actual mapped traffic lights and documented hourly traffic

With SUMO active the sidebar now shows the georeferenced `highway=traffic_signals`
**OSM nodes** near the study axis and the nearby SUMO controllers,
with clickable OSM source IDs. This is a traceable *mapping inventory*,
not a certified list of currently operational installations. Multiple
traffic signal nodes may correspond to one junction or to pedestrian
crossings. Some SUMO controllers may be auto-generated and unmatched.
See `GET /api/signals/inventory`. For optional comparison with a historical
2020 archival ArcGIS point layer run
`docker compose exec salaria-lab python scripts/download_signal_archive.py`.
That layer is explicitly archival, not contemporary evidence.

To use observed hour-by-hour **directional flows** rather than made-up
daily multipliers, select **Conteggi/stime orarie importate** and
upload an authorized CSV. Required headers:

```csv
day_type,hour,direction,vehicles_per_hour,kind,source,observation_period,licence
```

Each row must identify a genuine source, observation period and usage licence,
with `day_type` either `weekday` or `weekend`, `hour` 0–23,
`direction` `outbound` (towards GRA) or `inbound` (towards Centro),
and `kind` either `observed_count` for measured counts or
`provider_estimate` for model-based volume estimates. Do not label a
routing travel-time prediction as a vehicle count. CSV rows are deliberately
**not bundled with invented sample figures**. For example, with 24 hours
and 2 directions you need 48 separately sourced records for a day type.

The source CSV is validated and saved under `data/observations` locally,
with a SHA-256 digest, preserving provenance. The requested hour must include
both main-road directions; missing hours cause a clear error and **never**
fall back silently to synthetic demand. Lateral-road route proportions,
actual signal phases and OD matrices are **still assumptions**, so even
with observed corridor counts the whole model is NOT field-calibrated.
The simplified Preview mode supports synthetic demand only.

For counts estimated by a historical traffic data vendor, obtain licensed
TomTom Traffic Volume outputs or municipal traffic detector/field counts;
TomTom Routing ETA alone cannot supply traffic volume. Provider access,
coverage, and redistribution terms must be verified separately.

## Optional TomTom typical traffic route reference

You can connect your own TomTom Routing API key without modifying the app code.
Create a local `.env` in the repository root containing:

```dotenv
TOMTOM_API_KEY=YOUR_KEY
ROME_RESEARCH_MAX_WORKERS=4
```

Keep this file private; never commit credentials. Restart with
`docker compose up -d --force-recreate`. In the dashboard, click
**Stima traffico tipico TomTom** for an optional *provider-predicted*
historical-typical journey time for the selected day-of-week/hour and
direction. The provider uses a future matching day when a date is in the
past. The service uses the OSM corridor axis and via points but the
provider's selected route may not coincide exactly with SUMO's route
catalog. This result is **not a traffic count**, is **not a raw historical
observation**, and is deliberately not stored or exported with simulator
reports. Separate access/subscription terms and possible charges apply.

Use TomTom **Traffic Stats Route Analysis** or **Area Analysis** for
retrospective aggregated measured/probe-based speeds and travel times
by time slice, with a suitable licence. Those datasets are distinct
from this predictive routing endpoint and not yet automatically imported.
Documentation:
https://docs.tomtom.com/traffic-stats/documentation/api/route-analysis

## Research report and paired-seed experiments

Once SUMO is ready, choose an outbound/inbound green wave, set day/hour/demand,
then click **Studio parallelo + report ZIP**. The lab runs a
hypothetical all-zero-offset baseline and your selected signal policy for
each seed. Configure 2 to 200 seeds and a bounded number of worker processes
(default 2, maximum 8, additionally restricted by
`ROME_RESEARCH_MAX_WORKERS` and available CPU cores). Use 20–50 seeds for
exploratory stability; bigger runs consume significant CPU and RAM.
The baseline/experiment pair for each seed runs sequentially inside one
worker process, while independent seeds run in parallel. After the study
finishes, the app downloads a self-contained ZIP:

- report.md: methodological summary, differences and limits;
- runs.csv: per-seed metrics and denominators;
- paired_differences.csv: differences within matched seeds;
- experiment.json: scenario inputs, network SHA-256 and warnings.

These are **uncalibrated simulation outputs**, not counts or travel times
observed on Via Salaria. Completed-trip means are especially sensitive to
unfinished trips at the simulation cutoff. See
[research methodology](docs/RESEARCH_METHOD.md).

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
You must add calibrated origin/destination demand, actual signal programs and validation before scientific inference. The web service now supports **both** a preview engine and genuine SUMO/TraCI simulation. See the **Full SUMO** instructions above for installation.

SUMO docs: https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html

## Development and tests

    pip install -e ".[dev,sumo]"
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
