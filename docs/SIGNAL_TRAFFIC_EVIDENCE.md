# Via Salaria — audit signals and hourly traffic evidence

## What is actually verified?

The simulator does not yet contain a field-verified census of all currently
operational Via Salaria signal installations. OSM node tags represent
crowdsourced mapping evidence, not the municipality's controller registry.
One junction may have several nodes/head positions; SUMO may have generated
controllers that do not exist in real life.

An endpoint `/api/signals/inventory` now lists actual tagged OSM nodes,
their source links and proximity matches to generated SUMO signal controllers,
including unmatched candidates. Its historical 2020 ArcGIS layer remains a
separate archival source, NEVER described as contemporary.

To populate a fresh OSM network including standalone signal nodes:
`docker compose exec salaria-lab python scripts/bootstrap_sumo.py --force-download`
(only if necessary and Internet access works). A failed download leaves the
existing validated network in place.

An optional historical archive can be inspected using
`docker compose exec salaria-lab python scripts/download_signal_archive.py`;
its metadata indicated last edit in February 2020, and it is not a replacement
for surveying current signals.

## Import a real demand profile

Choose traffic source 'hourly_counts' in the dashboard and upload your
authorized CSV. The required header is:

```
day_type,hour,direction,vehicles_per_hour,kind,source,observation_period,licence
```

No invented numerical examples ship with the project.

- day_type: weekday or weekend (a representative group, not a literal date)
- hour: local traffic hour 0–23 (Europe/Rome)
- direction: outbound towards GRA or inbound towards Centro
- vehicles_per_hour: integer 0–8000 for the source observation location
- kind: observed_count (field counter/survey) or provider_estimate (vendor
  modelled vehicle volume; not field-measured)
- source, observation_period, licence: all required for traceability

The simulation requires both directions at the exact selected day_type/hour.
It uses the imported rates directly, bypassing the synthetic time-of-day
multipliers. If either is missing, it returns an error (no interpolation,
no silent replacement). Replacing the file is atomic and invalid input does
not wipe prior valid data. Research exports include the dataset SHA256.

**Not calibrated yet:** Actual intersection operating status, complete
phase timing, lateral-road flows and turning shares, route generation and
end-to-end OD matrices. Observed two-way counts at one point do not calibrate
the entire network. The resulting scenarios remain experimental.

## Next real-world steps

1. Verify mapped controller positions against a current municipal
   installation inventory and site inspections.
2. Obtain 5–60 minute directional loop counts with known count stations,
   coordinates, dates and metadata for each major approach.
3. Use TomTom Traffic Volume historical model-based volume estimates
   if licensed, keeping them distinct from roadside observations.
4. Use TomTom Traffic Stats Route Analysis historical speed and travel-time
   distributions for independent validation, not as a proxy for counts.
5. Implement per-junction turning matrices and field-measured phasing;
   calibrate with independent holdout dates before claiming impacts.

Vendor documentation:
https://docs.tomtom.com/traffic-stats/documentation/api/traffic-volume
https://docs.tomtom.com/traffic-stats/documentation/api/route-analysis

OSM traffic-light tagging:
https://wiki.openstreetmap.org/wiki/Key:traffic_signals:direction
