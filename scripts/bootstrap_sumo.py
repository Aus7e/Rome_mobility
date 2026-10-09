"""Download the Via Salaria OSM study area and convert it to a SUMO network.

The public Overpass main instance sometimes returns HTTP 406 to automated clients.
Identify this app, use current worldwide alternative instances and never cache a
server error page as an OSM file. Public OSM is not calibrated traffic data.
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import httpx

ROOT = Path(__file__).resolve().parent.parent
OSM_FILE = ROOT / "data" / "raw" / "salaria.osm.xml"
NET_FILE = ROOT / "data" / "sumo" / "salaria.net.xml"

# Worldwide Overpass API servers, documented on the OpenStreetMap wiki.
# Do not use regional-only servers (e.g. Switzerland) for Rome.
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
)
OVERPASS_QUERY = (
    '[out:xml][timeout:180];'
    '(way["highway"](41.936,12.487,42.006,12.537););'
    '(._;>;);out body;'
)
REQUEST_HEADERS = {
    "User-Agent": "RomeMobility-SalariaLab/0.3 (+https://github.com/Aus7e/Rome_mobility)",
    "Referer": "https://github.com/Aus7e/Rome_mobility",
    "Accept": "application/xml, text/xml;q=0.9, */*;q=0.8",
}


def validate_osm(payload: bytes) -> None:
    """Verify valid XML and the presence of road ways and their referenced nodes.

    Overpass can return HTTP 200 containing a non-OSM HTML error or an OSM XML
    <remark> element describing a timeout. Neither is safe for netconvert.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("Overpass response is not valid XML") from exc
    if root.tag != "osm":
        raise ValueError("Overpass response is not OSM XML (expected <osm>)")
    remarks = [el.text.strip() for el in root.findall(".//remark") if el.text]
    if remarks:
        raise ValueError("Overpass reported: " + " | ".join(remarks)[:250])
    if not any(node.tag == "way" for node in root):
        raise ValueError("OSM response contains no road ways")
    if not any(node.tag == "node" for node in root):
        raise ValueError("OSM response contains no coordinates (nodes)")


def download_osm(
    client: httpx.Client,
    endpoints: tuple[str, ...] = OVERPASS_ENDPOINTS,
    query: str = OVERPASS_QUERY,
) -> tuple[bytes, str]:
    """Attempt each provider once; give the user actionable errors if all fail.

    Never automatically retry the same overloaded server repeatedly.
    """
    problems: list[str] = []
    for endpoint in endpoints:
        print(f"OSM: contacting {endpoint}", flush=True)
        try:
            response = client.post(
                endpoint,
                data={"data": query},
                headers=REQUEST_HEADERS,
            )
            if response.status_code != 200:
                problems.append(f"{endpoint}: HTTP {response.status_code}")
                continue
            payload = response.content
            validate_osm(payload)
            print(f"OSM: received and validated {len(payload):,} bytes", flush=True)
            return payload, endpoint
        except (httpx.HTTPError, ValueError) as exc:
            problems.append(f"{endpoint}: {str(exc)[:180]}")
    raise RuntimeError(
        "Cannot download OpenStreetMap from available Overpass providers. "
        "The request may be blocked, rate-limited or the servers overloaded. "
        "Try again later or manually provide data/raw/salaria.osm.xml. "
        "Attempts: " + " | ".join(problems)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Rome Via Salaria road network into SUMO")
    parser.add_argument("--download-only", action="store_true",
                        help="Download OSM XML but do not run netconvert")
    parser.add_argument("--force-download", action="store_true",
                        help="Download again even when a valid cached OSM file exists")
    args = parser.parse_args()
    OSM_FILE.parent.mkdir(parents=True, exist_ok=True)
    NET_FILE.parent.mkdir(parents=True, exist_ok=True)

    cached_valid = False
    if OSM_FILE.is_file() and not args.force_download:
        try:
            validate_osm(OSM_FILE.read_bytes())
            cached_valid = True
            print(f"OSM: using existing validated local file {OSM_FILE}", flush=True)
        except ValueError as exc:
            print(f"OSM: invalid cached file ({exc}); downloading a new copy", flush=True)

    if not cached_valid:
        timeout = httpx.Timeout(connect=20.0, read=240.0, write=30.0, pool=30.0)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            payload, endpoint = download_osm(client)
        # Write only a fully validated payload and replace cache atomically.
        partial = OSM_FILE.with_suffix(OSM_FILE.suffix + ".part")
        try:
            partial.write_bytes(payload)
            partial.replace(OSM_FILE)
        finally:
            partial.unlink(missing_ok=True)
        print(f"OSM: source {endpoint} -> {OSM_FILE}", flush=True)

    if args.download_only:
        return

    netconvert = shutil.which("netconvert")
    if not netconvert:
        sys.exit("netconvert missing. Install SUMO or use --download-only.")

    print("SUMO: building lane-level road network with netconvert", flush=True)
    subprocess.run([
        netconvert, "--osm-files", str(OSM_FILE), "--output-file", str(NET_FILE),
        "--ramps.guess", "--roundabouts.guess", "--junctions.join",
        "--tls.guess-signals", "--tls.discard-simple",
        "--output.street-names", "true",
    ], check=True)
    print(f"SUMO network created: {NET_FILE}", flush=True)
    print("WARNING: OSM signal phases and hourly traffic are not measured Rome data.")


if __name__ == "__main__":
    main()
