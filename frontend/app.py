from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import requests
import streamlit as st
import calendar
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

st.set_page_config(
    page_title="Totally Fair Scheduler",
    page_icon="TF",
    layout="wide",
)

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"


def post_json(base_url: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.post(
        f"{base_url.rstrip('/')}{path}",
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def get_json(base_url: str, path: str) -> Dict[str, Any]:
    response = requests.get(f"{base_url.rstrip('/')}{path}", timeout=10)
    response.raise_for_status()
    return response.json()


def dataframe_from_rows(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def render_result(result: Dict[str, Any], heading: str) -> None:
    st.subheader(heading)

    metric_columns = st.columns(4)
    metric_columns[0].metric("Mode", result["mode"])
    metric_columns[1].metric("Assigned", result["assigned_total"])
    metric_columns[2].metric("Weekend Imbalance", result["weekend_imbalance"])
    metric_columns[3].metric("Preferred Weekends", result["preferred_weekend_assignments"])

    notes = []
    if result["excluded_clerks"]:
        notes.append(f"Excluded: {', '.join(result['excluded_clerks'])}")
    if result["unmatched_clerks"]:
        notes.append(f"Missing points: {', '.join(result['unmatched_clerks'])}")
    if notes:
        st.caption(" | ".join(notes))

    schedule_df = dataframe_from_rows(result["schedule"])
    summary_df = dataframe_from_rows(result["summary"])
    compliance_df = dataframe_from_rows(result["compliance"])

    tab_schedule, tab_summary, tab_compliance = st.tabs(["Schedule", "Summary", "Compliance"])

    with tab_schedule:
        st.dataframe(schedule_df, use_container_width=True, hide_index=True)

    with tab_summary:
        st.dataframe(summary_df, use_container_width=True, hide_index=True)

    with tab_compliance:
        if compliance_df.empty:
            st.info("No compliance rows returned.")
        else:
            st.dataframe(compliance_df, use_container_width=True, hide_index=True)

with st.sidebar:
    st.header("Backend")
    backend_url = st.text_input("Backend URL", value=DEFAULT_BACKEND_URL)

    health_clicked = st.button("Check Backend", use_container_width=True)
    if health_clicked:
        try:
            health = get_json(backend_url, "/health")
            st.success(f"Backend status: {health['status']}")
        except requests.RequestException as exc:
            st.error(f"Backend unavailable: {exc}")

    st.header("Inputs")
    year = st.number_input("Year", min_value=2000, max_value=2100, value=2026, step=1)
    month = st.number_input("Month", min_value=1, max_value=12, value=4, step=1)
    availability_csv = st.text_input("Availability CSV", value="../Availability.csv")
    points_csv = st.text_input("Points CSV", value="../march_points.csv")
    min_gap_days = st.slider("Min Gap Days", min_value=1, max_value=31, value=7)
    time_limit_seconds = st.slider("Solver Time Limit", min_value=1, max_value=120, value=10)
    use_random_seed = st.toggle("Use Fixed Random Seed", value=True)
    random_seed = st.number_input("Random Seed", value=42, step=1, disabled=not use_random_seed)
    reserve_rounds = st.slider("Reserve Rounds", min_value=0, max_value=5, value=2)

### Configurations ###
st.title("Slot Configurations")
st.caption("Set duty points assigned per day")

_, last_day = calendar.monthrange(year, month)
days = pd.date_range(f"{year}-{month:02d}-01", periods=last_day)
day_strings = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
df = pd.DataFrame(
    {
        "Date":[day.strftime("%d/%m/%Y") for day in days],
        "Day":[day_strings[pd.to_datetime(dateObj).weekday()] for dateObj in days],
        "Slot 1": [True for _ in range(len(days))],
        "Slot 2": [True if pd.to_datetime(dateObj).weekday() >= 5 else False for dateObj in days]
    },
)
def highlight_weekends(row, color="#D3D3D3"):
    if row.Day in ["Sat", "Sun"]:
        return [f'background-color: {color}'] * len(row)  # * len(row) applies to ALL columns
    return [''] * len(row)

styled_df = df.style.apply(highlight_weekends, color="#2C3A4A", axis=1)
edited_df = st.data_editor(styled_df, disabled=["Date", "Day"])

# Collect user input and generate slots
slots = []
warning_slots = []
for i, row in edited_df.iterrows():
    date = row["Date"]
    slot1 = row["Slot 1"]
    slot2 = row["Slot 2"]
    if slot1 and slot2:
        slots.append(date + " AM")
        slots.append(date + " PM")
    elif slot1:
        slots.append(date)
    else:
        warning_slots.append(date)
if warning_slots: st.markdown(f"No Slot or Invalid Slot Combination on the following days: {' '.join(warning_slots)}")

### Availability and Preference ###
st.title("Availability and Preferences")
st.caption("Indicate the availability and preferences of every clerk")
clerks_df = pd.read_csv("../Personnel List.csv")

availability_df = pd.DataFrame({
    'No': range(1, len(clerks_df) + 1), 
    'Name': clerks_df['Name'],
    **{slot: 1 for slot in slots}
})

cell_style_js = JsCode("""
    function(params) {
        if (params.value === 0) {
            return { 'background-color': '#000000' };
        } else if (params.value === 2) {
            return { 'background-color': '#7B2FBE' };
        }
        return {};
    }
""")

gb = GridOptionsBuilder.from_dataframe(availability_df)

gb.configure_default_column(editable=True) 

gb.configure_column('No', pinned='left', width=50, minWidth=50, editable=False)
gb.configure_column('Name', pinned='left', width=150, minWidth=200, editable=False)

for col in slots:
    gb.configure_column(
        col,
        cellStyle=cell_style_js,
        minWidth=100,
        wrapHeaderText=True,
        autoHeaderHeight=True,
        filterable=False
    )

gb.configure_grid_options(
    rowHeight=35,
    headerHeight=35,
    rowNumbers = True # shows row index on the left
)

AgGrid(
    availability_df,
    gridOptions=gb.build(),
    allow_unsafe_jscode=True,
    theme='streamlit',
    fit_columns_on_grid_load=False,
    height=(len(availability_df) + 1) * 35 + 3,
)

### Scheduler ###
st.title("Totally Fair Scheduler")
st.caption("Minimal UI for the FastAPI scheduling backend.")

payload = {
    "year": int(year),
    "month": int(month),
    "availability_csv": availability_csv,
    "points_csv": points_csv,
    "min_gap_days": int(min_gap_days),
    "time_limit_seconds": int(time_limit_seconds),
    "use_random_seed": use_random_seed,
    "random_seed": int(random_seed) if use_random_seed else None,
}

primary_col, reserve_col = st.columns(2)
generate_primary = primary_col.button("Generate Primary Schedule", use_container_width=True, type="primary")
generate_reserves = reserve_col.button("Generate With Reserves", use_container_width=True)


if "primary_result" not in st.session_state:
    st.session_state.primary_result = None
if "reserve_results" not in st.session_state:
    st.session_state.reserve_results = None


if generate_primary:
    try:
        with st.spinner("Generating primary schedule..."):
            st.session_state.primary_result = post_json(backend_url, "/schedule", payload)
            st.session_state.reserve_results = None
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        st.error(f"Request failed: {detail}")
    except requests.RequestException as exc:
        st.error(f"Backend request failed: {exc}")


if generate_reserves:
    reserve_payload = {**payload, "reserve_rounds": int(reserve_rounds)}
    try:
        with st.spinner("Generating primary and reserve schedules..."):
            st.session_state.reserve_results = post_json(backend_url, "/schedule/reserves", reserve_payload)
            st.session_state.primary_result = st.session_state.reserve_results["primary"]
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        st.error(f"Request failed: {detail}")
    except requests.RequestException as exc:
        st.error(f"Backend request failed: {exc}")


if st.session_state.primary_result:
    render_result(st.session_state.primary_result, "Primary Schedule")
else:
    st.info("Generate a schedule to see results.")


if st.session_state.reserve_results and st.session_state.reserve_results["reserves"]:
    st.divider()
    for index, reserve_result in enumerate(st.session_state.reserve_results["reserves"], start=1):
        render_result(reserve_result, f"Reserve {index}")
