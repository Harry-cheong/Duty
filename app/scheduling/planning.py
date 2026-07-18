from __future__ import annotations

import heapq
import random

import pandas as pd

from scheduling.config import (
    AVAILABILITY_COLUMN,
    DUTY_COLUMN,
    OBLIGATION_DUTY_COLUMN,
    OBLIGATION_RESERVE_COLUMN,
    PROJECTED_DUTY_COLUMN,
    PROJECTED_RESERVE_COLUMN,
    RESERVE_COLUMN,
    SchedulerConfig,
)


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
