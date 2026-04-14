from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

from inputs import (
    DEFAULT_PERSONNEL_CSV,
    DEFAULT_POINTS_CSV,
    availability_for_solver,
    build_availability_template,
    build_slot_config,
    load_clerks,
    slot_labels_from_config,
)
from scheduler_core import (
    SchedulerConfig,
    generate_reserve_schedules_from_inputs,
    generate_schedule_from_inputs,
)

st.set_page_config(page_title="Totally Fair Scheduler", page_icon="TF", layout="wide")

def dataframe_from_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)

def render_result(result: dict[str, Any], heading: str) -> None:
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
    st.header("Inputs")
    year = st.number_input("Year", min_value=2000, max_value=2100, value=2026, step=1)
    month = st.number_input("Month", min_value=1, max_value=12, value=4, step=1)
    personnel_csv = st.text_input("Personnel CSV", value=DEFAULT_PERSONNEL_CSV)
    points_csv = st.text_input("Points CSV", value=DEFAULT_POINTS_CSV)
    min_gap_days = st.slider("Min Gap Days", min_value=1, max_value=31, value=7)
    time_limit_seconds = st.slider("Solver Time Limit", min_value=1, max_value=120, value=10)
    use_random_seed = st.toggle("Use Fixed Random Seed", value=True)
    random_seed = st.number_input("Random Seed", value=42, step=1, disabled=not use_random_seed)
    reserve_rounds = st.slider("Reserve Rounds", min_value=0, max_value=5, value=2)

### Configurations ###
st.title("Slot Configurations")
st.caption("Set duty points assigned per day")

slot_config_key = f"slot_config_{year}_{month}"
if slot_config_key not in st.session_state:
    st.session_state[slot_config_key] = build_slot_config(int(year), int(month))


def highlight_weekends(row, color="#D3D3D3"):
    if row.Day in ["Sat", "Sun"]:
        return [f"background-color: {color}"] * len(row)
    return [""] * len(row)

styled_df = st.session_state[slot_config_key].style.apply(highlight_weekends, color="#2C3A4A", axis=1)
edited_df = st.data_editor(styled_df, disabled=["Date", "Day"])
st.session_state[slot_config_key] = edited_df

slots, warning_slots = slot_labels_from_config(edited_df)
if warning_slots:
    st.markdown(f"No Slot or Invalid Slot Combination on the following days: {' '.join(warning_slots)}")

### Availability and Preference ###
st.title("Availability and Preferences")
st.caption("Indicate the availability and preferences of every clerk")

try:
    clerks_df = load_clerks(personnel_csv)
except FileNotFoundError:
    st.error(f"Personnel CSV not found: {Path(personnel_csv).resolve()}")
    st.stop()
except Exception as exc:
    st.error(f"Unable to load personnel CSV: {exc}")
    st.stop()

expected_columns = ["No", "Name", *slots]
availability_df = build_availability_template(clerks_df, slots)
availability_grid_key = f"availability_grid_{year}_{month}_{len(slots)}_{Path(personnel_csv).resolve()}"

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

grid_response = AgGrid(
    availability_df,
    gridOptions=gb.build(),
    allow_unsafe_jscode=True,
    theme='streamlit',
    fit_columns_on_grid_load=False,
    height=min((len(availability_df) + 1) * 35 + 3, 600),
    key=availability_grid_key,
)
edited_availability_df = pd.DataFrame(grid_response["data"])[expected_columns]

### Scheduler ###
st.title("Totally Fair Scheduler")
st.caption("Single-process desktop scheduler.")

solver_config = SchedulerConfig(
    min_gap_days=int(min_gap_days),
    time_limit_seconds=int(time_limit_seconds),
    use_random_seed=use_random_seed,
    random_seed=int(random_seed) if use_random_seed else 42,
)

try:
    solver_availability_df = availability_for_solver(edited_availability_df, slots)
except Exception as exc:
    st.error(f"Invalid availability grid data: {exc}")
    st.stop()

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
            st.session_state.primary_result = generate_schedule_from_inputs(
                availability_df=solver_availability_df,
                points_csv=points_csv,
                config=solver_config,
            ).to_dict()
            st.session_state.reserve_results = None
    except Exception as exc:
        st.error(f"Schedule generation failed: {exc}")

if generate_reserves:
    try:
        with st.spinner("Generating primary and reserve schedules..."):
            reserve_response = generate_reserve_schedules_from_inputs(
                availability_df=solver_availability_df,
                points_csv=points_csv,
                config=solver_config,
                reserve_rounds=int(reserve_rounds),
            )
            st.session_state.reserve_results = {
                "primary": reserve_response.primary.to_dict(),
                "reserves": [reserve.to_dict() for reserve in reserve_response.reserves],
            }
            st.session_state.primary_result = st.session_state.reserve_results["primary"]
    except Exception as exc:
        st.error(f"Schedule generation failed: {exc}")

if st.session_state.primary_result:
    render_result(st.session_state.primary_result, "Primary Schedule")
else:
    st.info("Generate a schedule to see results.")


if st.session_state.reserve_results and st.session_state.reserve_results["reserves"]:
    st.divider()
    for index, reserve_result in enumerate(st.session_state.reserve_results["reserves"], start=1):
        render_result(reserve_result, f"Reserve {index}")

@st.cache_data
def convert_for_download(df):
    return df.to_csv(index=False).encode("utf-8")

csv = convert_for_download(edited_availability_df)

st.download_button(
    label="Download CSV",
    data=csv,
    file_name="data.csv",
    mime="text/csv",
    icon=":material/download:",
)
