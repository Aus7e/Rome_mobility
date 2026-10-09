"""Download archival Rome signal point features and compare near the current corridor.

ArcGIS data layer metadata reports last edit in February 2020; NOT current truth.
Requires Internet connection. Results are stored locally under data/raw (gitignored).
"""
import argparse
import asyncio
import csv
import json
from pathlib import Path

import httpx

from app.network import BBOX, ROOT, closest_fraction, current_network

URL = "https://services2.arcgis.com/NZMqCJwY3kMjFOqf/ArcGIS/rest/services/semafori/FeatureServer/0/query"


async def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold-m", type=float, default=100)
    args = parser.parse_args()
    north, east = BBOX[2], BBOX[3]
    south, west = BBOX[0], BBOX[1]
    params = {
        "f": "json", "where": "1=1", "outFields": "FID,COD_IMP,TIPO,VIA_1,VIA_2,Lat,Long",
        "geometry": json.dumps({"xmin":west,"ymin":south,"xmax":east,"ymax":north,
                                "spatialReference":{"wkid":4326}}),
        "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "false",
        "resultRecordCount": 2000
    }
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        response = await client.get(URL, params=params)
        response.raise_for_status()
        data = response.json()
    if "error" in data:
        raise RuntimeError("ArcGIS error: "+str(data["error"]))
    if data.get("exceededTransferLimit"):
        raise RuntimeError("ArcGIS truncated results; pagination required")
    network = current_network()
    accepted=[]
    for f in data.get("features",[]):
        a=f.get("attributes",{})
        lat,lon=a.get("Lat"),a.get("Long")
        if not isinstance(lat,(float,int)) or not isinstance(lon,(float,int)):
            continue
        distance,s=closest_fraction([lat,lon],network["points"])
        if distance<=args.threshold_m:
            accepted.append({"id":a.get("COD_IMP"),"type":a.get("TIPO"),
                             "via_1":a.get("VIA_1"),"via_2":a.get("VIA_2"),
                             "lat":lat,"lon":lon,"s_m":round(s,1),
                             "distance_m":round(distance,1),
                             "source":"arcgis_signal_archive_last_edit_2020"})
    accepted.sort(key=lambda x:x["s_m"])
    dest=ROOT/"data"/"raw"
    dest.mkdir(parents=True,exist_ok=True)
    (dest/"signal_archive_2020.json").write_text(json.dumps({
        "source":URL,"network_source":network["source"],"results":accepted
    },ensure_ascii=False,indent=2),encoding="utf-8")
    with (dest/"signal_archive_2020.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=["id","type","via_1","via_2","lat","lon","s_m","distance_m","source"])
        writer.writeheader()
        writer.writerows(accepted)
    print("Archive features in local corridor radius:",len(accepted))
    print("Output:",dest/"signal_archive_2020.csv")
    if network["source"]=="synthetic_demo":
        print("WARNING: comparison uses synthetic fallback geometry. Import OSM first.")


if __name__=="__main__":
    asyncio.run(run())
