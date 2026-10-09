# Reproducible research methodology - Rome Mobility

## Version 1: repeated SUMO experiments (implemented)

From the browser choose real SUMO, select study area and day/hour/demand,
select a timing strategy, and press the research button.
The app runs three paired seeds by default: 42, 43 and 44.
For each seed it runs (a) manual all-zero-offset baseline, and
(b) the chosen timing strategy under identical demand and seed.
It provides a zip archive containing report.md, experiment.json,
runs.csv, and paired_differences.csv. The network content is fingerprinted
with SHA256. This is simulation data ONLY, not observed traffic.

The paired bootstrap interval is a descriptive sensitivity interval
over the chosen seeds, not a confidence statement about conditions in Rome.
The completed-trip travel time means can be biased when some vehicles
are still travelling at the simulation cutoff; always report completion
fractions, queue counts and vehicles still on road. The app warns when
less than 85 percent of inserted vehicles finish a run.

## What must come next (not yet implemented)

1. Establish exact corridor and inventory every controlled junction,
   pedestrian crossing, lane and affected side road.
2. Find legally reusable and time-aligned measured vehicle counts,
   turning volumes, measured travel-time distributions and actual
   signal-cycle programs.
3. Design a separate observations schema with provenance, units,
   observation date, location/segment, time resolution and quality flag.
4. Use measured data to calibrate demand, turn fractions and vehicle
   type mix; validate using independent days not used in calibration.
5. Expand SUMO output with TripInfo, edgeData and (selectively) FCD.
   Add per-corridor-direction and per-junction queue metrics, emissions,
   transit and pedestrian constraints. Avoid claiming benefits without
   a suitable calibrated emission class mix.
6. Compare morning, midday, PM, weekends and demand/rain sensitivity.
   Optimize only within safely verified signal plans and technical
   supervisory review.

SUMO documentation:
https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html
https://sumo.dlr.de/docs/Simulation/Output/Lane-_or_Edge-based_Traffic_Measures.html
https://sumo.dlr.de/docs/Simulation/Output/StatisticOutput.html

Roma Mobilita open-data page:
https://romamobilita.it/sistemi-e-tecnologie/open-data/

That portal publicly advertises public transport AVM/open data; it does
not establish access to actual Via Salaria vehicle counts or controller plans.
Those measurements must be independently found or requested.
