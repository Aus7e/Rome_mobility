# Overpass API import troubleshooting

The web app requires an actual OSM road file before it can run SUMO:
\`data/raw/salaria.osm.xml\`, then \`data/sumo/salaria.net.xml\`.

## HTTP 406 Not Acceptable

Since April 2026, the public \`overpass-api.de\` instance has at times rejected
scripts without an application-specific User-Agent/Referer. The downloader now
identifies Rome Mobility and automatically falls back to two globally covering
instances:

1. https://overpass-api.de/api/interpreter
2. https://overpass.private.coffee/api/interpreter
3. https://overpass.osm.jp/api/interpreter

A successful HTTP 200 response is only accepted if it contains valid OSM XML
with roads and coordinate nodes and no Overpass runtime-error remark. An
invalid error page is never saved as the network.

## Run from the repository directory

    git pull origin main
    docker compose up --build -d
    docker compose exec salaria-lab python scripts/bootstrap_sumo.py
    docker compose exec salaria-lab ls -lh data/raw/salaria.osm.xml data/sumo/salaria.net.xml

Then refresh http://localhost:8000 and choose SUMO.
The web button **Prepara rete reale SUMO** runs the same setup.

For a new download even when there is already an OSM XML file:

    docker compose exec salaria-lab python scripts/bootstrap_sumo.py --force-download

If Overpass providers remain unavailable, obtain a valid OSM XML file for the
same study area by other legitimate means, place it as
\`data/raw/salaria.osm.xml\` and run setup again. Do not place arbitrary HTML
or a PDF there: the downloader validates the file.

This only supplies *geometry and candidate traffic lights*, not observed
hourly flow counts or actual traffic-signal timing programs.

Sources:
- https://community.openstreetmap.org/t/overpass-api-error-406/143198
- https://wiki.openstreetmap.org/wiki/Overpass_API
