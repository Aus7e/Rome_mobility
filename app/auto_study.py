"""500-run exploratory signal optimization (10 periods x 10 policies x 5 seeds).

SUMO is independent for every run. A candidate is selected on 3 seeds and
evaluated against the baseline on 2 held-out, identical-demand seeds.
TomTom historical-typical route predictions inform context, NOT vehicle counts.
"""
from __future__ import annotations

import csv
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html import escape
import hashlib
import io
import json
import multiprocessing
from pathlib import Path
import statistics
from zipfile import ZipFile, ZIP_DEFLATED
from pydantic import BaseModel, Field, SecretStr, model_validator
from app.models import SimulationRequest
from app.areas import network_path
from app.research import allowed_workers, values_of
from app.sumo_runner import run_sumo
from app.traffic_reference import get_typical_traffic

COUNT = 500
SCORE_DESCRIPTION = "delay_s + 0.08 × stopped_vehicles_peak + 4 × stops_per_trip + 250 × (1 - trip_completion_rate)"
HOURS = [("weekday", (7, 8, 12, 17, 19)), ("weekend", (9, 12, 15, 18, 21))]


class AutoStudyRequest(BaseModel):
    scenario: SimulationRequest
    api_key: SecretStr
    workers: int = Field(2, ge=1, le=8)

    @model_validator(mode="after")
    def check(self):
        if not self.api_key.get_secret_value().strip():
            raise ValueError("An authorized TomTom Routing key is required")
        if self.scenario.seed > 999_996:
            raise ValueError("Seed must not exceed 999996 for five runs")
        return self


def study_periods(base):
    result = []
    for kind, hours in HOURS:
        target = 0 if kind == "weekday" else 5
        day = base.day + timedelta(days=(target - base.day.weekday()) % 7)
        for hour in hours:
            result.append({"id":f"{kind}_{hour:02d}","type":kind,
                           "date":day.isoformat(),"hour":hour})
    return result


def policies():
    p = [{"id":"baseline","mode":"manual","cycle_s":90,"green_s":45}]
    for mode in ("manual","wave_outbound","wave_inbound"):
        for cycle, green in ((75, 35),(90, 50),(105, 60)):
            p.append({"id":f"{mode}_{cycle}_{green}","mode":mode,
                      "cycle_s":cycle,"green_s":green})
    return p


def plan(base):
    return [{"period":period,"policy":p,"seed":base.seed+i}
            for period in study_periods(base)
            for p in policies() for i in range(5)]


def _run_one(base, task, net_path):
    period, p = task["period"], task["policy"]
    sim = base.model_copy(update={
        "day":date.fromisoformat(period["date"]),
        "hour":period["hour"],"seed":task["seed"],
        "mode":p["mode"],"cycle_s":p["cycle_s"],
        "green_s":p["green_s"],"overrides":{},
    })
    outcome = run_sumo(sim, frames=False, net_path=net_path)
    return {"period":period["id"],"date":period["date"],"hour":period["hour"],
            "day_type":period["type"],"policy":p["id"],"seed":task["seed"],
            "metrics":values_of(outcome["metrics"]),
            "warnings":outcome.get("warnings",[])[:5]}


def model_score(m):
    required=("avg_delay_s","max_queued_vehicles","mean_stops_per_trip","completion_rate")
    if any(not isinstance(m.get(k),(int,float)) for k in required):
        return None
    if m["completion_rate"]<.85 or not m.get("controlled_lights"):
        return None
    # No false policy win when a simulation terminated with unresolved cars.
    if m.get("drain_timed_out") and m["completion_rate"] < .98:
        return None
    return round(m["avg_delay_s"]+.08*m["max_queued_vehicles"]
                 +4*m["mean_stops_per_trip"]+250*(1-m["completion_rate"]),4)


def select_winners(rows, base):
    by_key={(r["period"],r["policy"],r["seed"]):r for r in rows}
    seeds=[base.seed+i for i in range(5)]
    ranking=[]
    decisions=[]
    for period in study_periods(base):
        c=period["id"]
        scored=[]
        for p in policies():
            train=[by_key[(c,p["id"],s)] for s in seeds[:3]]
            hold=[by_key[(c,p["id"],s)] for s in seeds[3:]]
            train_vals=[row["score"] for row in train]
            held_vals=[row["score"] for row in hold]
            record={
                "period":c,"policy":p["id"],
                "training_score":round(statistics.mean(train_vals),4)
                    if all(v is not None for v in train_vals) else None,
                "holdout_score":round(statistics.mean(held_vals),4)
                    if all(v is not None for v in held_vals) else None,
                "training_completion":round(statistics.mean(
                    r["metrics"].get("completion_rate") or 0 for r in train),4),
                "holdout_completion":round(statistics.mean(
                    r["metrics"].get("completion_rate") or 0 for r in hold),4),
            }
            ranking.append(record)
            scored.append(record)
        baseline=scored[0]
        winner=min((x for x in scored[1:] if x["training_score"] is not None),
                   key=lambda x:x["training_score"],default=None)
        improvement=(winner and winner["holdout_score"] is not None
                     and baseline["holdout_score"] is not None
                     and winner["holdout_score"]<baseline["holdout_score"]
                     and winner["holdout_completion"]>=baseline["holdout_completion"]-.02)
        delta=(round(winner["holdout_score"]-baseline["holdout_score"],4)
               if winner and winner["holdout_score"] is not None
               and baseline["holdout_score"] is not None else None)
        chosen=winner["policy"] if improvement else "baseline"
        decisions.append({
            "period":c,"date":period["date"],"hour":period["hour"],
            "day_type":period["type"],"candidate":winner["policy"] if winner else None,
            "recommended":chosen,"accepted":bool(improvement),"holdout_delta":delta,
            "reason":"Improved on unseen simulation seeds"
                     if improvement else "No reliable out-of-sample improvement",
        })
    return {"rankings":ranking,"decisions":decisions,
            "training_seeds":seeds[:3],"validation_seeds":seeds[3:]}


def _fetch_tomtom(design, progress=None, provider=get_typical_traffic):
    from app.sumo_runner import load_network
    net=load_network("full",network_path("salaria"))
    references=[]
    notes=[]
    for i,period in enumerate(study_periods(design.scenario)):
        for direction in ("outbound","inbound"):
            if progress:
                progress(.025*(2*i+(direction=="inbound"))/20,
                         f"TomTom: {period['id']} {direction}")
            try:
                row=provider(date.fromisoformat(period["date"]),period["hour"],direction,
                             network=net,api_key=design.api_key.get_secret_value())
                references.append({
                    "period":period["id"],"direction":direction,
                    "historical_typical_s":row["travel_time_typical_s"],
                    "freeflow_s":row.get("travel_time_freeflow_s"),
                    "length_m":row.get("length_m"),
                    "prediction_departure":row["prediction_departure"],
                    "classification":"PROVIDER_ROUTE_PREDICTION_NOT_TRAFFIC_COUNT",
                })
            except (RuntimeError,ValueError) as exc:
                # The TomTom helper suppresses credential-bearing raw response bodies.
                notes.append(f"TomTom {period['id']} {direction}: {str(exc)[:175]}")
    # A severe two-way route-length asymmetry means the provider probably
    # snapped a waypoint to a different one-way road or detoured.
    # Preserve the raw predictions but mark them unsuitable for calibration.
    for period in study_periods(design.scenario):
        matched=[x for x in references if x["period"]==period["id"]
                 and isinstance(x.get("length_m"),(int,float)) and x["length_m"]>0]
        if len(matched)==2:
            ratio=max(x["length_m"] for x in matched)/min(x["length_m"] for x in matched)
            if ratio>=1.75:
                for x in matched:
                    x["route_geometry_check"]="UNRELIABLE_DIRECTION_LENGTH_ASYMMETRY"
                notes.append(f"TomTom {period['id']}: opposite-direction route lengths differ "
                             f"by {ratio:.2f}x; these ETAs are not suitable for SUMO calibration.")
            else:
                for x in matched:
                    x["route_geometry_check"]="DIRECTION_LENGTHS_SIMILAR_NOT_GEO_VERIFIED"
    if not references:
        raise RuntimeError("TomTom produced no usable hourly reference. "
                           "Check Routing API permissions before the 500 SUMO runs. "+
                           (notes[0] if notes else ""))
    return references,notes


def run_auto(design, *, runner=_run_one, tomtom=None, net_path=None, progress=None):
    """Do not serialize design.api_key, even in errors, logs, or report files."""
    net=Path(net_path or network_path(design.scenario.area))
    if not net.is_file():
        raise RuntimeError("SUMO study area network is missing")
    fingerprint=hashlib.sha256(net.read_bytes()).hexdigest()
    source_fingerprints={}
    if design.scenario.traffic_source=="hourly_counts":
        from app.observations import demand_for
        for period in study_periods(design.scenario):
            source_fingerprints[period["id"]]=demand_for(
                date.fromisoformat(period["date"]),period["hour"])["csv_sha256"]
    if progress: progress(0,"Retrieving TomTom reference times")
    references,notes=tomtom if tomtom is not None else _fetch_tomtom(design,progress)
    tasks=plan(design.scenario)
    workers=allowed_workers(design.workers) if runner is _run_one else 1
    rows=[]
    if workers==1:
        for i,task in enumerate(tasks):
            rows.append(runner(design.scenario,task,net))
            if progress:
                progress(.03+.94*(i+1)/COUNT,f"Completed {i+1}/500 SUMO simulations")
    else:
        with ProcessPoolExecutor(max_workers=workers,
                mp_context=multiprocessing.get_context("spawn")) as pool:
            futures={pool.submit(_run_one,design.scenario,task,net):i
                     for i,task in enumerate(tasks)}
            for future in as_completed(futures):
                rows.append((futures[future],future.result()))
                if progress:
                    progress(.03+.94*len(rows)/COUNT,
                             f"Completed {len(rows)}/500 SUMO simulations; {workers} processes")
        rows=[row for _,row in sorted(rows)]
    assert len(rows)==COUNT
    if hashlib.sha256(net.read_bytes()).hexdigest()!=fingerprint:
        raise RuntimeError("SUMO network was replaced during the experiment")
    if source_fingerprints:
        from app.observations import demand_for
        for period in study_periods(design.scenario):
            if demand_for(date.fromisoformat(period["date"]),period["hour"])["csv_sha256"]!=source_fingerprints[period["id"]]:
                raise RuntimeError("Hourly traffic dataset changed during experiment")
    for row in rows:
        row["score"]=model_score(row["metrics"])
    selection=select_winners(rows,design.scenario)
    failures=sum(row["score"] is None for row in rows)
    cycle_tuned=sum(row["metrics"].get("timing_adjusted_lights",0) for row in rows)
    offset_only=sum(row["metrics"].get("offset_only_lights",0) for row in rows)
    drain_timeout=sum(bool(row["metrics"].get("drain_timed_out")) for row in rows)
    notes += [
        f"{drain_timeout}/500 runs reached the clearance time limit with cars in flight.",
        f"Effective controls across trials: {cycle_tuned} cycle/green adjustments, "
        f"{offset_only} preserved-plan offset-only adjustments.",
        f"{failures}/500 simulation runs had incomplete trips or no valid signal control.",
        "Best configurations are hypothetical, not real Rome municipal light timings.",
        "Only controllers with identifiable Salaria approaches are retimed; other roads may be affected.",
        "TomTom estimates apply to Salaria route, not the whole regional OD network.",
        "TomTom Route API predicts historical-typical journey times, NOT hourly vehicle volumes.",
        "TomTom estimates were NOT used to infer demand or directly optimize SUMO light settings.",
        "Three training and two holdout seeds per candidate are exploratory, not field validation.",
        "Actual light clearance, pedestrian stages, side-street queues and transit require engineering review.",
    ]
    if progress: progress(.98,"Preparing report with 500 raw results and validation scores")
    return {
        "schema_version":"rome-auto-500-1","created_at":datetime.now(timezone.utc).isoformat(),
        "evidence_level":"UNCALIBRATED_SUMO_SIMULATION",
        "area":design.scenario.area,
        "scenario":design.scenario.model_dump(mode="json"),
        "network_sha256":fingerprint,"observation_fingerprints":source_fingerprints,
        "simulations":len(rows),"workers":workers,
        "periods":study_periods(design.scenario),"policies":policies(),
        "tomtom":references,"tomtom_success":len(references),
        "tomtom_requested":20,"score_formula":SCORE_DESCRIPTION,
        "selection":selection,"runs":rows,"warnings":notes,
        "quality":{"rejected_trials":failures,
                   "drain_timeouts":drain_timeout,
                   "total_timing_adjustments":cycle_tuned,
                   "total_offset_only_adjustments":offset_only,
                   "accepted_periods":sum(x["accepted"] for x in selection["decisions"])},
    }


def _csv(rows,headers):
    output=io.StringIO()
    writer=csv.DictWriter(output,fieldnames=headers,extrasaction="ignore")
    writer.writeheader();writer.writerows(rows)
    return output.getvalue()


def report_md(study):
    lines=[
        "# Rome Mobility — 500-simulation signal study","",
        "**SIMULATED / NOT FIELD-CALIBRATED / NOT A DEPLOYABLE TIMING PLAN**","",
        f"Study area: {study['area']}",
        f"Completed SUMO runs: {study['simulations']}",
        "Design: 10 periods x 10 candidate plans x 5 matched random seeds.",
        f"Training: {study['selection']['training_seeds']}; held-out: {study['selection']['validation_seeds']}",
        f"TomTom route predictions: {study['tomtom_success']} of 20 attempted",
        f"Runs reaching drain cap: {study['quality']['drain_timeouts']}/500",
        f"Time-program retunings: {study['quality']['total_timing_adjustments']}",
        f"Original-plan offset-only changes: {study['quality']['total_offset_only_adjustments']}",
        f"Network fingerprint: {study['network_sha256']}",
        f"Objective: {study['score_formula']}",
        "",
        "## Experimental recommendations by time","",
        "| Period | Policy | Improvement on unused seeds (score) | Accepted |",
        "|---|---|---:|---|",
    ]
    for d in study["selection"]["decisions"]:
        delta="n/a" if d["holdout_delta"] is None else f"{d['holdout_delta']:+.4f}"
        lines.append(f"| {d['period']} | {d['recommended']} | {delta} | {d['accepted']} |")
    lines.extend([
        "", "Candidates must have >=85% completed trips and at least one controlled signal. "
        "A winner is chosen on 3 seeds and must outperform zero-offset baseline on "
        "the 2 unused seeds, without reducing completion by >2 percentage points. "
        "SUMO stops injecting at the requested duration, then continues to drain "
        "in-flight traffic up to the configured drain cap.",
        "", "## TomTom evidence","",
        "At most 20 contextual TomTom historical-typical route predictions are fetched, "
        "not 500. They are not observed vehicle counts, not guaranteed to match "
        "SUMO OD routes, and are not used as the optimizing target.",
        "", "## Warnings and limitations","",
    ])
    lines.extend("- "+note for note in study["warnings"])
    lines.extend([
        "", "## Data files","",
        "runs.csv: all 500 simulated trials, completion rates and scores.",
        "policy_rankings.csv: per-period train/holdout performance.",
        "recommended_schedule.csv: time-specific hypothetical policies.",
        "tomtom_reference.csv: external route-time reference classifications.",
        "study.json: reproducible source inputs, results, hashes and warnings.",
        "TomTom route-length asymmetry >=1.75x is flagged as unreliable for calibration.",
    ])
    return "\n".join(lines)+"\n"


def report_html(study):
    """Printable offline report; browser can save it as PDF."""
    esc=lambda x:escape(str(x),quote=True)
    trs="".join(
        "<tr><td>"+esc(d["period"])+"</td><td>"+esc(d["recommended"])+
        "</td><td>"+esc(d["holdout_delta"] if d["holdout_delta"] is not None else "n/a")+
        "</td><td>"+esc(d["accepted"])+"</td></tr>"
        for d in study["selection"]["decisions"])
    warnings="".join("<li>"+esc(s)+"</li>" for s in study["warnings"])
    return (
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<title>Rome Mobility | 500 Simulations</title>"
        "<style>body{font:15px system-ui;margin:30px auto;padding:0 20px;max-width:1000px;"
        "color:#26374b}h1{color:#1762c1}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #cbd7e6;padding:9px;text-align:left}th{background:#eaf1fc}"
        ".warning{color:#9b452a}@media print{body{margin:0;max-width:none}}</style>"
        "<h1>Rome Mobility — 500 automatic SUMO simulations</h1>"
        "<p class='warning'><strong>Simulation only, not a field-validated traffic-light plan.</strong></p>"
        "<p>Area: "+esc(study["area"])+" | Simulations: "+esc(study["simulations"])+
        " | TomTom references: "+esc(study["tomtom_success"])+"/20 | "
        "Out-of-sample improvements: "+esc(study["quality"]["accepted_periods"])+"/10</p>"
        "<h2>Experimental timing recommendations</h2><table><tr>"
        "<th>Period</th><th>Policy</th><th>Hold-out delta</th><th>Accepted</th>"
        "</tr>"+trs+"</table><h2>Scoring</h2><p>"+esc(study["score_formula"])+
        "</p><p>Three training seeds and two different holdout seeds per policy. "
        "TomTom historical-typical route predictions inform context but are "
        "not hourly flow measurements.</p><h2>Warnings and limitations</h2>"
        "<ul>"+warnings+"</ul></html>"
    )


def report_zip(study):
    rows=[]
    for run in study["runs"]:
        rows.append({
            "period":run["period"],"day":run["date"],"hour":run["hour"],
            "policy":run["policy"],"seed":run["seed"],"score":run["score"],
            **run["metrics"],
        })
    schedule=[
        {**decision,**{k:policy[k] for k in ("cycle_s","green_s","mode")}}
        for decision in study["selection"]["decisions"]
        for policy in study["policies"] if policy["id"]==decision["recommended"]
    ]
    buf=io.BytesIO()
    with ZipFile(buf,"w",ZIP_DEFLATED) as z:
        z.writestr("report.html",report_html(study))
        z.writestr("report.md",report_md(study))
        z.writestr("runs.csv",_csv(rows,list(rows[0])))
        z.writestr("policy_rankings.csv",_csv(study["selection"]["rankings"],
                      list(study["selection"]["rankings"][0])))
        z.writestr("recommended_schedule.csv",_csv(schedule,list(schedule[0])))
        z.writestr("tomtom_reference.csv",_csv(study["tomtom"],
            list(study["tomtom"][0]) if study["tomtom"] else
            ["period","direction","historical_typical_s","freeflow_s",
             "length_m","prediction_departure","classification"]))
        z.writestr("study.json",json.dumps(study,indent=2,ensure_ascii=False))
    return buf.getvalue()
