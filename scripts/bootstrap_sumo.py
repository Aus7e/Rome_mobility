
"""Optional scientific workbench: download raw OSM and build a SUMO network.

Not connected to the browser's demonstration engine.
Needs SUMO netconvert + SUMO_HOME to build a route demand later.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

import httpx

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser(description="Import road network surrounding Salaria into SUMO")
    parser.add_argument("--download-only",action="store_true")
    args=parser.parse_args()
    raw=ROOT/"data"/"raw"/"salaria.osm.xml"
    net=ROOT/"data"/"sumo"/"salaria.net.xml"
    raw.parent.mkdir(parents=True,exist_ok=True)
    net.parent.mkdir(parents=True,exist_ok=True)
    if not raw.exists():
        # Full road network, not merely ways whose name contains 'Salaria'.
        query='[out:xml][timeout:180];(way["highway"](41.936,12.487,42.006,12.537););(._;>;);out body;'
        url="https://overpass-api.de/api/interpreter"
        with httpx.Client(timeout=240) as client:
            response=client.post(url,data={"data":query})
            response.raise_for_status()
            if b"<osm" not in response.content[:300]:
                raise RuntimeError("Overpass did not return OSM XML")
            raw.write_bytes(response.content)
        print("OSM:",raw)
    if args.download_only:
        return
    binary=shutil.which("netconvert")
    if not binary:
        sys.exit("netconvert missing. Install SUMO or run with --download-only.")
    subprocess.run([binary,"--osm-files",str(raw),"--output-file",str(net),
                    "--geometry.remove","--ramps.guess","--roundabouts.guess",
                    "--junctions.join","--tls.guess-signals","--tls.discard-simple"],check=True)
    print("SUMO network:",net)
    print("This network is uncalibrated; OSM default signal phases are NOT observed field plans.")


if __name__=="__main__":
    main()
