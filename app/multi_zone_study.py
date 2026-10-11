"""Sequential per-zone orchestration of 500-run SUMO experiments.

A full Rome batch is 500 x N READY zones (up to 5,500 real SUMO runs),
NOT 500 runs shared across the city. Overlapping study rectangles are
independent; never add their trip counts or rank absolute scores citywide.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from html import escape
import io
import json
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from app.areas import AREAS
from app.auto_study import AutoStudyRequest, run_auto, report_zip, COUNT


def select_study_areas(scope: str, selected: str, ready: dict[str, bool]) -> list[str]:
    if selected not in AREAS:
        raise ValueError("Unknown Rome study zone")
    if scope == "selected":
        if not ready.get(selected, False):
            raise ValueError("Prepare the SUMO network for "+selected+" first")
        return [selected]
    if scope != "all_prepared":
        raise ValueError("Unknown automatic study scope")
    chosen=[a for a in AREAS if ready.get(a,False)]
    if not chosen:
        raise ValueError("No SUMO region is ready. Prepare at least one Rome zone.")
    return chosen


def _csv(entries, fields):
    buf=io.StringIO()
    writer=csv.DictWriter(buf,fieldnames=fields,extrasaction="ignore")
    writer.writeheader();writer.writerows(entries)
    return buf.getvalue()


def batch_summary_html(manifest: dict) -> str:
    safe=lambda v:escape(str(v),quote=True)
    rows=[]
    for item in manifest["zones"]:
        rows.append(
            "<tr><td>"+safe(item["name"])+"</td><td>"+safe(item["status"])+
            "</td><td>"+safe(item["simulations"])+
            "</td><td>"+safe(item["accepted_periods"])+
            "</td><td>"+safe(item["tomtom_success"])+"/"+
            safe(item["tomtom_requested"])+
            "</td><td>"+safe(item.get("error",""))+"</td></tr>")
    return ("<!doctype html><html lang='it'><meta charset='utf-8'>"
            "<title>Rome Mobility · Studio multi-zona</title>"
            "<style>body{font:15px system-ui;max-width:1050px;margin:30px auto;"
            "padding:0 18px;color:#243d56}table{border-collapse:collapse;width:100%}"
            "td,th{border:1px solid #d3dce8;padding:8px;text-align:left}"
            "th{background:#edf4ff}h1{color:#185bbd}em{color:#8c4420}</style>"
            "<h1>Rome Mobility — esperimenti per zone di Roma</h1>"
            "<p><strong>500 simulazioni SUMO per ogni zona completata.</strong> "
            "Le aree sono reti separate, con confini anche sovrapposti; "
            "i punteggi NON sono confrontabili direttamente fra quartieri.</p>"
            "<p>Totale completato: "+safe(manifest["simulations"])+
            " / "+safe(manifest["planned_simulations"])+
            " · Zone completate: "+safe(manifest["completed_areas"])+
            " / "+safe(len(manifest["requested_areas"]))+"</p>"
            "<table><tr><th>Zona</th><th>Stato</th><th>Run SUMO</th>"
            "<th>Fasce migliorate /10</th><th>TomTom / richieste</th>"
            "<th>Note</th></tr>"+"".join(rows)+"</table>"
            "<p><em>Tarature sperimentali, non approvate per l'uso reale. "
            "Il riferimento TomTom è una previsione sui tempi di percorrenza "
            "di una direttrice e non una misura del volume di traffico.</em></p>"
            "<p>I report HTML, CSV e JSON di ogni zona sono in "
            "aree/&lt;zona&gt;/. Vedere anche riepilogo.csv.</p></html>")


def run_area_batch(design: AutoStudyRequest, *, progress=None,
                   run_zone=run_auto, make_zip=report_zip) -> dict:
    """Return sanitized ZIP bytes & metadata. Never materialize the user's key."""
    zones=list(design.resolved_areas)
    if not zones:
        raise ValueError("The list of ready zones must be resolved by the server")
    if len(set(zones))!=len(zones) or any(area not in AREAS for area in zones):
        raise ValueError("Invalid or duplicate study zone")
    if design.scope=="selected" and zones!=[design.scenario.area]:
        raise ValueError("Selected study must use exactly its selected area")

    manifest={
        "schema_version":"rome-multi-zone-500-1",
        "created_at_utc":datetime.now(timezone.utc).isoformat(),
        "scope":design.scope,
        "requested_areas":zones,
        "runs_per_area":COUNT,
        "planned_simulations":len(zones)*COUNT,
        "simulations":0,
        "completed_areas":0,
        "failed_areas":0,
        "zones":[],
        "methodology":"Independent SUMO zone experiments. No citywide score aggregation.",
        "tomtom_methodology":(
            "At most 4 area-focus Routing requests per zone "
            "(weekday 08:00 and 17:00 in two directions); "
            "predicted times not measured hourly vehicle counts."
        ),
        "limitations":[
            "Study zones overlap and lack inter-zone boundary flow calibration.",
            "Do not sum regional trip counts or compare unnormalized scores across zones.",
            "Signal phases and demand are experimental unless independently validated.",
            "TomTom Routing predicts the chosen focus-road times, not a regionwide traffic census.",
        ],
    }
    buff=io.BytesIO()
    safe_key=design.api_key.get_secret_value()
    with ZipFile(buff,"w",ZIP_DEFLATED,compresslevel=6) as archive:
        for index,area in enumerate(zones):
            def zone_progress(value,msg):
                if progress:
                    progress((index+max(0,min(1,float(value))))/len(zones),
                             f"Zona {index+1}/{len(zones)} · {AREAS[area]['name']}: {msg}")
            if progress:
                zone_progress(0,f"inizio dei 500 run SUMO per {area}")
            zone_scenario=design.scenario.model_copy(update={
                "area":area,
                "segment":"full" if area!="salaria" else design.scenario.segment,
                # Existing TLS overrides from a different zone cannot be reused.
                "overrides":{},
            })
            per_zone=design.model_copy(update={
                "scope":"selected","scenario":zone_scenario,
                "resolved_areas":[area],
            })
            item={
                "id":area,
                "name":AREAS[area]["name"],
                "status":"failed",
                "simulations":0,
                "accepted_periods":0,
                "tomtom_success":0,
                "tomtom_requested":4,
                "error":"",
                "network_sha256":"",
            }
            try:
                report=run_zone(per_zone,progress=zone_progress,
                                tomtom_max_periods=2)
                if report["area"]!=area or report["simulations"]!=COUNT:
                    raise ValueError("SUMO area output does not match requested zone")
                zonezip=make_zip(report)
                with ZipFile(io.BytesIO(zonezip)) as nested:
                    # Names are static known content from report_zip; no Zip Slip.
                    for name in nested.namelist():
                        if "/" in name or name.startswith("."):
                            raise ValueError("Unexpected report filename")
                        archive.writestr(f"aree/{area}/{name}",nested.read(name))
                item.update({
                    "status":"complete",
                    "simulations":COUNT,
                    "accepted_periods":report["quality"]["accepted_periods"],
                    "tomtom_success":report["tomtom_success"],
                    "tomtom_requested":report["tomtom_requested"],
                    "network_sha256":report["network_sha256"],
                })
                manifest["simulations"]+=COUNT
                manifest["completed_areas"]+=1
            except Exception as error:
                # Most errors are unavailable regional networks/routes.
                # Do not leak secrets even if provider libraries include URLs.
                message=str(error).replace(safe_key,"[REDACTED]") if safe_key else str(error)
                item["error"]=message[:180]
                manifest["failed_areas"]+=1
            manifest["zones"].append(item)
            if progress:
                progress((index+1)/len(zones),
                         f"Zone terminate: {index+1}/{len(zones)}; "
                         f"run completati: {manifest['simulations']}/{manifest['planned_simulations']}")
        archive.writestr("riepilogo.csv",_csv(manifest["zones"],[
            "id","name","status","simulations","accepted_periods",
            "tomtom_success","tomtom_requested","network_sha256","error",
        ]))
        archive.writestr("relazione_generale.html",batch_summary_html(manifest))
        archive.writestr("manifest.json",json.dumps(manifest,indent=2,ensure_ascii=False))
    if not manifest["completed_areas"]:
        raise RuntimeError("No Rome zone could complete its 500-run study. "
                           "Verify each region's OSM/SUMO network and routability.")
    return {
        "manifest":manifest,
        "download_bytes":buff.getvalue(),
        "total_runs":manifest["simulations"],
        "tomtom_success":sum(z["tomtom_success"] for z in manifest["zones"]),
        "tomtom_requested":sum(z["tomtom_requested"] for z in manifest["zones"]),
        "accepted_periods":sum(z["accepted_periods"] for z in manifest["zones"]),
    }
