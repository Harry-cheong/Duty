from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from typing import Dict

import pandas as pd
from ortools.sat.python import cp_model

from inputs import singapore_public_holiday_name
from models import ComplianceRow, ReserveScheduleResponse, ScheduleResult, ScheduleRow, SummaryRow


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


def _safe_int(value: object, default: int = 0) -> int:
    if pd.isna(value):
        return default
    return int(value)


def _safe_float(value: object, default: float = 0.0) -> float:
    if pd.isna(value):
        return default
    return float(value)


def _reset_rng(config: SchedulerConfig) -> random.Random:
    if config.use_random_seed:
        return random.Random(config.random_seed)
    return random.Random()


def _prepare_planning_table(
    availability_df: pd.DataFrame,
    points_df: pd.DataFrame,
) -> pd.DataFrame:
    planning_table = availability_df.copy()  # "RANK & NAME" is the index

    # Add AVAILABILITY_COLUMN
    planning_table[AVAILABILITY_COLUMN] = (planning_table.iloc[:, 1:] != 0).sum(axis=1)

    excluded_df = planning_table[planning_table[AVAILABILITY_COLUMN] == 0]
    planning_table = planning_table[planning_table[AVAILABILITY_COLUMN] > 0].copy()

    # Add DUTY_COLUMN, RESERVE_COLUMN, Active (Months)
    for i, row in points_df.iterrows():
        duty = 0
        reserve = 0
        active = 1  # Inclusive of the current planning month
        for col in points_df.columns:
            if "Duty" in col:
                if pd.isna(points_df.loc[i, col]):  # If no duty record for the month, assume clerk is not active
                    break
                else:
                    active += 1
                    duty += points_df.loc[i, col]

            if "R1" in col or "R2" in col:
                reserve += points_df.loc[i, col]

        planning_table.loc[i, "Active (Months)"] = active
        planning_table.loc[i, RESERVE_COLUMN] = reserve
        planning_table.loc[i, DUTY_COLUMN] = duty

    return planning_table


def _project_duties(
    duty_col: str,
    obligation_col: str,
    projected_col: str,
    planning_table: pd.DataFrame,
    duty_target: int,
    rng: random.Random,
) -> pd.DataFrame:
    planning_table[projected_col] = 0
    heap = [
        (
            -(_safe_float(row[obligation_col]) - _safe_float(row[duty_col])),
            rng.random(),
            idx,
        )
        for idx, row in planning_table.iterrows()
    ]
    heapq.heapify(heap)

    for _ in range(duty_target):
        if not heap:
            break
        _, _, name = heapq.heappop(heap)
        planning_table.loc[name, projected_col] += 1
        row = planning_table.loc[name]
        remaining_gap = (
            _safe_float(row[obligation_col])
            - _safe_float(row[duty_col])
            - _safe_float(row[projected_col])
        )
        heapq.heappush(heap, (-remaining_gap, rng.random(), name))

    return planning_table


def generate_planning_table(
    availability_df: pd.DataFrame,
    points_df: pd.DataFrame,
    config: SchedulerConfig,
    duty_obligation: float,
    reserve_obligation: float,
    num_slots: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    planning_table = _prepare_planning_table(
        availability_df=availability_df,
        points_df=points_df,
    )
    rng = _reset_rng(config)
    planning_table[OBLIGATION_DUTY_COLUMN] = planning_table["Active (Months)"] * duty_obligation
    planning_table[OBLIGATION_RESERVE_COLUMN] = planning_table["Active (Months)"] * reserve_obligation

    planning_table = _project_duties(PROJECTED_DUTY_COLUMN, OBLIGATION_DUTY_COLUMN, PROJECTED_DUTY_COLUMN, planning_table, num_slots, rng)
    planning_table = _project_duties(PROJECTED_RESERVE_COLUMN, OBLIGATION_RESERVE_COLUMN, PROJECTED_RESERVE_COLUMN, planning_table, num_slots * 2, rng)

    preview_df = planning_table[
        ["Active (Months)", DUTY_COLUMN, RESERVE_COLUMN, OBLIGATION_DUTY_COLUMN, OBLIGATION_RESERVE_COLUMN]
    ].copy()

    projected_df = planning_table[
        [PROJECTED_DUTY_COLUMN, PROJECTED_RESERVE_COLUMN]
    ]

    return planning_table, preview_df, projected_df


def _parse_slot_date(slot_label: str) -> pd.Timestamp:
    return pd.to_datetime(slot_label.split()[0], format="%d-%m-%y")


def _get_prior_assigned_dates(
    planning_table: pd.DataFrame,
    slots: list[str],
) -> dict[str, list[pd.Timestamp]]:
    """
    Return dates (as Timestamps) where each clerk is already assigned (value == 3).
    Used to enforce min_gap_days against assignments from prior rounds.
    """
    prior: dict[str, list[pd.Timestamp]] = {}
    for clerk in planning_table.index:
        assigned_dates = []
        for slot in slots:
            if slot in planning_table.columns and _safe_int(planning_table.at[clerk, slot]) == 3:
                assigned_dates.append(_parse_slot_date(slot))
        prior[clerk] = assigned_dates
    return prior


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


def _build_schedule_model(
    projected_column: str,
    planning_table: pd.DataFrame,
    slots: list[str],
    strict: bool,
    min_gap_days: int,
    prior_assigned_dates: dict[str, list[pd.Timestamp]] | None = None,
) -> tuple[
    cp_model.CpModel,
    Dict[tuple[str, str], cp_model.IntVar],
    Dict[str, cp_model.IntVar],
    Dict[str, cp_model.IntVar],
    cp_model.IntVar,
    cp_model.LinearExpr,
]:
    model = cp_model.CpModel()
    clerk_names = planning_table.index.tolist()
    prior_assigned_dates = prior_assigned_dates or {}

    projected_load = {
        idx: _safe_int(row[projected_column])
        for idx, row in planning_table.iterrows()
    }
    is_available = {
        idx: {slot: _safe_int(row[slot]) for slot in slots}
        for idx, row in planning_table.iterrows()
    }
    slot_dates = {slot: _parse_slot_date(slot) for slot in slots}
    weekend_slots = [slot for slot in slots if slot_dates[slot].weekday() >= 5]
    weekend_preference = {
        idx: {
            slot: 1 if slot in weekend_slots and _safe_int(row[slot]) == 2 else 0
            for slot in slots
        }
        for idx, row in planning_table.iterrows()
    }

    # Decision variables
    x: Dict[tuple[str, str], cp_model.IntVar] = {}
    for clerk in clerk_names:
        clerk_prior_dates = prior_assigned_dates.get(clerk, [])
        for slot in slots:
            x[clerk, slot] = model.NewBoolVar(f"assign_{clerk}_{slot}")

            # Block if unavailable (0 in availability matrix)
            if not is_available[clerk][slot]:
                model.Add(x[clerk, slot] == 0)
                continue

            # Block if this slot is too close to any prior-round assignment
            slot_date = slot_dates[slot]
            if any(
                abs((slot_date - prior_date).days) < min_gap_days
                for prior_date in clerk_prior_dates
            ):
                model.Add(x[clerk, slot] == 0)

    # Each slot must have exactly one clerk assigned
    for slot in slots:
        model.Add(sum(x[clerk, slot] for clerk in clerk_names) == 1)

    # Per-clerk duty count constraints
    total_duties: Dict[str, cp_model.IntVar] = {}
    projected_diff: Dict[str, cp_model.IntVar] = {}
    for clerk in clerk_names:
        total_duties[clerk] = model.NewIntVar(0, len(slots), f"total_{clerk}")
        model.Add(total_duties[clerk] == sum(x[clerk, slot] for slot in slots))
        if strict:
            model.Add(total_duties[clerk] == projected_load[clerk])
        else:
            projected_diff[clerk] = model.NewIntVar(0, len(slots), f"projected_diff_{clerk}")
            model.AddAbsEquality(projected_diff[clerk], total_duties[clerk] - projected_load[clerk])

    # Within-round gap constraints
    gap_violations: list[cp_model.IntVar] = []
    for clerk in clerk_names:
        clerk_prior_dates = prior_assigned_dates.get(clerk, [])
        for i, slot in enumerate(slots):
            slot_date = slot_dates[slot]

            # Skip slots already hard-blocked above
            if not is_available[clerk][slot]:
                continue
            if any(abs((slot_date - prior_date).days) < min_gap_days for prior_date in clerk_prior_dates):
                continue

            # Collect within-round conflicting slots (too close together)
            conflicting_slots = [slot]
            j = i + 1
            while j < len(slots):
                next_slot = slots[j]
                gap_days = (slot_dates[next_slot] - slot_dates[slot]).days
                if gap_days < min_gap_days:
                    conflicting_slots.append(next_slot)
                    j += 1
                else:
                    break

            if len(conflicting_slots) == 1:
                continue

            if strict:
                model.Add(sum(x[clerk, s] for s in conflicting_slots) <= 1)
            else:
                violation = model.NewIntVar(0, len(conflicting_slots) - 1, f"gap_violation_{clerk}_{i}")
                model.Add(violation >= sum(x[clerk, s] for s in conflicting_slots) - 1)
                gap_violations.append(violation)

    # Weekend balance
    weekend_count: Dict[str, cp_model.IntVar] = {}
    preferred_weekend_assignments = []
    for clerk in clerk_names:
        weekend_count[clerk] = model.NewIntVar(0, len(weekend_slots), f"weekend_{clerk}")
        model.Add(weekend_count[clerk] == sum(x[clerk, slot] for slot in weekend_slots))
        preferred_weekend_assignments.extend(
            weekend_preference[clerk][slot] * x[clerk, slot]
            for slot in weekend_slots
        )

    max_weekend = model.NewIntVar(0, len(weekend_slots), "max_weekend")
    min_weekend = model.NewIntVar(0, len(weekend_slots), "min_weekend")
    model.AddMaxEquality(max_weekend, list(weekend_count.values()))
    model.AddMinEquality(min_weekend, list(weekend_count.values()))
    weekend_imbalance = model.NewIntVar(0, len(weekend_slots), "weekend_imbalance")
    model.Add(weekend_imbalance == max_weekend - min_weekend)

    preferred_weekend_total = sum(preferred_weekend_assignments)

    if strict:
        model.Maximize(100 * preferred_weekend_total - weekend_imbalance)
    else:
        model.Minimize(
            100 * sum(projected_diff.values())
            + 50 * sum(gap_violations)
            + weekend_imbalance
            - 20 * preferred_weekend_total
        )

    return model, x, total_duties, weekend_count, weekend_imbalance, preferred_weekend_total


def _solve_schedule(
    projected_column: str,
    planning_table: pd.DataFrame,
    slots: list[str],
    min_gap_days: int,
    time_limit_seconds: int,
    prior_assigned_dates: dict[str, list[pd.Timestamp]] | None = None,
):
    for strict in (True, False):
        model, x, total_duties, weekend_count, weekend_imbalance, preferred_weekend_total = _build_schedule_model(
            projected_column,
            planning_table=planning_table,
            slots=slots,
            strict=strict,
            min_gap_days=min_gap_days,
            prior_assigned_dates=prior_assigned_dates,
        )
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_seconds
        solver.parameters.num_search_workers = 8
        status = solver.Solve(model)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return (
                "strict" if strict else "fallback",
                solver,
                x,
                total_duties,
                weekend_count,
                weekend_imbalance,
                preferred_weekend_total,
            )
    raise ValueError("No feasible schedule found, even after fallback.")


def _build_compliance_rows(
    schedule_rows: list[ScheduleRow],
    planning_table: pd.DataFrame,
) -> list[ComplianceRow]:
    report_rows: list[ComplianceRow] = []

    for row in schedule_rows:
        if row.assigned_clerk not in planning_table.index:
            report_rows.append(
                ComplianceRow(
                    date=row.date,
                    assigned_clerk=row.assigned_clerk,
                    availability_value=None,
                    compliant=False,
                    issue="Clerk missing from availability table",
                )
            )
            continue

        if row.date not in planning_table.columns:
            report_rows.append(
                ComplianceRow(
                    date=row.date,
                    assigned_clerk=row.assigned_clerk,
                    availability_value=None,
                    compliant=False,
                    issue="Slot missing from availability table",
                )
            )
            continue

        availability_value = _safe_int(planning_table.at[row.assigned_clerk, row.date])
        report_rows.append(
            ComplianceRow(
                date=row.date,
                assigned_clerk=row.assigned_clerk,
                availability_value=availability_value,
                compliant=availability_value > 0,
                issue="" if availability_value > 0 else "Assigned despite zero availability",
            )
        )

    return report_rows


def generate_schedule(
    projected_column: str,
    slots: list[str],
    planning_table: pd.DataFrame,
    config: SchedulerConfig,
) -> tuple[ScheduleResult, pd.DataFrame]:
    duty_planning_table = planning_table.copy()

    if duty_planning_table.empty:
        raise ValueError("No schedulable clerks remain after filtering availability and points.")

    # Read prior assignments (value == 3) so gap constraints respect all previous rounds
    prior_assigned_dates = _get_prior_assigned_dates(duty_planning_table, slots)

    mode, solver, x, total_duties, weekend_count, weekend_imbalance, preferred_weekend_total = _solve_schedule(
        projected_column=projected_column,
        planning_table=duty_planning_table,
        slots=slots,
        min_gap_days=config.min_gap_days,
        time_limit_seconds=config.time_limit_seconds,
        prior_assigned_dates=prior_assigned_dates,
    )

    slot_dates = {slot: _parse_slot_date(slot) for slot in slots}
    clerk_names = duty_planning_table.index.tolist()

    schedule_rows: list[ScheduleRow] = []
    for slot in slots:
        holiday_name = singapore_public_holiday_name(slot_dates[slot])
        schedule_rows.append(
            ScheduleRow(
                date=slot,
                assigned_clerk=next(clerk for clerk in clerk_names if solver.Value(x[clerk, slot]) == 1),
                weekend=slot_dates[slot].weekday() >= 5,
                public_holiday=bool(holiday_name),
                holiday=holiday_name,
            )
        )

    weekend_slots = [slot for slot in slots if slot_dates[slot].weekday() >= 5]
    weekend_preference = {
        idx: {
            slot: 1 if slot in weekend_slots and _safe_int(row[slot]) == 2 else 0
            for slot in slots
        }
        for idx, row in duty_planning_table.iterrows()
    }

    summary_rows = [
        SummaryRow(
            name=clerk,
            projected=_safe_int(duty_planning_table.loc[clerk, projected_column]),
            assigned=solver.Value(total_duties[clerk]),
            projected_delta=solver.Value(total_duties[clerk]) - _safe_int(duty_planning_table.loc[clerk, projected_column]),
            weekend_duties=solver.Value(weekend_count[clerk]),
            preferred_weekend_slots=sum(weekend_preference[clerk][slot] for slot in weekend_slots),
        )
        for clerk in clerk_names
    ]

    compliance_rows = _build_compliance_rows(schedule_rows, duty_planning_table)

    # Mark assignments on the planning table: summary column and slot cells
    for item in summary_rows:
        duty_planning_table.loc[item.name, "Duty"] = item.assigned
    for item in schedule_rows:
        duty_planning_table.loc[item.assigned_clerk, item.date] = 3

    result = ScheduleResult(
        mode=mode,
        weekend_imbalance=solver.Value(weekend_imbalance),
        preferred_weekend_assignments=solver.Value(preferred_weekend_total),
        projected_total=int(duty_planning_table[projected_column].sum()),
        assigned_total=sum(row.assigned for row in summary_rows),
        schedule=schedule_rows,
        summary=summary_rows,
        compliance=compliance_rows,
    )
    return result, duty_planning_table


def generate_schedule_from_inputs(
    planning_table: pd.DataFrame,
    config: SchedulerConfig,
    slots: list[str],
) -> tuple[ScheduleResult, pd.DataFrame]:
    return generate_schedule(
        projected_column=PROJECTED_DUTY_COLUMN,
        planning_table=planning_table,
        config=config,
        slots=slots,
    )


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