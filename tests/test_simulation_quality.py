"""End-of-injection draining and conservative signal validity."""
from datetime import date
from types import SimpleNamespace
import pytest
from app.engine import demand_factor, WEEKDAY_HOURLY_FACTORS, WEEKEND_HOURLY_FACTORS
from app.models import SimulationRequest
from app.simulation_quality import drain_deadline, comparable_completion
from app.sumo_runner import rescale_phases, _offset_phase, _signal_offset


def test_hourly_synthetic_profiles_have_distinct_periods():
    weekday=date(2026,10,9)
    weekend=date(2026,10,10)
    assert len(WEEKDAY_HOURLY_FACTORS)==24
    assert len(WEEKEND_HOURLY_FACTORS)==24
    assert demand_factor(weekday,7)!=demand_factor(weekday,8)
    assert demand_factor(weekday,17)!=demand_factor(weekday,19)
    assert demand_factor(weekend,9)!=demand_factor(weekend,12)
    assert demand_factor(weekend,12)!=demand_factor(weekend,15)
    assert all(x>0 for x in WEEKDAY_HOURLY_FACTORS + WEEKEND_HOURLY_FACTORS)
    with pytest.raises(ValueError):
        demand_factor(weekday,24)


def test_drain_horizon_separated_from_insertion():
    p=SimulationRequest(duration_min=12,drain_max_s=900)
    assert drain_deadline(p.duration_min*60,p.drain_max_s)==1620
    assert drain_deadline(180,600)==780
    assert drain_deadline(180,0)==180
    assert comparable_completion({"inserted":100,"completed":93})==.93
    assert comparable_completion({"inserted":100,"completed":101}) is None
    with pytest.raises(ValueError):
        drain_deadline(0,900)
    with pytest.raises(ValueError):
        SimulationRequest(drain_max_s=3601)


def test_feasible_signal_split_retains_amber_and_red():
    phases=[SimpleNamespace(state="GGrr",duration=40),
            SimpleNamespace(state="yyrr",duration=4),
            SimpleNamespace(state="rrGG",duration=32),
            SimpleNamespace(state="rryy",duration=4),
            SimpleNamespace(state="rrrr",duration=3)]
    result=rescale_phases(phases,0,45,90)
    assert result is not None
    assert len(result)==len(phases)
    assert abs(sum(result)-90)<1e-6
    assert result[0]==45
    assert result[1]==4 and result[3]==4 and result[4]==3
    assert result[2]>=7
    assert rescale_phases(phases,0,90,90) is None


def test_offset_modulo_retains_original_program():
    req=SimulationRequest(mode="wave_outbound",speed_kmh=50)
    sig={"id":"tls","s_m":1100}
    off=_signal_offset(req,sig,5000,90)
    assert 0<=off<90
    assert off==round(-1100/(50/3.6))%90
    idx,left=_offset_phase([41,4,35,4,6],off)
    assert 0<=idx<5
    assert 0<left<=90


def test_unknown_street_not_falsely_identified_as_salaria():
    from app.sumo_runner import _is_salaria_approach
    class StubEdge:
        def __init__(self,name):self.name=name
        def getName(self):return self.name
        def getShape(self):return []
        def getLength(self):return 120
    assert _is_salaria_approach(None,StubEdge("Via Salaria"),[]) is True
    assert _is_salaria_approach(None,StubEdge("Via Laterale"),[]) is False
