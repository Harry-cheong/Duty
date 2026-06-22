from __future__ import annotations

import calendar
import re
from functools import lru_cache
import holidays
import pandas as pd
import json

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
    day_strings = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    holiday_labels = [singapore_public_holiday_name(day) for day in days]
    return pd.DataFrame(
        {
            "Date": [day.strftime("%d-%m-%y") for day in days],
            "Day": [day_strings[day.weekday()] for day in days],
            "Holiday": [f"PH: {label}" if label else "" for label in holiday_labels],
            "Slot 1": [True for _ in range(len(days))],
            "Slot 2": [day.weekday() >= 5 or bool(label) for day, label in zip(days, holiday_labels)],
        },
    )

def slot_labels_from_config(config_df: pd.DataFrame) -> tuple[list[str], list[str]]:
    slots: list[str] = []
    days: list[str] = []
    warning_slots: list[str] = []
    for _, row in config_df.iterrows():
        date = row["Date"]
        day = row["Day"]
        slot1 = bool(row["Slot 1"])
        slot2 = bool(row["Slot 2"])
        if slot1 and slot2:
            slots.append(f"{date} (AM)")
            days.append(day)
            slots.append(f"{date} (PM)")
            days.append(day)
        elif slot1:
            slots.append(f"{date}")
            days.append(day)
        else:
            warning_slots.append(date)
    return slots, days, warning_slots

def build_availability_template(clerks_df: pd.DataFrame, slots: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "No": range(1, len(clerks_df) + 1),
            "Name": clerks_df["Name"],
            **{slot: 1 for slot in slots},
        }
    )


def _preferred_slots_for_token(tokens: str, slot_metadata: list[dict[str, object]]) -> set[str]:
    tokens = json.loads(tokens)
    slots = set()
    if not tokens:
        return set()
    simple = [t for t in tokens if len(t.split()) == 1]
    complex = [set(t.split()) for t in tokens if len(t.split()) == 2]
    if any(token not in simple and token not in complex for token in tokens):
        raise ValueError(f"Error parsing token '{tokens}'")
    
    # TODO: What if simple and complex tokens conflict?
    for metadata in slot_metadata:
        # Simple Tokens e.g. "Monday", "Tuesday", "Weekends", 11
        if str(metadata["day"]) in simple or metadata["day_type"]in simple or metadata["day_name"] in simple:
            slots.add(metadata["slot"])
            continue
        
        # Complex Tokens e.g. "Weekends AM"
        metadata_set = set([str(metadata["day"]), metadata["day_type"], metadata["day_name"]])
        for t in complex:
            if t.issubset(metadata_set):
                slots.add(metadata["slot"])
                break
        
    return slots


def _slot_metadata(slot, slot_as_day) -> dict[str, object]:
    # Expected slot format: dd/mm/yy (AM)
    if len(slot.split()) > 1:
        date, shift = slot.split()
        shift = shift.replace("(", "").replace(")", "")
    else:
        date = slot
        shift = None

    _day, _month, _year = date.split("-")

    if slot_as_day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        day_type = "Weekdays"
    else:
        day_type = "Weekends"
    
    return {
        "slot": slot, # dd/mm/yy (AM)
        "date": date, # dd/mm/yy
        "day": int(_day),
        "day_type": day_type,
        "shift": shift,
        "day_name": slot_as_day
    }

def build_availability_from_input(
    clerks_df: pd.DataFrame,
    response_df: pd.DataFrame,
    slots: list[str],
    slots_as_days: list[str],
):

    availability_df = pd.DataFrame(
        {
            "RANK & NAME": clerks_df["RANK & NAME"].astype(str).str.strip().reset_index(drop=True),
            **{slot: 1 for slot in slots},
        }
    )
    availability_df = availability_df.set_index("RANK & NAME")

    slot_metadata = []
    for s, d in zip(slots, slots_as_days):
        slot_metadata.append(_slot_metadata(s, d))

    response_lookup = response_df.set_index("RANK & NAME") if not response_df.empty else None

    for clerk_name in availability_df.index:
        if clerk_name.strip() not in response_lookup.index: # Clerk has not indicated unavailable dates and preferrences
            continue

        response = response_lookup.loc[clerk_name]
        if isinstance(response, pd.DataFrame):
            response = response.iloc[-1]

        unavailable_days = response.get("Unavailable Dates")
        
        # Parse back from JSON string if needed
        if isinstance(unavailable_days, str):
            unavailable_days = json.loads(unavailable_days)
        if not isinstance(unavailable_days, list):
            unavailable_days = []

        for item in slot_metadata:
            slot_date = item["day"]
            if slot_date in unavailable_days:
                availability_df.loc[clerk_name, str(item["slot"])] = 0
        
        preferrence_tokens = response.get("Preferrences")
        preferred_slots = _preferred_slots_for_token(preferrence_tokens, slot_metadata)

        for p in preferred_slots:
            # Unavailable dates should take precedence over preferred dates
            if int(availability_df.loc[clerk_name, p]) == 1:
                availability_df.loc[clerk_name, p] = 2

    return availability_df

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
