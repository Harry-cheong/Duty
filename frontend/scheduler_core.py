from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from typing import Dict

import pandas as pd
from ortools.sat.python import cp_model

from inputs import load_points
from models import ComplianceRow, ReserveScheduleResponse, ScheduleResult, ScheduleRow, SummaryRow


AVAILABILITY_COLUMN = "Availability"
NAME_COLUMN = "Name"
DUTY_COLUMN = "Duty"
PROJECTED_COLUMN = "Projected"


@dataclass
class SchedulerConfig:
    min_gap_days: int = 7
    time_limit_seconds: int = 10
    random_seed: int = 42
    use_random_seed: bool = True


def _reset_rng(config: SchedulerConfig) -> random.Random:
    if config.use_random_seed:
        return random.Random(config.random_seed)
    return random.Random()


def _slot_columns(availability_df: pd.DataFrame) -> list[str]:
    availability_index = availability_df.columns.get_loc(AVAILABILITY_COLUMN)
    return availability_df.columns[1:availability_index].tolist()


def _prepare_planning_table(
    availability_df: pd.DataFrame,
    points_df: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str], list[str], list[str]]:
    planning_table = availability_df.copy()

    excluded_df = planning_table[planning_table[AVAILABILITY_COLUMN] == 0]
    excluded_clerks = excluded_df[NAME_COLUMN].tolist()
    planning_table = planning_table[planning_table[AVAILABILITY_COLUMN] > 0].copy()
    clerks = planning_table[NAME_COLUMN].tolist()

    point_names = set(points_df[NAME_COLUMN].tolist())
    unmatched_clerks = [clerk for clerk in clerks if clerk not in point_names]
    if unmatched_clerks:
        planning_table = planning_table[~planning_table[NAME_COLUMN].isin(unmatched_clerks)].copy()

    planning_table = planning_table.merge(
        points_df[[NAME_COLUMN, DUTY_COLUMN]],
        on=NAME_COLUMN,
        how="left",
        sort=False,
    )

    slot_columns = _slot_columns(availability_df)
    return planning_table.reset_index(drop=True), slot_columns, excluded_clerks, unmatched_clerks


def _project_duties(planning_table: pd.DataFrame, duty_target: int, rng: random.Random) -> pd.DataFrame:
    current_duty = planning_table[DUTY_COLUMN].fillna(0).astype(float).to_numpy()
    planning_table[PROJECTED_COLUMN] = [1 if duty < 4.0 else 0 for duty in current_duty]

    assigned = int(planning_table[PROJECTED_COLUMN].sum())
    remaining = max(duty_target - assigned, 0)
    heap = [
        (-(4 - (1 + duty)), rng.random(), name)
        for duty, name in zip(current_duty.tolist(), planning_table[NAME_COLUMN].tolist())
    ]
    heapq.heapify(heap)

    for _ in range(remaining):
        if not heap:
            break
        _, _, name = heapq.heappop(heap)
        planning_table.loc[planning_table[NAME_COLUMN] == name, PROJECTED_COLUMN] += 1

    planning_table["Monthly Duty Points"] = 0
    return planning_table


def _parse_slot_date(slot_label: str) -> pd.Timestamp:
    return pd.to_datetime(str(slot_label)[:10], format="%d/%m/%Y")


def _build_schedule_model(
    planning_table: pd.DataFrame,
    slots: list[str],
    strict: bool,
    min_gap_days: int,
) -> tuple[
    cp_model.CpModel,
    Dict[tuple[str, str], cp_model.IntVar],
    Dict[str, cp_model.IntVar],
    Dict[str, cp_model.IntVar],
    cp_model.IntVar,
    cp_model.LinearExpr,
]:
    model = cp_model.CpModel()
    clerk_names = planning_table[NAME_COLUMN].tolist()
    projected_load = {
        row[NAME_COLUMN]: int(row[PROJECTED_COLUMN])
        for _, row in planning_table.iterrows()
    }
    is_available = {
        row[NAME_COLUMN]: {slot: int(row[slot]) for slot in slots}
        for _, row in planning_table.iterrows()
    }
    slot_dates = {slot: _parse_slot_date(slot) for slot in slots}
    weekend_slots = [slot for slot in slots if slot_dates[slot].weekday() >= 5]
    weekend_preference = {
        row[NAME_COLUMN]: {
            slot: 1 if slot in weekend_slots and int(row[slot]) == 2 else 0
            for slot in slots
        }
        for _, row in planning_table.iterrows()
    }

    x: Dict[tuple[str, str], cp_model.IntVar] = {}
    for clerk in clerk_names:
        for slot in slots:
            x[clerk, slot] = model.NewBoolVar(f"assign_{clerk}_{slot}")
            if not is_available[clerk][slot]:
                model.Add(x[clerk, slot] == 0)

    for slot in slots:
        model.Add(sum(x[clerk, slot] for clerk in clerk_names) == 1)

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

    gap_violations: list[cp_model.IntVar] = []
    for clerk in clerk_names:
        for i, slot in enumerate(slots):
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
                model.Add(sum(x[clerk, current_slot] for current_slot in conflicting_slots) <= 1)
            else:
                violation = model.NewIntVar(0, len(conflicting_slots) - 1, f"gap_violation_{clerk}_{i}")
                model.Add(violation >= sum(x[clerk, current_slot] for current_slot in conflicting_slots) - 1)
                gap_violations.append(violation)

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
    planning_table: pd.DataFrame,
    slots: list[str],
    min_gap_days: int,
    time_limit_seconds: int,
):
    for strict in (True, False):
        model, x, total_duties, weekend_count, weekend_imbalance, preferred_weekend_total = _build_schedule_model(
            planning_table=planning_table,
            slots=slots,
            strict=strict,
            min_gap_days=min_gap_days,
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


def _build_compliance_rows(schedule_rows: list[ScheduleRow], planning_table: pd.DataFrame) -> list[ComplianceRow]:
    availability_lookup = planning_table.set_index(NAME_COLUMN)
    report_rows: list[ComplianceRow] = []

    for row in schedule_rows:
        if row.assigned_clerk not in availability_lookup.index:
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

        if row.date not in availability_lookup.columns:
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

        availability_value = int(availability_lookup.at[row.assigned_clerk, row.date])
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
    availability_df: pd.DataFrame,
    points_df: pd.DataFrame,
    config: SchedulerConfig,
    availability_override: pd.DataFrame | None = None,
) -> tuple[ScheduleResult, pd.DataFrame]:
    if availability_override is not None:
        availability_df = availability_override.copy()

    planning_table, slots, excluded_clerks, unmatched_clerks = _prepare_planning_table(
        availability_df=availability_df,
        points_df=points_df,
    )
    if planning_table.empty:
        raise ValueError("No schedulable clerks remain after filtering availability and points.")
    if not slots:
        raise ValueError("No duty slots are configured.")

    duty_target = len(slots)
    rng = _reset_rng(config)
    planning_table = _project_duties(planning_table, duty_target, rng)

    mode, solver, x, total_duties, weekend_count, weekend_imbalance, preferred_weekend_total = _solve_schedule(
        planning_table=planning_table,
        slots=slots,
        min_gap_days=config.min_gap_days,
        time_limit_seconds=config.time_limit_seconds,
    )

    slot_dates = {slot: _parse_slot_date(slot) for slot in slots}
    clerk_names = planning_table[NAME_COLUMN].tolist()
    schedule_rows = [
        ScheduleRow(
            date=slot,
            assigned_clerk=next(clerk for clerk in clerk_names if solver.Value(x[clerk, slot]) == 1),
            weekend=slot_dates[slot].weekday() >= 5,
        )
        for slot in slots
    ]

    weekend_slots = [slot for slot in slots if slot_dates[slot].weekday() >= 5]
    weekend_preference = {
        row[NAME_COLUMN]: {
            slot: 1 if slot in weekend_slots and int(row[slot]) == 2 else 0
            for slot in slots
        }
        for _, row in planning_table.iterrows()
    }
    summary_rows = [
        SummaryRow(
            name=clerk,
            projected=int(planning_table.loc[planning_table[NAME_COLUMN] == clerk, PROJECTED_COLUMN].iloc[0]),
            assigned=solver.Value(total_duties[clerk]),
            projected_delta=solver.Value(total_duties[clerk])
            - int(planning_table.loc[planning_table[NAME_COLUMN] == clerk, PROJECTED_COLUMN].iloc[0]),
            weekend_duties=solver.Value(weekend_count[clerk]),
            preferred_weekend_slots=sum(weekend_preference[clerk][slot] for slot in weekend_slots),
        )
        for clerk in clerk_names
    ]
    summary_rows.sort(key=lambda row: (-row.assigned, -row.weekend_duties, row.name))

    compliance_rows = _build_compliance_rows(schedule_rows, planning_table)

    for item in summary_rows:
        row_idx = planning_table.index[planning_table[NAME_COLUMN] == item.name].item()
        planning_table.loc[row_idx, "Monthly Duty Points"] = item.assigned

    for item in schedule_rows:
        row_idx = planning_table.index[planning_table[NAME_COLUMN] == item.assigned_clerk].item()
        planning_table.loc[row_idx, item.date] = 3

    result = ScheduleResult(
        mode=mode,
        weekend_imbalance=solver.Value(weekend_imbalance),
        preferred_weekend_assignments=solver.Value(preferred_weekend_total),
        projected_total=int(planning_table[PROJECTED_COLUMN].sum()),
        assigned_total=sum(row.assigned for row in summary_rows),
        excluded_clerks=excluded_clerks,
        unmatched_clerks=unmatched_clerks,
        schedule=schedule_rows,
        summary=summary_rows,
        compliance=compliance_rows,
    )
    return result, planning_table


def apply_reserve_round(planning_table: pd.DataFrame, schedule_rows: list[ScheduleRow]) -> pd.DataFrame:
    next_availability = planning_table.copy()
    for item in schedule_rows:
        row_idx = next_availability.index[next_availability[NAME_COLUMN] == item.assigned_clerk].item()
        next_availability.loc[row_idx, item.date] = 0
    slot_columns = _slot_columns(next_availability)
    next_availability[AVAILABILITY_COLUMN] = (next_availability[slot_columns] > 0).sum(axis=1)
    return next_availability


def generate_schedule_from_inputs(
    availability_df: pd.DataFrame,
    points_csv: str,
    config: SchedulerConfig,
) -> ScheduleResult:
    points_df = load_points(points_csv)
    result, _ = generate_schedule(availability_df=availability_df, points_df=points_df, config=config)
    return result


def generate_reserve_schedules_from_inputs(
    availability_df: pd.DataFrame,
    points_csv: str,
    config: SchedulerConfig,
    reserve_rounds: int,
) -> ReserveScheduleResponse:
    points_df = load_points(points_csv)
    primary, planning_table = generate_schedule(availability_df=availability_df, points_df=points_df, config=config)

    reserves: list[ScheduleResult] = []
    current_availability = apply_reserve_round(planning_table, primary.schedule)
    for _ in range(reserve_rounds):
        reserve_result, reserve_planning_table = generate_schedule(
            availability_df=availability_df,
            points_df=points_df,
            config=config,
            availability_override=current_availability,
        )
        reserves.append(reserve_result)
        current_availability = apply_reserve_round(reserve_planning_table, reserve_result.schedule)

    return ReserveScheduleResponse(primary=primary, reserves=reserves)
