from __future__ import annotations

from dataclasses import dataclass


AVAILABILITY_COLUMN = "Availability"
DUTY_COLUMN = "H. Duty"
RESERVE_COLUMN = "H. Reserve"
OBLIGATION_DUTY_COLUMN = "O. Duty"
OBLIGATION_RESERVE_COLUMN = "O. Reserve"
PROJECTED_DUTY_COLUMN = "P. Duty"
PROJECTED_RESERVE_COLUMN = "P. Reserve"


@dataclass
class SchedulerConfig:
    min_gap_days: int = 7
    time_limit_seconds: int = 10
    random_seed: int = 42
    use_random_seed: bool = True
