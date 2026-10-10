"""Simulation horizon and censoring quality helpers.

Vehicle generation is stopped at its requested window; continuing the same
simulation avoids falsely counting newly departed trips as congestion.
"""


def drain_deadline(injection_seconds: int, drain_max_s: int) -> int:
    if injection_seconds <= 0:
        raise ValueError("Vehicle injection duration must be positive")
    if not 0 <= drain_max_s <= 3600:
        raise ValueError("Drain must be within 0..3600 seconds")
    return injection_seconds + drain_max_s


def comparable_completion(metrics: dict) -> float | None:
    injected = int(metrics.get("inserted") or 0)
    completed = int(metrics.get("completed") or 0)
    if injected <= 0 or completed < 0 or completed > injected:
        return None
    return completed / injected
