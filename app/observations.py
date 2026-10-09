"""Explicit, source-traceable hourly directional counts/volume estimates.

No map-routing ETA is converted to vehicle counts. Missing observed data always
causes a clear error when measured-demand mode is selected; no silent fallback.
"""
from __future__ import annotations
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
from threading import Lock

from app.network import ROOT

DATA_FILE = ROOT / "data" / "observations" / "hourly_counts.json"
_LOCK = Lock()
DIRECTIONS = ("outbound", "inbound")
DAY_TYPES = ("weekday", "weekend")
KINDS = ("observed_count", "provider_estimate")
FIELDS = ("day_type", "hour", "direction", "vehicles_per_hour", "kind",
          "source", "observation_period", "licence")


def validate_csv(contents: bytes) -> dict:
    """Accept only explicitly labelled observations, not invented defaults."""
    if len(contents) > 300_000:
        raise ValueError("CSV too large (maximum 300 KB)")
    try:
        decoded = contents.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("CSV must use UTF-8") from exc
    if reader.fieldnames is None or not set(FIELDS).issubset(reader.fieldnames):
        raise ValueError("CSV header must contain: " + ", ".join(FIELDS))
    if len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("CSV has duplicate field names")
    rows = []
    unique = set()
    try:
        for line, source in enumerate(reader, 2):
            if len(rows) >= 96:
                raise ValueError("CSV supports a maximum of 96 hourly rows")
            if None in source:
                raise ValueError(f"Line {line}: unexpected additional CSV columns")
            day_type = source["day_type"].strip().lower()
            direction = source["direction"].strip().lower()
            kind = source["kind"].strip().lower()
            if day_type not in DAY_TYPES or direction not in DIRECTIONS or kind not in KINDS:
                raise ValueError(f"Line {line}: invalid day_type, direction or kind")
            try:
                hour = int(source["hour"])
                volume = float(source["vehicles_per_hour"])
            except (TypeError, ValueError):
                raise ValueError(f"Line {line}: invalid hour / vehicles_per_hour") from None
            if not 0 <= hour <= 23 or not math.isfinite(volume) or not 0 <= volume <= 8000:
                raise ValueError(f"Line {line}: hour must be 0–23 and vehicle count 0–8000")
            if volume != int(volume):
                raise ValueError(f"Line {line}: vehicles_per_hour must be a whole number")
            values = {}
            for field in ("source", "observation_period", "licence"):
                label = (source.get(field) or "").strip()
                if not label or len(label) > 250:
                    raise ValueError(f"Line {line}: {field} is required (up to 250 chars)")
                values[field] = label
            key = (day_type, hour, direction)
            if key in unique:
                raise ValueError(f"Line {line}: duplicate {day_type}, {hour}, {direction}")
            unique.add(key)
            rows.append({"day_type": day_type, "hour": hour, "direction": direction,
                         "vehicles_per_hour": int(volume), "kind": kind, **values})
    except csv.Error as exc:
        raise ValueError("Malformed CSV") from exc
    if not rows:
        raise ValueError("CSV has no observations")
    rows.sort(key=lambda x: (x["day_type"], x["hour"], x["direction"]))
    return {
        "schema_version": "hourly-traffic-1",
        "imported_at_utc": datetime.now(timezone.utc).isoformat(),
        "sha256_source_csv": hashlib.sha256(contents).hexdigest(),
        "observations": rows,
    }


def save_csv(contents: bytes, path=DATA_FILE) -> dict:
    bundle = validate_csv(contents)
    path = Path(path)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".json.part")
        try:
            partial.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    return describe(bundle)


def load(path=DATA_FILE) -> dict | None:
    path = Path(path)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "hourly-traffic-1":
        raise ValueError("Unsupported hourly observations schema")
    return payload


def describe(bundle=None) -> dict:
    if bundle is None:
        bundle = load()
    if bundle is None:
        return {"available": False, "rows": 0, "coverage": [], "source_classes": [],
                "note": "No field counts or licensed hourly volume estimates loaded."}
    rows = bundle["observations"]
    pairs = {(r["day_type"], r["hour"], r["direction"]) for r in rows}
    coverage = [
        {"day_type": day, "hour": hour, "complete": all((day, hour, d) in pairs for d in DIRECTIONS)}
        for day in DAY_TYPES for hour in range(24)
        if any((day, hour, d) in pairs for d in DIRECTIONS)
    ]
    return {"available": True, "rows": len(rows), "coverage": coverage,
            "source_classes": sorted({r["kind"] for r in rows}),
            "imported_at_utc": bundle["imported_at_utc"],
            "sha256_source_csv": bundle["sha256_source_csv"],
            "note": "Observed counts vs provider estimates retain distinct evidence labels."}


def demand_for(day: date, hour: int, *, path=DATA_FILE) -> dict:
    bundle = load(path)
    if bundle is None:
        raise ValueError("No hourly traffic CSV imported. Choose synthetic demand or upload measured traffic.")
    day_type = "weekend" if day.weekday() >= 5 else "weekday"
    subset = {r["direction"]: r for r in bundle["observations"]
              if r["day_type"] == day_type and r["hour"] == hour}
    if not all(direction in subset for direction in DIRECTIONS):
        missing = [name for name in DIRECTIONS if name not in subset]
        raise ValueError(
            f"No traffic observations for {day_type} at {hour:02d}:00, "
            f"missing: {', '.join(missing)}. No synthetic substitution is applied."
        )
    return {
        "outbound_vph": subset["outbound"]["vehicles_per_hour"],
        "inbound_vph": subset["inbound"]["vehicles_per_hour"],
        "day_type": day_type,
        "hour": hour,
        "evidence_types": sorted({x["kind"] for x in subset.values()}),
        "provenance": {key: {name: subset[key][name] for name in
                           ("kind", "source", "observation_period", "licence")}
                       for key in DIRECTIONS},
        "csv_sha256": bundle["sha256_source_csv"],
        "description": "Externally provided hourly direction counts; not generated from TomTom Routing ETA.",
    }
