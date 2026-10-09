"""Regression tests for HTTP 406, alternate providers, and safe OSM caching."""
import sys

import httpx
import pytest

import scripts.bootstrap_sumo as bootstrap

VALID_OSM = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<osm version="0.6" generator="test">'
    b'<node id="1" lat="41.95" lon="12.50"/>'
    b'<node id="2" lat="41.96" lon="12.51"/>'
    b'<way id="3"><nd ref="1"/><nd ref="2"/>'
    b'<tag k="highway" v="primary"/><tag k="name" v="Via Salaria"/>'
    b'</way></osm>'
)


def test_retries_other_provider_on_406_with_descriptive_user_agent():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["user-agent"].startswith("RomeMobility")
        assert "Rome_mobility" in request.headers["referer"]
        if len(requests) == 1:
            return httpx.Response(406, text="Not Acceptable")
        return httpx.Response(200, content=VALID_OSM)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payload, provider = bootstrap.download_osm(
            client, ("https://primary.example/api", "https://backup.example/api")
        )
    assert payload == VALID_OSM
    assert provider == "https://backup.example/api"
    assert len(requests) == 2
    assert b"data=" in requests[-1].content and b"highway" in requests[-1].content


def test_rejects_html_and_xml_timeout_remarks():
    with pytest.raises(ValueError, match="expected <osm>"):
        bootstrap.validate_osm(b"<html><body>Error</body></html>")
    with pytest.raises(ValueError, match="Overpass reported"):
        bootstrap.validate_osm(
            b'<osm version="0.6"><remark>runtime error: Query timed out</remark></osm>'
        )
    with pytest.raises(ValueError, match="coordinates"):
        bootstrap.validate_osm(b'<osm><way id="1"/></osm>')


def test_reports_failed_endpoints():
    def handler(request):
        return httpx.Response(406, text="denied")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="HTTP 406") as error:
            bootstrap.download_osm(client, ("https://one.example/", "https://two.example/"))
    assert "one.example" in str(error.value) and "two.example" in str(error.value)


def test_invalid_old_cache_is_replaced_after_download(tmp_path, monkeypatch):
    raw = tmp_path / "raw" / "salaria.osm.xml"
    net = tmp_path / "sumo" / "salaria.net.xml"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"bad xml")
    monkeypatch.setattr(bootstrap, "OSM_FILE", raw)
    monkeypatch.setattr(bootstrap, "NET_FILE", net)
    monkeypatch.setattr(sys, "argv", ["bootstrap_sumo.py", "--download-only"])
    monkeypatch.setattr(bootstrap, "download_osm", lambda client: (VALID_OSM, "mock"))
    bootstrap.main()
    assert raw.read_bytes() == VALID_OSM
    assert not (raw.parent / "salaria.osm.xml.part").exists()


def test_existing_good_cache_needs_no_download(tmp_path, monkeypatch):
    raw = tmp_path / "salaria.osm.xml"
    raw.write_bytes(VALID_OSM)
    monkeypatch.setattr(bootstrap, "OSM_FILE", raw)
    monkeypatch.setattr(bootstrap, "NET_FILE", tmp_path / "salaria.net.xml")
    monkeypatch.setattr(sys, "argv", ["bootstrap_sumo.py", "--download-only"])
    monkeypatch.setattr(
        bootstrap, "download_osm",
        lambda client: pytest.fail("Should not call Overpass for a valid cached file")
    )
    bootstrap.main()



def test_api_explains_missing_sumo_network(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import app
    from app import sumo_runner

    monkeypatch.setattr(sumo_runner, "status", lambda: {
        "available": False, "installed": True,
        "python_modules": True, "network_ready": False
    })
    response = TestClient(app).post("/api/sumo/jobs", json={"duration_min": 3})
    assert response.status_code == 503
    assert "rete OSM" in response.json()["detail"]


def test_sumo_environment_uses_locally_installed_type_maps(tmp_path, monkeypatch):
    root = tmp_path / "sumo-installed"
    typemap = root / "data" / "typemap" / "osmNetconvert.typ.xml"
    typemap.parent.mkdir(parents=True)
    typemap.write_text("<types/>")
    monkeypatch.setenv("SUMO_HOME", str(root))
    env = bootstrap.sumo_environment()
    assert env["SUMO_HOME"] == str(root)


def test_sumo_environment_fails_with_clear_installation_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("SUMO_HOME", str(tmp_path / "not-installed"))
    with pytest.raises(RuntimeError, match="Missing SUMO OSM type map") as error:
        bootstrap.sumo_environment()
    assert "sumo-tools" in str(error.value)
