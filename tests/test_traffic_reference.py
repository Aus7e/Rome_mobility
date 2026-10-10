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



def test_tomtom_key_from_screen_is_per_request_and_not_saved(monkeypatch):
    import app.traffic_reference as module
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setenv("TOMTOM_API_KEY", "")
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.url.params["key"] == "ON_SCREEN_ONLY_SECRET"
        return httpx.Response(200,json={"routes":[{"summary":{
            "historicTrafficTravelTimeInSeconds":900,
            "noTrafficTravelTimeInSeconds":610,
            "travelTimeInSeconds":890,
            "lengthInMeters":7900
        }}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=get_typical_traffic(
            date(2026,10,14),8,"outbound",network=OSM_INFO,
            client=client,api_key="ON_SCREEN_ONLY_SECRET",
            now=datetime(2026,10,10,9,tzinfo=ROME))
    assert result["travel_time_typical_s"] == 900
    assert "ON_SCREEN_ONLY_SECRET" not in str(result)
    assert len(calls)==1


def test_screen_key_post_is_accepted_with_local_sumo(monkeypatch):
    from app import sumo_runner, traffic_reference
    monkeypatch.setattr(sumo_runner,"status",lambda area="salaria":{"available":True})
    monkeypatch.setattr(sumo_runner,"load_network",lambda segment, net_path=None:OSM_INFO)
    received=[]
    def stub(day,hour,direction,*,network,api_key=None,**kwargs):
        received.append(api_key)
        return {"provider":"TomTom","evidence_type":"PROVIDER_PREDICTION_NOT_OBSERVED_MEASUREMENT"}
    monkeypatch.setattr(traffic_reference,"get_typical_traffic",stub)
    response=TestClient(app).post("/api/traffic/typical",json={
        "day":"2026-10-14","hour":8,"direction":"outbound",
        "api_key":"PER_REQUEST_TEST_ONLY"})
    assert response.status_code==200
    assert received == ["PER_REQUEST_TEST_ONLY"]
    assert "PER_REQUEST_TEST_ONLY" not in response.text



def test_routing_auth_check_is_diagnostic_and_does_not_echo_key():
    from app.traffic_reference import check_routing_key
    cases = {
        200: True,
        401: False,
        403: False,
        429: False,
        500: False,
    }
    for status, authorized in cases.items():
        seen=[]
        def handler(request):
            seen.append(request)
            assert request.url.params["key"] == "A_PRIVATE_TEST_KEY"
            assert "/routing/1/calculateRoute/" in str(request.url)
            return httpx.Response(status, text="A_PRIVATE_TEST_KEY should not be surfaced")
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result=check_routing_key("A_PRIVATE_TEST_KEY", client=client)
        assert len(seen)==1
        assert result["authorized"] is authorized
        assert result["http_status"] == status
        assert "A_PRIVATE_TEST_KEY" not in str(result)


def test_routing_auth_check_endpoint_uses_key_per_request(monkeypatch):
    import app.traffic_reference as module
    captured=[]
    def check(key):
        captured.append(key)
        return {"authorized":False,"http_status":401,"message":"Non autorizzata"}
    monkeypatch.setattr(module,"check_routing_key",check)
    response=TestClient(app).post("/api/traffic/check-key", json={"api_key":"A_SECRET_FOR_TEST"})
    assert response.status_code==200
    assert captured==["A_SECRET_FOR_TEST"]
    assert "A_SECRET_FOR_TEST" not in response.text


def test_route_401_is_human_actionable_and_does_not_leak_key(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY","A_SECRET_FOR_TEST")
    with httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(401,text="A_SECRET_FOR_TEST should not echo")
    )) as client:
        with pytest.raises(RuntimeError,match="TomTom HTTP 401") as error:
            get_typical_traffic(date(2026,10,14),8,"outbound",
                                network=OSM_INFO,client=client)
    assert "A_SECRET_FOR_TEST" not in str(error.value)
