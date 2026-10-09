"""Provider request semantics and safe, optional TomTom traffic reference."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.traffic_reference import (
    coordinate_chain, get_typical_traffic, next_departure,
)

ROME = ZoneInfo("Europe/Rome")
OSM_INFO = {
    "source": "sumo_osm", "geo_valid": True,
    "points": [
        [41.9414, 12.5088], [41.952, 12.5071],
        [41.964, 12.5063], [41.985, 12.5088],
        [41.999, 12.5130],
    ],
}


def test_missing_key_explains_config_without_network(monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TOMTOM_API_KEY"):
        get_typical_traffic(date(2026, 10, 10), 8, "outbound", network=OSM_INFO)


def test_provider_data_never_claims_field_observation(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "TEST_SECRET_DO_NOT_LEAK")
    got = []

    def handler(request):
        got.append(request)
        assert request.url.params["key"] == "TEST_SECRET_DO_NOT_LEAK"
        assert request.url.params["traffic"] == "false"
        assert request.url.params["computeTravelTimeFor"] == "all"
        assert request.url.params["departAt"].endswith("+02:00")
        return httpx.Response(200, json={
            "routes": [{
                "summary": {
                    "historicTrafficTravelTimeInSeconds": 1150,
                    "noTrafficTravelTimeInSeconds": 650,
                    "travelTimeInSeconds": 1140,
                    "lengthInMeters": 8100,
                }
            }]
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = get_typical_traffic(
            date(2026, 10, 14), 8, "outbound", network=OSM_INFO,
            client=client, now=datetime(2026, 10, 10, 9, tzinfo=ROME),
        )
    assert result["travel_time_typical_s"] == 1150
    assert result["travel_time_freeflow_s"] == 650
    assert result["direction"] == "outbound"
    assert "NOT_OBSERVED" in result["evidence_type"]
    assert "TEST_SECRET_DO_NOT_LEAK" not in str(result)
    assert len(got) == 1


def test_older_date_means_next_matching_weekday_not_false_historical_backfill():
    departure = next_departure(date(2020, 1, 1), 8,
                               now=datetime(2026, 10, 10, 23, tzinfo=ROME))
    assert departure > datetime(2026, 10, 10, 23, tzinfo=ROME)
    assert departure.weekday() == date(2020, 1, 1).weekday()
    assert departure.hour == 8


def test_inbound_route_reverses_waypoint_order():
    outward = coordinate_chain(OSM_INFO["points"], "outbound").split(":")
    inbound = coordinate_chain(OSM_INFO["points"], "inbound").split(":")
    assert inbound == list(reversed(outward))


def test_rejects_bad_network_and_bad_provider_payload(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "TEST_SECRET")
    with pytest.raises(ValueError, match="georeferenced"):
        get_typical_traffic(date(2026, 10, 14), 8, "inbound",
                            network={"geo_valid": False})
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"routes": []})
    )) as client:
        with pytest.raises(RuntimeError, match="expected"):
            get_typical_traffic(date(2026, 10, 14), 8, "outbound",
                                network=OSM_INFO, client=client)


def test_http_errors_do_not_leak_api_key(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "SECRET_456")
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, text="Unauthorized")
    )) as client:
        with pytest.raises(RuntimeError, match="HTTP 401") as error:
            get_typical_traffic(date(2026, 10, 14), 8, "outbound",
                                network=OSM_INFO, client=client)
    assert "SECRET_456" not in str(error.value)


def test_optional_api_requires_sumo_ready(monkeypatch):
    from app import sumo_runner
    monkeypatch.setattr(sumo_runner, "status", lambda: {"available": False})
    response = TestClient(app).get("/api/traffic/typical", params={
        "day": "2026-10-14", "hour": 8, "direction": "outbound"
    })
    assert response.status_code == 503
