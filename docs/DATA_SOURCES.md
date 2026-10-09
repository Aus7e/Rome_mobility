
# Data register and validation plan

| Dataset | What we can obtain | Provider | Access | Status |
| --- | --- | --- | --- | --- |
| Salaria road shape/ways/traffic_signals | Segments, approximate signal node mapping | OpenStreetMap contributors | Overpass API; ODbL | Implemented on-demand; NOT yet ground-truthed |
| Time/hour precipitation near Salaria | Precipitation mm/h (gridded historical model) | Open-Meteo / ECMWF | Open-Meteo Historical Weather API, see provider terms | Implemented on-demand |
| Posted speed, turning lanes, priority | Available tagged attributes, completeness varies | OpenStreetMap contributors | OSM tags | Potential enhancement |
| Rome signal installation inventory | Roma Servizi per la Mobilità static GIS dataset | Roma Mobilità | https://romamobilita.it/sistemi-e-tecnologie/open-data/ | Manual discovery / validation required |
| Measured car counts/turning movements | Per interval, direction and intersection | Municipality / field counts / qualified data vendor | Request or survey | NOT acquired |
| Observed phase diagrams and UTC schedules | Cycle, green split, offsets, pedestrian phases | Roma Mobilità | Request explicitly | NOT acquired |
| Historic travel times | Per link and time slice | TomTom Traffic Stats | https://www.tomtom.com/products/traffic-stats/ | Vendor product, NOT acquired |
| Open public transit feeds | Bus operations GTFS and RT positions | Roma Mobilità | https://romamobilita.it/sistemi-e-tecnologie/open-data/ | Potential enhancement, not road vehicle counts |
| SUMO network | Simulator-compatible road junctions | Local netconvert using OSM | https://sumo.dlr.de/docs/ | Generator script included, execution requires SUMO |

## What is real vs synthetic in this prototype?

- OSM, if requested and successful, is a geospatial source but NOT a certified intersection audit.
- OSM traffic_signals nodes can represent stop line signals, pedestrian lights or multiple heads at one intersection. Aggregation by 60 m is an **assumption**, not a confirmed controller inventory.
- The demonstration fallback road is **illustrative**, including all four fake signal positions. Never cite them as actual Salaria intersections.
- Date/hour changes use assumed hourly multipliers, not observed data. Weekday AM 1.65; weekday PM 1.50; weekend lower. The 500 vehicles/h/direction base is hypothetical.
- Rain changes maximum speed and braking envelope under assumed equations, not a calibrated friction model.
- The backend lightweight two-way single-corridor simulator does not model turning vehicles, side-road capacity, lane changes, pedestrians, transit priority, collision risk, queue spillback across different links or legal signal design.
- Baseline means offset 0 at all demo controller points, NOT the actual current city configuration.

## Empirical next steps

1. Audit intersections and pedestrian crossings between Prati Fiscali and GRA; add source ID, location, field observation date and confidence.
2. Request signal schedules: cycle plans by hour/day/holiday, offset, green split, amber/all-red safety intervals, coordination constraints.
3. Acquire counts/turning movements and speed distributions at matched 5–15-minute times.
4. Calibrate speed–rain relationship while controlling for day, time, demand and road works.
5. Build and run SUMO network using scripts/bootstrap_sumo.py and create *calibrated* OD traffic routes.
6. Compare scenarios using many random seeds and report distributions, not a single run.
7. Audit trade-offs affecting side roads, safety and pedestrian phases BEFORE any real-world engineering application.

### Licensing / attribution
© OpenStreetMap contributors, ODbL: https://www.openstreetmap.org/copyright
Open-Meteo: https://open-meteo.com/en/terms; verify usage and attribution limits for deployment.
Data from traffic analytics vendors may be subject to proprietary usage terms.


## Additional historical ArcGIS signal layer (not contemporary)

Public ArcGIS feature service: https://services2.arcgis.com/NZMqCJwY3kMjFOqf/ArcGIS/rest/services/semafori/FeatureServer/0

This layer exposes fields including COD_IMP, TIPO, VIA_1, VIA_2, Lat and Long. Its metadata reports last editing on **19 February 2020**. Treat it as an archival comparator only: its provenance, geographic completeness, currency and licensing must be checked before using it as official current inventory. See scripts/download_signal_archive.py for an optional local export and comparison against the active OSM/demo axis.


## October 2026: mapped signal inventory and actual hourly inputs

The app now reads tagged OSM signal nodes from the downloaded OSM XML
(including standalone tagged nodes on a newly downloaded network) and
proximity-matches them to SUMO generated controllers. The inventory endpoint
reports unmatched OSM and SUMO entries. A proximity match cannot establish
whether an active, field-verified municipal controller exists.

Only authorized locally imported hourly directional count data can enable
`traffic_source=hourly_counts`; each record explicitly distinguishes
`observed_count` from `provider_estimate`, source, period and licence.
No data is bundled or invented. Missing hour/direction fails closed.
A model using these data remains uncalibrated in side-road flows, OD routes,
junction operations, and traffic signal time plans.

Recommended licensed historical data sources:
- https://docs.tomtom.com/traffic-stats/documentation/api/traffic-volume
- https://docs.tomtom.com/traffic-stats/documentation/api/route-analysis

Traffic Stats Route Analysis yields historical route travel times and probe-
based speeds but not directional vehicle counts. Traffic Volume yields
model-estimated counts, not manual roadside counts.
