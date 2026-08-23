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


def year_suffix(year: int) -> str:
    """Two-digit year used in worksheet titles (e.g. 2026 → '26')."""
    return f"{int(year) % 100:02d}"


def master_overview_title(year: int, month: int) -> str:
    return f"{MONTH_COLUMN_NAMES[int(month)]}{year_suffix(year)} Master Duty Overview"


def send_out_title(year: int, month: int) -> str:
    return f"{MONTH_COLUMN_NAMES[int(month)]}{year_suffix(year)} Send Out"


def previous_months(year: int, month: int, count: int = 2) -> list[tuple[int, int]]:
    """
    Return (year, month) pairs for the previous `count` months,
    most recent first. Wraps across year boundaries.
    """
    result: list[tuple[int, int]] = []
    y, m = int(year), int(month)
    for _ in range(count):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        result.append((y, m))
    return result


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


def parse_availability_json(text: str):
    """Parse the LLM paste from Step 3 into a list of [name, unavail, pref] triples."""
    payload = (text or "").strip()
    if payload.startswith("```"):
        payload = re.sub(r"^```(?:json)?\s*", "", payload, flags=re.IGNORECASE)
        payload = re.sub(r"\s*```$", "", payload)
    data = json.loads(payload)
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of [name, unavailable, preferences]")
    for i, row in enumerate(data):
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise ValueError(
                f"Entry {i} must be [RANK & NAME, unavailable list, preference list], got {row!r}"
            )
    return data


def _normalize_piece(piece: str):
    cleaned = piece.strip().strip("()[]").strip()
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned)
    return cleaned


def _token_parts(token) -> list:
    '''
    Check for validity of token
    '''
    if isinstance(token, bool) or token is None:
        raise ValueError(f"Error parsing token {token!r}")
    if isinstance(token, int):
        return [token]
    if isinstance(token, float) and token.is_integer():
        return [int(token)]
    if not isinstance(token, str):
        raise ValueError(f"Error parsing token {token!r}")

    pieces = []
    for raw in re.sub(r"[()]", " ", token).split():
        part = _normalize_piece(raw)
        if part is not None:
            pieces.append(part)
    if "24hr" in pieces:
        pieces = [part for part in pieces if part != "24hr"]
    if not pieces:
        raise ValueError(f"Error parsing token {token!r}")
    return pieces


def match_token_to_slots(tokens, slot_metadata: list[dict[str, object]]) -> set[str]:
    slots = set()
    if not tokens:
        return slots
    simple = []
    complex = []

    for token in tokens:
        parts = _token_parts(token)
        if len(parts) == 1:
            simple.append(parts[0])
        elif len(parts) == 2:
            complex.append(frozenset(str(part) for part in parts))
        else:
            raise ValueError(f"Error parsing token {token!r}")

    for metadata in slot_metadata:
        props = {
            str(metadata["day"]),
            metadata["day_type"],
            metadata["day_name"],
        }
        if metadata["shift"]:
            props.add(metadata["shift"])

        # Simple tokens e.g. "Monday", "Weekends", 11, "AM"
        if (
            metadata["day"] in simple
            or str(metadata["day"]) in simple
            or metadata["day_type"] in simple
            or metadata["day_name"] in simple
            or (metadata["shift"] is not None and metadata["shift"] in simple)
        ):
            slots.add(metadata["slot"])
            continue

        # Complex tokens e.g. "Weekends AM", "13 (AM)", "Monday PM"
        for token_set in complex:
            if token_set.issubset(props):
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

def resolve_clerk_name(clerk_name, personnel_names):
    """Map an LLM name onto a personnel RANK & NAME label, ignoring case."""
    wanted = str(clerk_name).strip()
    if wanted in personnel_names:
        return wanted
    folded = wanted.casefold()
    matches = [
        label for label in personnel_names
        if str(label).strip().casefold() == folded
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def build_availability_from_input(
    clerks_df: pd.DataFrame,
    response_json,
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
        
    for clerk, unavail, pref in response_json:
        clerk_name = resolve_clerk_name(clerk, availability_df.index)
        if clerk_name is None:
            continue

        unavailable_slots = match_token_to_slots(unavail, slot_metadata)
        for slot in unavailable_slots:
            availability_df.loc[clerk_name, slot] = 0

        preferred_slots = match_token_to_slots(pref, slot_metadata)
        for slot in preferred_slots:
            # Unavailable dates should take precedence over preferred dates
            if int(availability_df.loc[clerk_name, slot]) == 1:
                availability_df.loc[clerk_name, slot] = 2

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
