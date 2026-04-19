from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass
class ScheduleRow:
    date: str
    assigned_clerk: str
    weekend: bool
    public_holiday: bool
    holiday: str


@dataclass
class SummaryRow:
    name: str
    projected: int
    assigned: int
    projected_delta: int
    weekend_duties: int
    preferred_weekend_slots: int


@dataclass
class ComplianceRow:
    date: str
    assigned_clerk: str
    availability_value: int | None
    compliant: bool
    issue: str


@dataclass
class ScheduleResult:
    mode: Literal["strict", "fallback"]
    weekend_imbalance: int
    preferred_weekend_assignments: int
    projected_total: int
    assigned_total: int
    excluded_clerks: list[str]
    unmatched_clerks: list[str]
    schedule: list[ScheduleRow]
    summary: list[SummaryRow]
    compliance: list[ComplianceRow]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReserveScheduleResponse:
    primary: ScheduleResult
    reserves: list[ScheduleResult]
