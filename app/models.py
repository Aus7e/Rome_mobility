
"""Input validation: all ranges are experimental, not engineering approvals."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SignalOverride(BaseModel):
    offset_s: int = Field(default=0, ge=0, le=300)
    green_s: int | None = Field(default=None, ge=10, le=180)


class SimulationRequest(BaseModel):
    day: date = Field(default_factory=date.today)
    hour: int = Field(default=8, ge=0, le=23)
    rain_mm_h: float = Field(default=0, ge=0, le=80)
    speed_kmh: float = Field(default=50, ge=15, le=100)
    demand_vph: int = Field(default=500, ge=50, le=2500, description="Hypothetical vehicles per hour per direction, before time multiplier")
    cycle_s: int = Field(default=90, ge=40, le=240)
    green_s: int = Field(default=45, ge=10, le=180)
    duration_min: int = Field(default=12, ge=3, le=40)
    side_traffic_share: float = Field(default=0.25, ge=0, le=1, description="Experimental relative side-street demand")
    seed: int = Field(default=42, ge=0, le=1_000_000)
    segment: Literal["full", "south", "north"] = "full"
    mode: Literal["manual", "wave_outbound", "wave_inbound"] = "manual"
    overrides: dict[str, SignalOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_clearance(self):
        if self.green_s + 5 > self.cycle_s:
            raise ValueError("green_s must leave at least 5 seconds for clearance and other approaches")
        return self
