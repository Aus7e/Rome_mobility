"""Reproducible paired-seed traffic simulation: uncalibrated, research-only."""
from __future__ import annotations
import csv
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import os
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import random
import statistics
from zipfile import ZipFile, ZIP_DEFLATED

from pydantic import BaseModel, Field, field_validator
from app.models import SimulationRequest
from app.sumo_runner import NET_FILE, run_sumo

METRICS = {
    "avg_delay_s": "s, completed trips",
    "avg_travel_s": "s, completed trips",
    "p95_travel_s": "s, completed trips",
    "max_queued_vehicles": "vehicles, entire network",
    "mean_stops_per_trip": "stops, completed trips",
    "completion_rate": "fraction of inserted trips",
}


class ResearchRequest(BaseModel):
    scenario: SimulationRequest
    seeds: list[int] = Field(default_factory=lambda: [42, 43, 44], min_length=2, max_length=200)
    workers: int = Field(default=2, ge=1, le=8,
        description="Concurrent independent SUMO worker processes; capped by server config")

    @field_validator("seeds")
    @classmethod
    def validate_seeds(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Each seed must be unique")
        if any(not 0 <= x <= 1_000_000 for x in values):
            raise ValueError("Seeds must be within 0..1,000,000")
        return values


def values_of(metrics):
    inserted = int(metrics.get("inserted") or 0)
    completed = int(metrics.get("completed") or 0)
    result = {k: metrics.get(k) for k in METRICS if k != "completion_rate"}
    result.update({
        "completion_rate": round(completed / inserted, 6) if inserted else None,
        "inserted": inserted, "completed": completed,
        "still_on_road": int(metrics.get("still_on_road") or 0),
        "upstream_waiting": int(metrics.get("upstream_waiting") or 0),
        "controlled_lights": int(metrics.get("controlled_lights") or 0),
    })
    return result


def _quantile(numbers, proportion):
    values = sorted(numbers)
    point = (len(values) - 1) * proportion
    left = int(point)
    if left == len(values) - 1:
        return values[-1]
    return values[left] + (point - left) * (values[left + 1] - values[left])


def summarize(pairs):
    summary = {}
    rng = random.Random(20261009)
    for metric, unit in METRICS.items():
        diffs = [p["experiment"][metric] - p["baseline"][metric] for p in pairs
                 if p["baseline"][metric] is not None and p["experiment"][metric] is not None]
        if not diffs:
            summary[metric] = {"unit": unit, "n": 0, "mean_delta": None, "interval95": None}
            continue
        bootstrap_means = [statistics.mean(rng.choice(diffs) for _ in diffs)
                           for _ in range(1000)]
        summary[metric] = {
            "unit": unit, "n": len(diffs),
            "mean_delta": round(statistics.mean(diffs), 4),
            "interval95": [round(_quantile(bootstrap_means, .025), 4),
                           round(_quantile(bootstrap_means, .975), 4)],
        }
    return summary


def allowed_workers(requested: int) -> int:
    """Bound concurrency to avoid exhausting RAM on consumer laptops."""
    try:
        cap = int(os.environ.get("ROME_RESEARCH_MAX_WORKERS", "4"))
    except ValueError:
        cap = 4
    return max(1, min(requested, max(1, min(8, cap)), max(1, os.cpu_count() or 1)))


def _pair_for_seed(scenario: SimulationRequest, seed: int, simulator=run_sumo, net_path=NET_FILE) -> dict:
    """Run matched policies sequentially within one isolated worker process."""
    baseline = scenario.model_copy(update={"mode": "manual", "overrides": {}, "seed": seed})
    experiment = scenario.model_copy(update={"seed": seed})
    # The network argument must propagate to every process for isolated tests
    # and for reproducible non-default research networks.
    if simulator is run_sumo:
        before = simulator(baseline, frames=False, net_path=net_path)
        after = simulator(experiment, frames=False, net_path=net_path)
    else:
        before = simulator(baseline, frames=False)
        after = simulator(experiment, frames=False)
    return {
        "seed": seed, "baseline": values_of(before["metrics"]),
        "experiment": values_of(after["metrics"]),
        "baseline_warnings": before.get("warnings", []),
        "experiment_warnings": after.get("warnings", []),
    }


def run_study(design: ResearchRequest, *, simulate=run_sumo, net_path=NET_FILE, progress=None):
    """A bounded process pool isolates SUMO TraCI clients and random streams.

    A pair runs in one process; multiple pairs run concurrently, each with
    its own TraCI connection, SUMO binary and process-local lock.
    Custom fake simulators run sequentially for deterministic unit tests.
    """
    pairs, warnings = [], []
    start_dataset_hash = None
    if design.scenario.traffic_source == "hourly_counts":
        from app.observations import demand_for
        start_dataset_hash = demand_for(design.scenario.day, design.scenario.hour)["csv_sha256"]
    count = len(design.seeds)
    workers = allowed_workers(design.workers) if simulate is run_sumo else 1
    if workers == 1:
        for index, seed in enumerate(design.seeds):
            if progress:
                progress(index / count, f"Pair {index + 1}/{count}: SUMO seed {seed}")
            pairs.append(_pair_for_seed(design.scenario, seed, simulate, net_path))
            if progress:
                progress((index+1) / count, f"Finished {index + 1}/{count} pairs")
    else:
        # Spawn instead of fork: this API process already has live threads.
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
        ) as pool:
            futures = {
                pool.submit(_pair_for_seed, design.scenario, seed, run_sumo, net_path): seed
                for seed in design.seeds
            }
            for future in as_completed(futures):
                pair = future.result()
                pairs.append(pair)
                if progress:
                    progress(len(pairs) / count,
                             f"Completed {len(pairs)}/{count} paired seeds · {workers} workers")
        pairs.sort(key=lambda pair: design.seeds.index(pair["seed"]))
    for pair in pairs:
        seed = pair["seed"]
        b, e = pair["baseline"], pair["experiment"]
        for label, outcome in (("baseline", b), ("experiment", e)):
            if outcome["completion_rate"] is None or outcome["completion_rate"] < .85:
                warnings.append(f"Seed {seed}, {label}: {outcome['completed']}/"
                                f"{outcome['inserted']} completed; trip averages may be biased")
            if outcome["controlled_lights"] == 0:
                warnings.append(f"Seed {seed}, {label}: no signals could be retimed")
        if b["inserted"] != e["inserted"]:
            warnings.append(f"Seed {seed}: inserted vehicle counts differ between policies")
    if start_dataset_hash is not None:
        from app.observations import demand_for
        if demand_for(design.scenario.day, design.scenario.hour)["csv_sha256"] != start_dataset_hash:
            raise RuntimeError("Observation dataset changed while the study was running")
    if design.scenario.mode == "manual" and not design.scenario.overrides:
        warnings.append("No manual offsets supplied; experiment may equal baseline")
    net = Path(net_path)
    fingerprint = hashlib.sha256(net.read_bytes()).hexdigest() if net.is_file() else None
    return {
        "schema_version": "research-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_level": "SIMULATED_UNCALIBRATED",
        "network_sha256": fingerprint,
        "scenario": design.scenario.model_dump(mode="json"),
        "seeds": design.seeds,
        "parallel_workers": workers,
        "observation_dataset_sha256": start_dataset_hash,
        "baseline_definition": "manual, all offsets zero; NOT the observed Rome signal plan",
        "pairs": pairs, "summary": summarize(pairs),
        "warnings": list(dict.fromkeys(warnings)),
        "limitations": [
            "OSM network geometry and netconvert signal controllers are not field verified.",
            "OD routes, day/hour demand factors and traffic volumes are hypothetical.",
            "This compares two simulated policies, not actual Rome traffic operations.",
            "Bootstrap intervals describe only selected simulation seeds, not field uncertainty.",
            "Travel time and stops are calculated only for completed trips.",
            "Maximum queued vehicles means all stopped vehicles, not a junction queue length.",
        ],
    }


def _write_csv(rows, columns):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def make_markdown(result):
    spec = result["scenario"]
    lines = [
        "# Rome Mobility - Repeated-seed experimental report", "",
        "**SIMULATED / UNCALIBRATED — no measured traffic benefit is claimed.**", "",
        "## Research setup", "",
        f"- Export time (UTC): {result['created_at_utc']}",
        f"- SUMO network SHA256: {result['network_sha256'] or 'unknown'}",
        f"- Seeds: {', '.join(map(str, result['seeds']))}",
        f"- SUMO worker processes: {result['parallel_workers']}",
        f"- Policy under test: {spec['mode']}; corridor: {spec['segment']}",
        f"- Synthetic demand: {spec['demand_vph']} vehicles/h/direction (before hourly factor)",
        f"- Date/hour: {spec['day']}, {spec['hour']:02d}:00",
        f"- Baseline: {result['baseline_definition']}", "",
        "## Paired simulation differences (experiment minus baseline)", "",
        "Negative values of delay/travel time suggest lower simulated congestion,",
        "but only under the specified hypothetical inputs and cutoff.", "",
        "| KPI | Mean delta | Exploratory 95% bootstrap interval | Runs |",
        "|---|---:|---:|---:|",
    ]
    for name, row in result["summary"].items():
        average = "n/a" if row["mean_delta"] is None else f"{row['mean_delta']:+.3f}"
        interval = "n/a" if row["interval95"] is None else str(row["interval95"])
        lines.append(f"| {name} ({row['unit']}) | {average} | {interval} | {row['n']} |")
    lines.extend(["", "## Data quality checks", ""])
    lines.extend("- " + v for v in (result["warnings"] or ["No run-specific warning"]))
    lines.extend(["", "## Limitations", ""])
    lines.extend("- " + v for v in result["limitations"])
    lines.extend(["", "## Included files", "",
                  "runs.csv: each run and denominator; paired_differences.csv: seed-level contrasts.",
                  "experiment.json: all scenario inputs, warnings, aggregate results and network hash.",
                  "",
                  "Acquire verified vehicle counts, traffic-light programs, and independent travel",
                  "times before inferring or proposing operational changes to actual Rome streets.", ""])
    return "\n".join(lines)


def make_zip(result):
    metric_names = list(METRICS)
    fields = metric_names + ["inserted", "completed", "still_on_road",
                             "upstream_waiting", "controlled_lights"]
    runs, contrasts = [], []
    for pair in result["pairs"]:
        for label in ("baseline", "experiment"):
            runs.append({"seed": pair["seed"], "policy": label, **pair[label]})
        contrasts.append({
            "seed": pair["seed"],
            **{m+"_delta": round(pair["experiment"][m]-pair["baseline"][m], 6)
               if pair["experiment"][m] is not None and pair["baseline"][m] is not None
               else None for m in metric_names},
        })
    buf = io.BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as output:
        output.writestr("report.md", make_markdown(result))
        output.writestr("experiment.json", json.dumps(result, indent=2, ensure_ascii=False))
        output.writestr("runs.csv", _write_csv(runs, ["seed", "policy", *fields]))
        output.writestr("paired_differences.csv", _write_csv(
            contrasts, ["seed", *(m+"_delta" for m in metric_names)]))
    return buf.getvalue()
