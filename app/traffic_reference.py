"""Optional, opt-in TomTom routing reference (typical, NOT measured counts).

The API key stays server-side. We do not persist or export TomTom responses.
Travel time is for TomTom's routed candidate path at a future departure time;
it must not be equated with the mixed-OD SUMO trip mean.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from zoneinfo import ZoneInfo

import httpx

TIME_ZONE = ZoneInfo("Europe/Rome")
ENDPOINT = "https://api.tomtom.com/routing/1/calculateRoute/"


def next_departure(day: date, hour: int, *, now: datetime | None = None) -> datetime:
    if not 0 <= hour <= 23:
        raise ValueError("Hour must be between 0 and 23")
    today = (now or datetime.now(TIME_ZONE)).astimezone(TIME_ZONE)
    candidate = datetime(day.year, day.month, day.day, hour, tzinfo=TIME_ZONE)
    # For a historical reference day, ask the forecast model for its *next*
    # matching weekday (does not pretend to backfill that past date).
    if candidate <= today:
        days_ahead = (day.weekday() - today.weekday()) % 7
        candidate = today.replace(hour=hour, minute=0, second=0, microsecond=0)
        candidate += timedelta(days=days_ahead)
        if candidate <= today:
            candidate += timedelta(days=7)
    return candidate


def coordinate_chain(points: list[list[float]], direction: str) -> str:
    if direction not in {"outbound", "inbound"}:
        raise ValueError("Unknown direction")
    if len(points) < 2:
        raise ValueError("Missing geographic road axis")
    # Via points discourage the routing provider from choosing an unrelated
    # corridor; they still do NOT guarantee exact SUMO road correspondence.
    indexes = [0, len(points)//4, len(points)//2, (3*len(points))//4, len(points)-1]
    positions = [points[i] for i in indexes]
    if direction == "inbound":
        positions.reverse()
    result = []
    for lat, lon in positions:
        if not (41.0 < lat < 43.0 and 11.0 < lon < 14.0):
            raise ValueError("Road axis is not positioned near Rome")
        result.append(f"{lat:.6f},{lon:.6f}")
    return ":".join(result)


def get_typical_traffic(day: date, hour: int, direction: str, *,
                        network: dict, client: httpx.Client | None = None,
                        now: datetime | None = None,
                        api_key: str | None = None) -> dict:
    api_key = (api_key if api_key is not None else os.environ.get("TOMTOM_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError(
            "TomTom API key missing. Use the in-app TomTom connection dialog or set TOMTOM_API_KEY in .env."
        )
    if len(api_key)>180 or any(c in api_key for c in "\r\n\t "):
        raise ValueError("Invalid TomTom API key format")
    if not network.get("geo_valid") or network.get("source") != "sumo_osm":
        raise ValueError("SUMO georeferenced OSM network is required for external route references")
    selected = next_departure(day, hour, now=now)
    coords = coordinate_chain(network["points"], direction)
    params = {
        "key": api_key,
        "traffic": "false",
        "routeType": "fastest",
        "travelMode": "car",
        "departAt": selected.isoformat(),
        "computeTravelTimeFor": "all",
        "routeRepresentation": "none",
    }
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=20, follow_redirects=False)
    try:
        try:
            response = client.get(ENDPOINT + coords + "/json", params=params)
        except httpx.HTTPError as exc:
            # Avoid leaking the key through exception strings containing URLs.
            raise RuntimeError("TomTom connection failed; verify your network and provider access") from None
        if response.status_code != 200:
            raise RuntimeError(f"TomTom returned HTTP {response.status_code}; verify API key, account and route")
        try:
            summary = response.json()["routes"][0]["summary"]
            typical = summary.get("historicTrafficTravelTimeInSeconds")
            freeflow = summary.get("noTrafficTravelTimeInSeconds")
            general = summary.get("travelTimeInSeconds")
            length = summary.get("lengthInMeters")
        except (KeyError, IndexError, ValueError, TypeError):
            raise RuntimeError("TomTom response did not contain expected route statistics") from None
        if not isinstance(typical, (int, float)) or typical <= 0:
            raise RuntimeError("TomTom did not return a historical typical travel-time estimate")
        return {
            "provider": "TomTom Routing v1",
            "evidence_type": "PROVIDER_PREDICTION_NOT_OBSERVED_MEASUREMENT",
            "direction": direction,
            "requested_day": day.isoformat(),
            "requested_hour": hour,
            "prediction_departure": selected.isoformat(),
            "travel_time_typical_s": typical,
            "travel_time_freeflow_s": freeflow,
            "travel_time_estimated_s": general,
            "length_m": length,
            "route_note": "Modelled historical typical traffic for TomTom's candidate routed path, "
                          "not observed historical counts or the mixed-OD SUMO average",
            "licence_note": "External provider terms apply; not persisted or included in simulation exports.",
        }
    finally:
        if owns_client:
            client.close()
