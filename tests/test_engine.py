
from datetime import date

from app.engine import aspect, demand_factor, simulate, timing_for
from app.models import SignalOverride, SimulationRequest
from app.network import demo_network, extract_osm, lengths


def scenario(**kwargs):
    return SimulationRequest(day=date(2026,10,9), duration_min=3, **kwargs)


def test_demo_has_positive_length_and_ordered_signals():
    net=demo_network()
    assert net["length_m"] > 4000
    assert all(a["s_m"] < b["s_m"] for a,b in zip(net["signals"],net["signals"][1:]))


def test_peak_demand_exceeds_weekend_and_night():
    assert demand_factor(date(2026,10,9),8) > demand_factor(date(2026,10,10),8)
    assert demand_factor(date(2026,10,9),8) > demand_factor(date(2026,10,9),3)


def test_timing_offsets_and_phases():
    network=demo_network()
    a=timing_for(network["signals"][0],scenario(),network["length_m"])
    assert aspect(a,0)=="green"
    assert aspect(a,46)=="amber"
    assert aspect(a,60)=="red"
    o=SignalOverride(offset_s=70,green_s=20)
    b=timing_for(network["signals"][0],scenario(overrides={a["id"]:o}),network["length_m"])
    assert b["green_s"]==20 and b["offset_s"]==70


def test_reproducible_and_rain_reduces_freeflow_speed():
    n=demo_network()
    dry=simulate(n,scenario(rain_mm_h=0))
    wet=simulate(n,scenario(rain_mm_h=8))
    assert dry==simulate(n,scenario(rain_mm_h=0))
    assert wet["metrics"]["effective_speed_kmh"] < dry["metrics"]["effective_speed_kmh"]
    assert dry["metrics"]["inserted"] > 0


def test_wave_produces_different_offsets():
    n=demo_network()
    result=simulate(n,scenario(mode="wave_outbound"),include_frames=False)
    assert len({s["offset_s"] for s in result["signals"]}) > 1


def test_bad_osm_no_silent_fabrication():
    try:
        extract_osm({"elements":[]})
        assert False, "Expected failure"
    except ValueError:
        pass
