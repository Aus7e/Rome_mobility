"""SUMO-independent tests for essential timing/safety rules."""
from dataclasses import dataclass

from app.sumo_runner import _offset_phase,rescale_phases,status
from app.models import SimulationRequest

@dataclass
class Phase:
    state: str
    duration: float


def test_intergreens_preserved():
    phases=[Phase("GGrr",30),Phase("yyrr",3),Phase("rrGG",25),
            Phase("rryy",3),Phase("rrrr",2)]
    result=rescale_phases(phases,0,40,90)
    assert result is not None
    assert sum(result)==90
    assert result[0]==40
    assert result[1]==3 and result[3]==3 and result[4]==2
    assert result[2]>=7


def test_infeasible_timings_rejected():
    phases=[Phase("GGrr",30),Phase("yyrr",3),Phase("rrGG",25),Phase("rryy",3)]
    assert rescale_phases(phases,0,70,75) is None
    assert rescale_phases([Phase("GG",20),Phase("yy",4)],0,14,40) is None


def test_phase_offset_wraps():
    i,left=_offset_phase([30,3,25,3],36)
    assert i==2 and left==22
    assert _offset_phase([30,3,25,3],61)==(0,30)


def test_sumo_status_survives_missing_binary():
    result=status()
    assert isinstance(result["available"],bool)


def test_side_traffic_range():
    assert SimulationRequest(side_traffic_share=.5).side_traffic_share==.5
