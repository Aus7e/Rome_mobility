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
        # TomTom v1 rejects "none" unless computeBestOrder=true.
        # Keep fixed Salaria waypoint ordering and request only summary data.
        "routeRepresentation": "summaryOnly",
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
            messages = {
                401: "TomTom HTTP 401: chiave non valida o non riconosciuta. "
                     "Apri «TomTom API», usa «Verifica chiave» e controlla la key su MyTomTom.",
                403: "TomTom HTTP 403: Routing API non autorizzata per questa chiave "
                     "oppure restrizione di dominio. Controlla i prodotti abilitati su MyTomTom.",
                429: "TomTom HTTP 429: quota o limite di richieste superato.",
            }
            raise RuntimeError(messages.get(
                response.status_code,
                f"TomTom HTTP {response.status_code}: il percorso non è stato calcolato. "
                "Verifica impostazioni della richiesta e disponibilità del servizio."))
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


def check_routing_key(api_key: str, *, client: httpx.Client | None = None) -> dict:
    """Diagnose authorization for TomTom Routing without using SUMO.

    Makes one *on-demand* lightweight route request. Never returns or stores
    the key or untrusted TomTom response text.
    """
    api_key = api_key.strip()
    if not api_key or len(api_key) > 180 or any(c.isspace() for c in api_key):
        raise ValueError("Inserisci una chiave TomTom senza spazi, valida e non vuota.")
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=15, follow_redirects=False)
    try:
        try:
            response = client.get(
                ENDPOINT + "41.934900,12.507700:41.943900,12.507400/json",
                params={"key": api_key, "travelMode": "car",
                        "routeType": "fastest", "routeRepresentation": "none"},
            )
        except httpx.HTTPError:
            return {"authorized": False, "http_status": None,
                    "message": "Connessione a TomTom non riuscita. Verifica la rete e riprova."}
        status_code = response.status_code
        messages = {
            401: "HTTP 401: chiave non riconosciuta o non autorizzata. Controlla che sia "
                 "una API key attiva di MyTomTom, copiata per intero.",
            403: "HTTP 403: la chiave non dispone dei permessi richiesti. "
                 "Abilita Routing API nelle restrizioni per prodotto e verifica il dominio.",
            429: "HTTP 429: limite richieste TomTom raggiunto. Controlla quota e piano.",
        }
        if status_code == 200:
            return {"authorized": True, "http_status": 200,
                    "message": "Routing API autorizzata: la chiave funziona. "
                               "Puoi richiedere una stima per la Salaria."}
        return {
            "authorized": False,
            "http_status": status_code,
            "message": messages.get(
                status_code,
                f"TomTom ha risposto HTTP {status_code}. "
                "La chiave non è stata confermata; controlla account, autorizzazioni e servizio."),
        }
    finally:
        if owns_client:
            client.close()
