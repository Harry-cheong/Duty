from __future__ import annotations

import calendar
import re
from functools import lru_cache
from pathlib import Path

import holidays
import pandas as pd

DEFAULT_POINTS_CSV = "../dutypts.csv"
DEFAULT_PERSONNEL_CSV = "../personnel.csv"
DEFAULT_AVAILABILITY_INPUT_CSV = "../availability_input.csv"
DEFAULT_AVAILABILITY_OUTPUT_CSV = "../Availability.csv"
MONTH_COLUMN_NAMES = {
    1: "JAN",
    2: "FEB",
    3: "MAR",
    4: "APR",
    5: "MAY",
    6: "JUN",
    7: "JUL",
    8: "AUG",
    9: "SEP",
    10: "OCT",
    11: "NOV",
    12: "DEC",
}


@lru_cache(maxsize=None)
def _singapore_public_holiday_lookup(year: int) -> dict[object, str]:
    return {
        holiday_date: str(name)
        for holiday_date, name in holidays.country_holidays("SG", years=year).items()
    }


def singapore_public_holiday_name(date_value: object) -> str:
    date = pd.Timestamp(date_value).date()
    return _singapore_public_holiday_lookup(date.year).get(date, "")


def build_slot_config(year: int, month: int) -> pd.DataFrame:
    _, last_day = calendar.monthrange(year, month)
    days = pd.date_range(f"{year}-{month:02d}-01", periods=last_day)
    day_strings = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    holiday_labels = [singapore_public_holiday_name(day) for day in days]
    return pd.DataFrame(
        {
            "Date": [day.strftime("%d/%m/%Y") for day in days],
            "Day": [day_strings[day.weekday()] for day in days],
            "Holiday": [f"PH: {label}" if label else "" for label in holiday_labels],
            "Slot 1": [True for _ in range(len(days))],
            "Slot 2": [day.weekday() >= 5 or bool(label) for day, label in zip(days, holiday_labels)],
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


def _split_csv_values(value: object) -> list[str]:
    if pd.isna(value):
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _parse_unavailable_days(value: object, year: int, month: int) -> set[int]:
    _, last_day = calendar.monthrange(year, month)
    unavailable_days: set[int] = set()
    for item in _split_csv_values(value):
        match = re.search(r"\d+", item)
        if not match:
            continue
        day = int(match.group())
        if 1 <= day <= last_day:
            unavailable_days.add(day)
    return unavailable_days


def _slot_metadata(slot: str) -> dict[str, object]:
    date_text = str(slot)[:10]
    slot_date = pd.to_datetime(date_text, format="%d/%m/%Y")
    suffix = None
    if str(slot).endswith(" AM"):
        suffix = "AM"
    elif str(slot).endswith(" PM"):
        suffix = "PM"
    return {
        "slot": slot,
        "date": slot_date,
        "day_name": slot_date.day_name(),
        "suffix": suffix,
    }


def _preferred_slots_for_token(token: str, slot_metadata: list[dict[str, object]]) -> set[str]:
    normalized = " ".join(str(token).strip().lower().split())
    if not normalized:
        return set()

    parts = normalized.split()
    day_name = parts[0].capitalize() if parts else ""
    suffix = parts[1].upper() if len(parts) > 1 else None
    if day_name not in {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    }:
        return set()

    matched_slots: set[str] = set()
    for item in slot_metadata:
        if item["day_name"] != day_name:
            continue
        if suffix is None:
            matched_slots.add(str(item["slot"]))
            continue
        item_suffix = item["suffix"]
        if item_suffix == suffix:
            matched_slots.add(str(item["slot"]))
        elif item_suffix is None and day_name in {"Saturday", "Sunday"}:
            matched_slots.add(str(item["slot"]))
    return matched_slots


def _latest_responses_by_name(input_df: pd.DataFrame) -> pd.DataFrame:
    if input_df.empty:
        return input_df

    responses = input_df.copy()
    responses["Timestamp"] = pd.to_datetime(
        responses["Timestamp"],
        dayfirst=True,
        errors="coerce",
    )
    responses = responses.dropna(subset=["Your Name (Select from list)"])
    responses["Your Name (Select from list)"] = responses["Your Name (Select from list)"].astype(str).str.strip()
    responses = responses.sort_values("Timestamp")
    return responses.drop_duplicates(subset=["Your Name (Select from list)"], keep="last")


def build_availability_from_input(
    clerks_df: pd.DataFrame,
    slots: list[str],
    availability_input_csv: str,
    output_csv: str,
    year: int,
    month: int,
) -> pd.DataFrame:
    responses_df = pd.read_csv(Path(availability_input_csv))
    required_columns = {"Timestamp", "Your Name (Select from list)"}
    missing_columns = sorted(required_columns - set(responses_df.columns))
    if missing_columns:
        raise ValueError(
            "Availability input CSV is missing required columns: "
            + ", ".join(missing_columns)
        )
    latest_responses = _latest_responses_by_name(responses_df)

    availability_df = pd.DataFrame(
        {
            "Name": clerks_df["Name"].astype(str).str.strip(),
            **{slot: 1 for slot in slots},
        }
    )
    slot_metadata = [_slot_metadata(slot) for slot in slots]

    response_lookup = latest_responses.set_index("Your Name (Select from list)") if not latest_responses.empty else None

    for row_idx, clerk_name in enumerate(availability_df["Name"]):
        if response_lookup is None or clerk_name not in response_lookup.index:
            continue

        response = response_lookup.loc[clerk_name]
        if isinstance(response, pd.DataFrame):
            response = response.iloc[-1]

        preferred_tokens = _split_csv_values(response.get("Preferred Duty Days"))
        unavailable_days = _parse_unavailable_days(response.get("Select unavailable dates"), year, month)

        preferred_slots: set[str] = set()
        for token in preferred_tokens:
            preferred_slots.update(_preferred_slots_for_token(token, slot_metadata))

        for slot in preferred_slots:
            availability_df.loc[row_idx, slot] = 2

        for item in slot_metadata:
            slot_date = item["date"]
            if int(slot_date.day) in unavailable_days:
                availability_df.loc[row_idx, str(item["slot"])] = 0

    normalized_df = availability_for_solver(availability_df, slots)
    normalized_df.to_csv(Path(output_csv), index=False)
    return normalized_df


def grid_from_normalized_availability(availability_df: pd.DataFrame, slots: list[str]) -> pd.DataFrame:
    grid_df = availability_df[["Name", *slots]].copy()
    grid_df.insert(0, "No", range(1, len(grid_df) + 1))
    return grid_df

def availability_for_solver(grid_df: pd.DataFrame, slots: list[str]) -> pd.DataFrame:
    availability_df = grid_df.copy()
    availability_df["Name"] = availability_df["Name"].astype(str).str.strip()

    for slot in slots:
        availability_df[slot] = (
            pd.to_numeric(availability_df[slot], errors="coerce")
            .fillna(0)
            .astype(int)
            .clip(lower=0, upper=2)
        )

    availability_df["Availability"] = (availability_df[slots] > 0).sum(axis=1)
    return availability_df[["Name", *slots, "Availability"]]

def load_points(points_csv: str, month: int, monthly_obligation: float) -> pd.DataFrame:
    points_df = pd.read_csv(Path(points_csv))
    if "Name" not in points_df.columns:
        raise ValueError("Points CSV must include a 'Name' column.")

    selected_month = int(month)
    previous_months = [
        MONTH_COLUMN_NAMES.get(((selected_month - offset - 1) % 12) + 1)
        for offset in range(1, 3)
    ]
    if any(month_name is None for month_name in previous_months):
        raise ValueError(f"Unsupported month value: {month}")

    missing_months = [month_name for month_name in previous_months if month_name not in points_df.columns]
    if missing_months:
        available_columns = ", ".join(str(column) for column in points_df.columns)
        raise ValueError(
            f"{', '.join(missing_months)} data is not found. "
            f"Available columns: {available_columns}"
        )

    historical_points = points_df[previous_months].apply(pd.to_numeric, errors="coerce")
    cumulative_points_df = pd.DataFrame(
        {
            "Name": points_df["Name"].astype(str).str.strip(),
            **{month_name: historical_points[month_name] for month_name in previous_months},
            "Duty": historical_points.fillna(0).sum(axis=1),
            "Obligation": monthly_obligation * (historical_points.notna().sum(axis=1) + 1),
        }
    )

    return cumulative_points_df
