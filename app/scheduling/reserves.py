from __future__ import annotations

import pandas as pd

from models import ReserveScheduleResponse, ScheduleResult
from scheduling.config import PROJECTED_RESERVE_COLUMN, SchedulerConfig
from scheduling.solver import generate_schedule


def _get_slot_assignment_counts(
    planning_table: pd.DataFrame,
    slots: list[str],
) -> dict[str, int]:
    """
    Count how many clerks are already assigned (value == 3) per slot.
    Each slot can hold up to 3 assignments: 1 duty + 1 R1 + 1 R2.
    """
    return {
        slot: int((planning_table[slot] == 3).sum())
        for slot in slots
        if slot in planning_table.columns
    }


def _zero_out_assigned_clerks(
    planning_table: pd.DataFrame,
    slots: list[str],
) -> pd.DataFrame:
    """
    For each slot, zero out clerks already assigned (value == 3) so the model
    cannot re-assign the same clerk to the same slot in a subsequent round.
    """
    table = planning_table.copy()
    for slot in slots:
        if slot in table.columns:
            table.loc[table[slot] == 3, slot] = 0
    return table


def generate_reserve_schedules_from_inputs(
    planning_table: pd.DataFrame,
    config: SchedulerConfig,
    slots: list[str],
    reserve_rounds: int = 2,  # 2 = R1 + R2
) -> ReserveScheduleResponse:
    """
    Generate reserve schedules in sequential rounds (R1, then R2).

    Each slot supports up to 3 occupants: 1 duty + 1 R1 + 1 R2.
    A clerk already assigned to a slot (value == 3) is excluded from that slot
    in subsequent rounds. The min_gap_days constraint is enforced against ALL
    prior assignments — duty and reserve — across all rounds.

    Args:
        planning_table: The planning table after generate_schedule_from_inputs
                        has been called (duty assignments already marked as 3).
        config:         Scheduler configuration.
        slots:          Full list of slot labels for the month.
        reserve_rounds: Number of reserve rounds to run (default 2 for R1 + R2).
    """
    current_planning_table = planning_table.copy()
    reserve_results: list[ScheduleResult] = []

    for round_index in range(reserve_rounds):
        slot_counts = _get_slot_assignment_counts(current_planning_table, slots)

        # Round 0 (R1) needs slots with exactly 1 prior assignment (the duty clerk).
        # Round 1 (R2) needs slots with exactly 2 prior assignments (duty + R1).
        required_prior = round_index + 1
        open_slots = [
            slot for slot in slots
            if slot_counts.get(slot, 0) == required_prior
        ]

        if not open_slots:
            break

        # Zero out already-assigned clerks per slot so the model won't re-pick them
        round_table = _zero_out_assigned_clerks(current_planning_table, open_slots)

        reserve_result, round_table = generate_schedule(
            projected_column=PROJECTED_RESERVE_COLUMN,
            planning_table=round_table,
            config=config,
            slots=open_slots,
        )
        reserve_results.append(reserve_result)

        # Propagate new assignments (3s) back onto current_planning_table so that:
        # (a) the next reserve round sees the correct slot counts
        # (b) _get_prior_assigned_dates picks them up for gap enforcement
        for item in reserve_result.schedule:
            current_planning_table.loc[item.assigned_clerk, item.date] = 3

    return ReserveScheduleResponse(reserves=reserve_results)
