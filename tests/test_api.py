from fastapi.testclient import TestClient
from app.api import app

client = TestClient(app)


def test_health_and_index():
    assert client.get("/health").json()["ok"] is True
    html = client.get("/")
    assert html.status_code == 200
    assert "Salaria" in html.text


def test_network_and_simulation():
    network = client.get("/api/network")
    assert network.status_code == 200
    result = client.post("/api/simulate",json={"day":"2026-10-09","hour":8,
                           "duration_min":3,"seed":7})
    assert result.status_code == 200
    data = result.json()
    assert "frames" in data
    assert "metrics" in data


def test_invalid_green_rejected():
    resp=client.post("/api/simulate",json={"cycle_s":40,"green_s":38})
    assert resp.status_code == 422


def test_compare_preserves_shape():
    r=client.post("/api/compare",json={"duration_min":3,"mode":"wave_outbound"})
    assert r.status_code==200
    assert "baseline" in r.json() and "experiment" in r.json()


def test_segment_selector():
    all_=client.get("/api/network?segment=full").json()
    half=client.get("/api/network?segment=south").json()
    assert half["length_m"] < all_["length_m"]
    assert half["signals"] != all_["signals"]
    subset=client.post("/api/simulate",json={"segment":"south","duration_min":3})
    assert subset.status_code==200
    assert subset.json()["length_m"]==half["length_m"]


def test_bad_segment_rejected():
    assert client.get("/api/network?segment=unknown").status_code == 422
