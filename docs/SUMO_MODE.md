# Scientific SUMO mode

## One-command installation

Prerequisites: Docker with Docker Compose and Internet for OSM preparation.

    docker compose up --build

Open http://localhost:8000, select **SUMO reale**, then click **Prepara rete reale SUMO**.
The first preparation calls Overpass and netconvert; it can take minutes.
After success, retry SUMO mode and click **Avvia simulazione**.

For local Python installation: install the SUMO binary (\`sumo\`, \`netconvert\`) and
\`pip install -e ".[dev,sumo]"\`; run \`python scripts/bootstrap_sumo.py\`.

### How it works

1. \`bootstrap_sumo.py\` downloads all OSM highways and their nodes within an experimental study bbox.
2. \`netconvert\` builds a directional lane/junction/TLS network from actual OSM geometry.
3. A SUMO worker reads SUMO edges, detects Via Salaria by road name, identifies incoming/outgoing and side-road edges.
4. TraCI builds routable OD pairs using \`simulation.findRoute\`; side-road pairs support intersection turning traffic.
5. Demand rates scale with day/hour. All values are *assumptions* until measured flows are provided.
6. TraCI runs vehicle following, junction conflict logic and lane-level connections from the imported net.
7. Signals are modified with \`setProgramLogic\` while retaining each imported phase's exact R/Y/G states and amber/all-red durations. Nonlinear or incompatible plans are skipped with warnings.
8. Recorded vehicle positions become geographic lon/lat via SUMO projection, and the Three.js client replays those coordinates in the street model.
9. The API runs jobs sequentially with status polling; results include warnings and validity limitations.

This does **NOT** claim a validated current Rome timing plan or real-time traffic.
Full SUMO simulation and OSM geometry do not compensate for missing calibrated traffic counts, real controller schedules, measured turning flows, or empirical rain-braking effects.
Average delay uses a per-trip freeflow length/speed reference, including turning trips.
Simulation ends at the requested time; not all inserted trips are necessarily complete. Treat average travel time only as a completed-trip metric and examine completion rate when comparing scenarios.

### API

- GET \`/api/sumo/status\`
- POST \`/api/sumo/setup\`
- GET \`/api/sumo/network?segment=full\`
- POST \`/api/sumo/jobs\` with a SimulationRequest
- POST \`/api/sumo/compare/jobs\` with same schema
- GET \`/api/sumo/jobs/{id}\` (progress, state)
- GET \`/api/sumo/jobs/{id}/result\` once complete

Workers reside in memory, maximum two pending/running; this service should be run as
a **single-process local laboratory**, not an unauthenticated public deployment.
No production API credentials or sensitive files are required.

**Compatibility:** OD route discovery now uses sumolib Dijkstra shortest paths, not the version-dependent TraCI `simulation.findRoute` wire protocol. This is valid across common SUMO distribution versions.
