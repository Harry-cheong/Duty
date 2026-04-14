from __future__ import annotations

import calendar
from pathlib import Path

import pandas as pd

DEFAULT_POINTS_CSV = "../points/march.csv"
DEFAULT_PERSONNEL_CSV = "../personnel.csv"

def build_slot_config(year: int, month: int) -> pd.DataFrame:
    _, last_day = calendar.monthrange(year, month)
    days = pd.date_range(f"{year}-{month:02d}-01", periods=last_day)
    day_strings = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return pd.DataFrame(
        {
            "Date": [day.strftime("%d/%m/%Y") for day in days],
            "Day": [day_strings[day.weekday()] for day in days],
            "Slot 1": [True for _ in range(len(days))],
            "Slot 2": [day.weekday() >= 5 for day in days],
        },
    )

def slot_labels_from_config(config_df: pd.DataFrame) -> tuple[list[str], list[str]]:
    slots: list[str] = []
    warning_slots: list[str] = []
    for _, row in config_df.iterrows():
        date = row["Date"]
        day = row["Day"]
        slot1 = bool(row["Slot 1"])
        slot2 = bool(row["Slot 2"])
        if slot1 and slot2:
            slots.append(f"{date} {day} AM")
            slots.append(f"{date} {day} PM")
        elif slot1:
            slots.append(f"{date} {day}")
        else:
            warning_slots.append(date)
    return slots, warning_slots

def load_clerks(personnel_csv: str = DEFAULT_PERSONNEL_CSV) -> pd.DataFrame:
    return pd.read_csv(Path(personnel_csv))


def build_availability_template(clerks_df: pd.DataFrame, slots: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "No": range(1, len(clerks_df) + 1),
            "Name": clerks_df["Name"],
            **{slot: 1 for slot in slots},
        }
    )

def availability_for_solver(grid_df: pd.DataFrame, slots: list[str]) -> pd.DataFrame:
    availability_df = grid_df.copy()
    availability_df["Name"] = availability_df["Name"].astype(str)

    for slot in slots:
        availability_df[slot] = (
            pd.to_numeric(availability_df[slot], errors="coerce")
            .fillna(0)
            .astype(int)
            .clip(lower=0, upper=2)
        )

    availability_df["Availability"] = (availability_df[slots] > 0).sum(axis=1)
    return availability_df[["Name", *slots, "Availability"]]

def load_points(points_csv: str) -> pd.DataFrame:
    return pd.read_csv(Path(points_csv))
