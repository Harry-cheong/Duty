from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

from inputs import (
    DEFAULT_AVAILABILITY_INPUT_CSV,
    DEFAULT_AVAILABILITY_OUTPUT_CSV,
    DEFAULT_PERSONNEL_CSV,
    DEFAULT_POINTS_CSV,
    MONTH_COLUMN_NAMES,
    availability_for_solver,
    build_availability_from_input,
    build_slot_config,
    grid_from_normalized_availability,
    load_clerks,
    slot_labels_from_config,
    load_points,
)
from scheduler_core import (
    SchedulerConfig,
    generate_reserve_schedules_from_inputs,
    generate_schedule_from_inputs,
    project_duties_preview,
)

st.set_page_config(page_title="Totally Fair Scheduler", page_icon="TF", layout="wide")

def dataframe_from_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def with_visual_index(df: pd.DataFrame) -> pd.DataFrame:
    indexed_df = df.copy()
    indexed_df.index = pd.RangeIndex(start=1, stop=len(indexed_df) + 1, step=1)
    return indexed_df


def table_dimensions_caption(df: pd.DataFrame) -> str:
    return f"{df.shape[1]} columns x {df.shape[0]} rows"


def render_dataframe_with_meta(df: pd.DataFrame) -> None:
    display_df = with_visual_index(df)
    st.dataframe(display_df, use_container_width=True, hide_index=False)
    st.caption(table_dimensions_caption(df))


def render_data_editor_with_meta(df: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
    display_df = with_visual_index(df)
    disabled_columns = list(kwargs.pop("disabled", []))
    editor_df = st.data_editor(display_df, disabled=["_index", *disabled_columns], hide_index=False, **kwargs)
    st.caption(table_dimensions_caption(df))
    return pd.DataFrame(editor_df).reset_index(drop=True)


def build_availability_mismatch_table(
    availability_names: list[str],
    points_names: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    points_lookup = set(points_names)
    for availability_name in availability_names:
        if availability_name in points_lookup:
            continue
        rows.append(
            {
                "Availability Name": availability_name,
                "Match To DutyPts Name": "",
                "Disable": False,
            }
        )

    return pd.DataFrame(
        rows,
        columns=[
            "Availability Name",
            "Match To DutyPts Name",
            "Disable",
        ],
    )


def build_points_mismatch_table(
    availability_names: list[str],
    points_names: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    availability_lookup = set(availability_names)
    for points_name in points_names:
        if points_name in availability_lookup:
            continue
        rows.append(
            {
                "DutyPts Name": points_name,
                "Match To Availability Name": "",
                "Disable": False,
            }
        )

    return pd.DataFrame(
        rows,
        columns=[
            "DutyPts Name",
            "Match To Availability Name",
            "Disable",
        ],
    )


def apply_name_corrections(
    availability_df: pd.DataFrame,
    points_df: pd.DataFrame,
    availability_review_df: pd.DataFrame,
    points_review_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    corrected_availability_df = availability_df.copy()
    corrected_points_df = points_df.copy()
    points_renames: dict[str, str] = {}
    disabled_availability_names: set[str] = set()
    disabled_points_names: set[str] = set()

    for _, row in availability_review_df.iterrows():
        availability_name = str(row.get("Availability Name", "")).strip()
        matched_dutypts_name = str(row.get("Match To DutyPts Name", "")).strip()
        disabled = bool(row.get("Disable", False))
        if not availability_name:
            continue
        if disabled:
            disabled_availability_names.add(availability_name)
            continue
        if matched_dutypts_name:
            points_renames[matched_dutypts_name] = availability_name

    for _, row in points_review_df.iterrows():
        dutypts_name = str(row.get("DutyPts Name", "")).strip()
        matched_availability_name = str(row.get("Match To Availability Name", "")).strip()
        disabled = bool(row.get("Disable", False))
        if not dutypts_name:
            continue
        if disabled:
            disabled_points_names.add(dutypts_name)
            continue
        if matched_availability_name:
            points_renames[dutypts_name] = matched_availability_name

    corrected_availability_df["Name"] = corrected_availability_df["Name"].astype(str).str.strip()
    corrected_availability_df = corrected_availability_df[
        ~corrected_availability_df["Name"].isin(disabled_availability_names)
    ].copy()
    corrected_points_df["Name"] = corrected_points_df["Name"].astype(str).str.strip().map(
        lambda name: points_renames.get(name, name)
    )
    corrected_points_df = corrected_points_df[
        ~corrected_points_df["Name"].isin(disabled_points_names)
    ].copy()
    return corrected_availability_df, corrected_points_df

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
    if not schedule_df.empty:
        schedule_df = schedule_df.rename(
            columns={
                "date": "Date",
                "assigned_clerk": "Assigned Clerk",
                "holiday": "Holiday",
            }
        )
        if "weekend" in schedule_df.columns:
            schedule_df["Weekend"] = schedule_df["weekend"].map(lambda value: "✓" if value else "")
        if "public_holiday" in schedule_df.columns:
            schedule_df["PH"] = schedule_df["public_holiday"].map(lambda value: "✓" if value else "")
        display_columns = [
            column
            for column in ["Date", "Assigned Clerk", "Weekend", "PH", "Holiday"]
            if column in schedule_df.columns
        ]
        schedule_df = schedule_df[display_columns]
    summary_df = dataframe_from_rows(result["summary"])
    compliance_df = dataframe_from_rows(result["compliance"])

    tab_schedule, tab_summary, tab_compliance = st.tabs(["Schedule", "Summary", "Compliance"])

    with tab_schedule:
        render_dataframe_with_meta(schedule_df)

    with tab_summary:
        render_dataframe_with_meta(summary_df)

    with tab_compliance:
        if compliance_df.empty:
            st.info("No compliance rows returned.")
        else:
            render_dataframe_with_meta(compliance_df)

# Defaults
today = datetime.datetime.today()
default_year = today.year if today.month < 12 else today.year + 1
default_month = today.month + 1 if today.month < 12 else 1

with st.sidebar:
    st.header("Inputs")
    year = st.number_input("Year", min_value=2000, max_value=2100, value=default_year, step=1)
    month = st.number_input("Month", min_value=1, max_value=12, value=default_month, step=1)
    monthly_obligation = st.number_input("Duty Per Month", value=1.33)
    personnel_csv = st.text_input("Personnel CSV", value=DEFAULT_PERSONNEL_CSV)
    points_csv = st.text_input("Points CSV", value=DEFAULT_POINTS_CSV)
    availability_input_csv = st.text_input("Availability Input CSV", value=DEFAULT_AVAILABILITY_INPUT_CSV)
    min_gap_days = st.slider("Min Gap Days", min_value=1, max_value=31, value=7)
    time_limit_seconds = st.slider("Solver Time Limit", min_value=1, max_value=120, value=10)
    use_random_seed = st.toggle("Use Fixed Random Seed", value=True)
    random_seed = st.number_input("Random Seed", value=42, step=1, disabled=not use_random_seed)
    reserve_rounds = st.slider("Reserve Rounds", min_value=0, max_value=5, value=2)

### Configurations ###
st.title("Slot Configurations")
st.caption("Set duty points assigned per day. Weekends and Singapore public holidays default to two slots.")

slot_config_key = f"slot_config_{year}_{month}"
if slot_config_key not in st.session_state:
    st.session_state[slot_config_key] = build_slot_config(int(year), int(month))


def highlight_special_days(row, weekend_color="#2C3A4A", holiday_color="#244B36"):
    holiday_label = row.get("Holiday", "")
    if pd.notna(holiday_label) and str(holiday_label).strip():
        return [f"background-color: {holiday_color}"] * len(row)
    if row.Day in ["Sat", "Sun"]:
        return [f"background-color: {weekend_color}"] * len(row)
    return [""] * len(row)

slot_config_display_df = with_visual_index(st.session_state[slot_config_key])
styled_df = slot_config_display_df.style.apply(highlight_special_days, axis=1)
edited_df = st.data_editor(styled_df, disabled=["_index", "Date", "Day", "Holiday"], hide_index=False)
edited_df = pd.DataFrame(edited_df).reset_index(drop=True)
st.caption("`Holiday` rows are Singapore public holidays (PH).")
st.caption(table_dimensions_caption(st.session_state[slot_config_key]))

slots, warning_slots = slot_labels_from_config(edited_df)
if warning_slots:
    st.markdown(f"No Slot or Invalid Slot Combination on the following days: {' '.join(warning_slots)}")

st.markdown(f"Assigned Duty Points: {edited_df['Slot 1'].sum() + edited_df['Slot 2'].sum()}")

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
try:
    normalized_availability_df = build_availability_from_input(
        clerks_df=clerks_df,
        slots=slots,
        availability_input_csv=availability_input_csv,
        output_csv=DEFAULT_AVAILABILITY_OUTPUT_CSV,
        year=int(year),
        month=int(month),
    )
except FileNotFoundError:
    st.error(f"Availability input CSV not found: {Path(availability_input_csv).resolve()}")
    st.stop()
except Exception as exc:
    st.error(f"Unable to load availability input CSV: {exc}")
    st.stop()

availability_df = grid_from_normalized_availability(normalized_availability_df, slots)
availability_grid_key = (
    f"availability_grid_{year}_{month}_{len(slots)}_"
    f"{Path(personnel_csv).resolve()}_{Path(availability_input_csv).resolve()}"
)

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
st.caption(table_dimensions_caption(availability_df))
edited_availability_df = pd.DataFrame(grid_response["data"])[expected_columns]

### Display Duty Points ###
st.title("Duty Point Management")
st.caption("Tabulate duty points done in the last 2 months and project next month's duty points")

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

try:
    points_df = load_points(
        points_csv=points_csv,
        month=int(month),
        monthly_obligation=float(monthly_obligation),
    )
except FileNotFoundError:
    st.error(f"Points CSV not found: {Path(points_csv).resolve()}")
    st.stop()
except Exception as exc:
    st.error(f"Unable to load duty points: {exc}")
    st.stop()

st.header("Name Review")
st.caption("Review clerk-name mismatches between Availability and dutypts. Corrections apply only in memory for this app session.")

availability_names = solver_availability_df["Name"].astype(str).str.strip().tolist()
points_names = points_df["Name"].astype(str).str.strip().tolist()
review_signature = (tuple(availability_names), tuple(points_names))
review_state_prefix = (
    f"name_review_{Path(personnel_csv).resolve()}_{Path(points_csv).resolve()}_"
    f"{Path(availability_input_csv).resolve()}_{year}_{month}"
)
review_signature_key = f"{review_state_prefix}_signature"
availability_review_key = f"{review_state_prefix}_availability"
points_review_key = f"{review_state_prefix}_points"

if st.session_state.get(review_signature_key) != review_signature:
    st.session_state[review_signature_key] = review_signature
    st.session_state[availability_review_key] = build_availability_mismatch_table(availability_names, points_names)
    st.session_state[points_review_key] = build_points_mismatch_table(availability_names, points_names)

availability_review_df = st.session_state[availability_review_key]
points_review_df = st.session_state[points_review_key]

availability_review_col, points_review_col = st.columns(2)

with availability_review_col:
    st.subheader("Availability Not In DutyPts")
    if availability_review_df.empty:
        st.info("No availability-only names.")
    else:
        availability_review_df = render_data_editor_with_meta(
            availability_review_df,
            key=f"{availability_review_key}_editor",
            use_container_width=True,
            disabled=["Availability Name"],
            column_config={
                "Match To DutyPts Name": st.column_config.SelectboxColumn(
                    "Match To DutyPts Name",
                    options=["", *points_names],
                    help="Select the dutypts name that matches this availability name for this session.",
                ),
                "Disable": st.column_config.CheckboxColumn(
                    "Disable",
                    help="Remove this availability name from the current session without changing source files.",
                ),
            },
        )
        st.session_state[availability_review_key] = availability_review_df

with points_review_col:
    st.subheader("DutyPts Not In Availability")
    if points_review_df.empty:
        st.info("No dutypts-only names.")
    else:
        points_review_df = render_data_editor_with_meta(
            points_review_df,
            key=f"{points_review_key}_editor",
            use_container_width=True,
            disabled=["DutyPts Name"],
            column_config={
                "Match To Availability Name": st.column_config.SelectboxColumn(
                    "Match To Availability Name",
                    options=["", *availability_names],
                    help="Select the availability name that matches this dutypts name for this session.",
                ),
                "Disable": st.column_config.CheckboxColumn(
                    "Disable",
                    help="Remove this dutypts name from the current session without changing source files.",
                ),
            },
        )
        st.session_state[points_review_key] = points_review_df

if availability_review_df.empty and points_review_df.empty:
    st.info("No clerk-name mismatches detected between Availability and dutypts.")

selected_dutypts_matches = [
    str(row["Match To DutyPts Name"]).strip()
    for _, row in availability_review_df.iterrows()
    if not bool(row.get("Disable", False)) and str(row.get("Match To DutyPts Name", "")).strip()
]
duplicate_selected_dutypts_matches = sorted(
    {name for name in selected_dutypts_matches if selected_dutypts_matches.count(name) > 1}
)
if duplicate_selected_dutypts_matches:
    st.error(
        "Multiple availability names are matched to the same dutypts name: "
        + ", ".join(duplicate_selected_dutypts_matches)
        + ". Keep each selected match unique."
    )
    st.stop()

selected_availability_matches = [
    str(row["Match To Availability Name"]).strip()
    for _, row in points_review_df.iterrows()
    if not bool(row.get("Disable", False)) and str(row.get("Match To Availability Name", "")).strip()
]
duplicate_selected_availability_matches = sorted(
    {name for name in selected_availability_matches if selected_availability_matches.count(name) > 1}
)
if duplicate_selected_availability_matches:
    st.error(
        "Multiple dutypts names are matched to the same availability name: "
        + ", ".join(duplicate_selected_availability_matches)
        + ". Keep each selected match unique."
    )
    st.stop()

corrected_availability_df, corrected_points_df = apply_name_corrections(
    availability_df=solver_availability_df,
    points_df=points_df,
    availability_review_df=st.session_state[availability_review_key],
    points_review_df=st.session_state[points_review_key],
)

duplicate_corrected_availability_names = corrected_availability_df["Name"][
    corrected_availability_df["Name"].duplicated(keep=False)
].unique().tolist()
if duplicate_corrected_availability_names:
    st.error(
        "Name corrections create duplicate availability names: "
        + ", ".join(sorted(duplicate_corrected_availability_names))
        + ". Adjust the review tables before scheduling."
    )
    st.stop()

duplicate_corrected_points_names = corrected_points_df["Name"][
    corrected_points_df["Name"].duplicated(keep=False)
].unique().tolist()
if duplicate_corrected_points_names:
    st.error(
        "Name corrections create duplicate dutypts names: "
        + ", ".join(sorted(duplicate_corrected_points_names))
        + ". Adjust the review tables before scheduling."
    )
    st.stop()

corrected_availability_names = corrected_availability_df["Name"].astype(str).str.strip().tolist()
corrected_point_names = corrected_points_df["Name"].astype(str).str.strip().tolist()
remaining_unmatched_availability = [
    name for name in corrected_availability_names if name not in set(corrected_point_names)
]
remaining_unmatched_points = [
    name for name in corrected_point_names if name not in set(corrected_availability_names)
]
if remaining_unmatched_availability:
    st.warning(
        "These availability names still do not match any dutypts row and will be excluded from Duty Point Management and scheduling: "
        + ", ".join(remaining_unmatched_availability)
    )
elif not availability_review_df.empty or not points_review_df.empty:
    st.success("All reviewed names now match for this session.")

if remaining_unmatched_points:
    st.caption(
        "DutyPts names still without an availability match in the current session: "
        + ", ".join(remaining_unmatched_points)
    )

try:
    projected_points_df = project_duties_preview(
        availability_df=corrected_availability_df,
        points_df=corrected_points_df,
        config=solver_config,
    )
except Exception as exc:
    st.error(f"Unable to project duty points after applying name corrections: {exc}")
    st.stop()

selected_month_name = MONTH_COLUMN_NAMES[int(month)]
display_points_df = projected_points_df.rename(columns={"Projected": selected_month_name})
display_columns = [
    "Name",
    *[
        column
        for column in display_points_df.columns
        if column not in {"Name", "Duty", "Obligation", selected_month_name, "Total", "Difference"}
    ],
    selected_month_name,
    "Total",
    "Obligation",
]
render_data_editor_with_meta(
    display_points_df[display_columns],
    use_container_width=True,
    disabled=[col for col in display_columns if col != selected_month_name],
)
st.markdown(f"Projected: {display_points_df[selected_month_name].sum()}")
st.markdown(f"Required: {edited_df['Slot 1'].sum() + edited_df['Slot 2'].sum()}")

### Scheduler ###
st.title("Totally Fair Scheduler")
st.caption("Single-process desktop scheduler.")

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
                availability_df=corrected_availability_df,
                points_csv=points_csv,
                month=int(month),
                monthly_obligation=float(monthly_obligation),
                config=solver_config,
                points_df_override=corrected_points_df,
            ).to_dict()
            st.session_state.reserve_results = None
    except Exception as exc:
        st.error(f"Schedule generation failed: {exc}")

if generate_reserves:
    try:
        with st.spinner("Generating primary and reserve schedules..."):
            reserve_response = generate_reserve_schedules_from_inputs(
                availability_df=corrected_availability_df,
                points_csv=points_csv,
                month=int(month),
                monthly_obligation=float(monthly_obligation),
                config=solver_config,
                reserve_rounds=int(reserve_rounds),
                points_df_override=corrected_points_df,
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
