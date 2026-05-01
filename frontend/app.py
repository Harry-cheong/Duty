from __future__ import annotations

import datetime
from typing import Any

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
from st_files_connection import FilesConnection
import os

from inputs import (
    MONTH_COLUMN_NAMES,
    availability_for_solver,
    build_availability_from_input,
    build_slot_config,
    grid_from_normalized_availability,
    slot_labels_from_config,
    load_duty_points,
    load_reserve_points,
)
from scheduler_core import (
    SchedulerConfig,
    generate_reserve_schedules_from_inputs,
    generate_schedule_from_inputs,
    project_duties_preview,
)
from export import (
    generate_summary
)

st.set_page_config(page_title="Totally Fair Scheduler", page_icon="TF", layout="wide")

num_steps = 4
if "step" not in st.session_state:
    st.session_state.step = 1

if "primary_result" not in st.session_state:
    st.session_state.primary_result = None
if "primary_result_obj" not in st.session_state:
    st.session_state.primary_result_obj = None
if "reserve_results" not in st.session_state:
    st.session_state.reserve_results = None


def next_step() -> None:
    st.session_state.step = min(num_steps, st.session_state.step + 1)


def prev_step() -> None:
    st.session_state.step = max(1, st.session_state.step - 1)

def dataframe_from_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)

def table_dimensions_caption(df: pd.DataFrame) -> str:
    return f"{df.shape[1]} columns x {df.shape[0]} rows"


def render_dataframe_with_dimensions(df: pd.DataFrame, hide_index=False) -> None:
    st.dataframe(df, use_container_width=True, hide_index=hide_index)
    st.caption(table_dimensions_caption(df))


def render_data_editor_with_dimensions(df: pd.DataFrame, hide_index=False, **kwargs: Any) -> pd.DataFrame:
    disabled_columns = list(kwargs.pop("disabled", []))
    editor_df = st.data_editor(df, disabled=["_index", *disabled_columns], hide_index=hide_index, **kwargs)
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
                "New Clerk": False,
            }
        )

    return pd.DataFrame(
        rows,
        columns=[
            "Availability Name",
            "Match To DutyPts Name",
            "Disable",
            "New Clerk",
        ],
    )


def normalize_editor_df(df: pd.DataFrame) -> pd.DataFrame:
    normalized_df = pd.DataFrame(df).reset_index(drop=True)
    normalized_df = normalized_df.loc[:, ~normalized_df.columns.astype(str).str.startswith(":")]
    return normalized_df


def has_required_columns(df: pd.DataFrame, columns: set[str]) -> bool:
    return columns.issubset(set(df.columns))


def step_1_is_ready() -> bool:
    personnel_df = st.session_state.get("personnel_df")
    duty_points_df = st.session_state.get("duty_points_df")
    availability_responses_df = st.session_state.get("availability_responses_df")
    return (
        isinstance(personnel_df, pd.DataFrame)
        and isinstance(duty_points_df, pd.DataFrame)
        and isinstance(availability_responses_df, pd.DataFrame)
        and has_required_columns(personnel_df, {"Name"})
        and has_required_columns(duty_points_df, {"Name"})
        and has_required_columns(availability_responses_df, {"Timestamp", "Your Name (Select from list)"})
    )


def step_2_is_ready() -> bool:
    finalised_availability_df = st.session_state.get("finalised_availability_df")
    slots = st.session_state.get("slots")
    return (
        isinstance(finalised_availability_df, pd.DataFrame)
        and isinstance(slots, list)
        and len(slots) > 0
        and has_required_columns(finalised_availability_df, {"No", "Name", *slots})
    )

def step_3_is_ready() -> bool:
    slots = st.session_state.get("slots")
    duty_projection_df = st.session_state.get("edited_duty_projection_df")
    reserve_projection_df = st.session_state.get("edited_reserve_projection_df")
    selected_month_name = st.session_state.get("selected_month_name")
    return (
        isinstance(slots, list)
        and len(slots) > 0
        and isinstance(selected_month_name, str)
        and isinstance(duty_projection_df, pd.DataFrame)
        and isinstance(reserve_projection_df, pd.DataFrame)
        and selected_month_name in duty_projection_df.columns
        and selected_month_name in reserve_projection_df.columns
        and duty_projection_df[selected_month_name].sum() == len(slots)
        and reserve_projection_df[selected_month_name].sum() == len(slots) * reserve_rounds
    )


def solver_config_from_inputs() -> SchedulerConfig:
    return SchedulerConfig(
        min_gap_days=int(min_gap_days),
        time_limit_seconds=int(time_limit_seconds),
        use_random_seed=use_random_seed,
        random_seed=int(random_seed) if use_random_seed else 42,
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
    duty_points_df: pd.DataFrame,
    reserve_points_df: pd.DataFrame,
    availability_review_df: pd.DataFrame,
    points_review_df: pd.DataFrame,
    duty_obligation: float,
    reserve_obligation: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    corrected_availability_df = availability_df.copy()
    corrected_duty_points_df = duty_points_df.copy()
    corrected_reserve_points_df = reserve_points_df.copy()
    points_renames: dict[str, str] = {}
    disabled_availability_names: set[str] = set()
    disabled_points_names: set[str] = set()
    new_clerk_names: set[str] = set()

    for _, row in availability_review_df.iterrows():
        availability_name = str(row.get("Availability Name", "")).strip()
        matched_dutypts_name = str(row.get("Match To DutyPts Name", "")).strip()
        disabled = bool(row.get("Disable", False))
        new_clerk = bool(row.get("New Clerk", False))
        if not availability_name:
            continue
        if disabled:
            disabled_availability_names.add(availability_name)
            continue
        if new_clerk:
            new_clerk_names.add(availability_name)
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

    def _correct_points_df(points_df: pd.DataFrame, monthly_obligation: float) -> pd.DataFrame:
        corrected_points_df = points_df.copy()
        corrected_points_df["Name"] = corrected_points_df["Name"].astype(str).str.strip().map(
            lambda name: points_renames.get(name, name)
        )
        corrected_points_df = corrected_points_df[
            ~corrected_points_df["Name"].isin(disabled_points_names)
        ].copy()

        existing_point_names = set(corrected_points_df["Name"].astype(str).str.strip().tolist())
        historical_columns = [
            column
            for column in corrected_points_df.columns
            if column not in {"Name", "Duty", "Obligation"}
        ]
        new_rows = []
        for name in sorted(new_clerk_names):
            if name in existing_point_names:
                continue
            new_row = {"Name": name, "Duty": 0, "Obligation": float(monthly_obligation)}
            for column in historical_columns:
                new_row[column] = pd.NA
            new_rows.append(new_row)

        if new_rows:
            corrected_points_df = pd.concat(
                [corrected_points_df, pd.DataFrame(new_rows, columns=corrected_points_df.columns)],
                ignore_index=True,
            )
        return corrected_points_df

    corrected_duty_points_df = _correct_points_df(corrected_duty_points_df, duty_obligation)
    corrected_reserve_points_df = _correct_points_df(corrected_reserve_points_df, reserve_obligation)
    return corrected_availability_df, corrected_duty_points_df, corrected_reserve_points_df


def build_points_override_from_projection(
    base_points_df: pd.DataFrame,
    edited_projection_df: pd.DataFrame,
    selected_month_name: str,
) -> pd.DataFrame:
    points_override_df = base_points_df.copy()
    projected_lookup = (
        edited_projection_df[["Name", selected_month_name]]
        .copy()
        .assign(
            Name=lambda df: df["Name"].astype(str).str.strip(),
            Projected=lambda df: pd.to_numeric(df[selected_month_name], errors="coerce").fillna(0).astype(int),
        )
        .set_index("Name")["Projected"]
    )
    points_override_df["Projected"] = points_override_df["Name"].astype(str).str.strip().map(projected_lookup).fillna(0).astype(int)
    return points_override_df



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
        render_dataframe_with_dimensions(schedule_df, hide_index=True)

    with tab_summary:
        render_dataframe_with_dimensions(summary_df, hide_index=True)

    with tab_compliance:
        if compliance_df.empty:
            st.info("No compliance rows returned.")
        else:
            render_dataframe_with_dimensions(compliance_df, hide_index=True)

## Defaults ##
today = datetime.datetime.today()
default_year = today.year if today.month < 12 else today.year + 1
default_month = today.month + 1 if today.month < 12 else 1

with st.sidebar:
    st.header("Inputs")
    year = st.number_input("Year", min_value=2000, max_value=2100, value=default_year, step=1)
    month = st.number_input("Month", min_value=1, max_value=12, value=default_month, step=1)
    duty_obligation = st.number_input("Duty Per Month", value=1.33)
    reserve_obligation = st.number_input("Reverse Per Month", value=3)
    min_gap_days = st.slider("Min Gap Days", min_value=1, max_value=31, value=7)
    time_limit_seconds = st.slider("Solver Time Limit", min_value=1, max_value=120, value=10)
    use_random_seed = st.toggle("Use Fixed Random Seed", value=True)
    random_seed = st.number_input("Random Seed", value=42, step=1, disabled=not use_random_seed)
    reserve_rounds = st.slider("Reserve Rounds", min_value=0, max_value=5, value=2)

# Configure Slots - Highlight Public Holiday Slots
def highlight_special_days(row, weekend_color="#2C3A4A", holiday_color="#244B36"):
    holiday_label = row.get("Holiday", "")
    if pd.notna(holiday_label) and str(holiday_label).strip():
        return [f"background-color: {holiday_color}"] * len(row)
    if row.Day in ["Sat", "Sun"]:
        return [f"background-color: {weekend_color}"] * len(row)
    return [""] * len(row)


## Introduction
st.title("Totally Fair Scheduler")
st.caption("Follow the steps to generate a new planning schedule")

## Progress indicator
st.progress(st.session_state.step / num_steps)
st.write(f"Step {st.session_state.step} of {num_steps}")

## Progress Tabs
if st.session_state.step == 1:

    # Skip Step 1 for testing
    use_test = st.checkbox("Use sample data", value=False)

    if use_test:
        if os.path.exists("../test/may/personnel.csv"):
            # Attempt to load local files
            st.session_state.personnel_df = pd.read_csv("../test/may/personnel.csv")
            st.session_state.duty_points_df = pd.read_csv("../test/may/dutypoints.csv")
            st.session_state.availability_responses_df = pd.read_csv("../test/may/availability_input.csv")
            st.info("Loaded Default Files from Local File Directory")
        
        else:
            try:
                # Attempt to load cloud files
                conn = st.connection("s3", type=FilesConnection)
                st.session_state.personnel_df = conn.read(
                    "s3-duty-planning-defauult-files/personnel.csv",
                    input_format="csv",
                )
                st.session_state.duty_points_df = conn.read(
                    "s3-duty-planning-defauult-files/dutypoints.csv",
                    input_format="csv",
                )
                st.session_state.availability_responses_df = conn.read(
                    "s3-duty-planning-defauult-files/availability_input.csv",
                    input_format="csv",
                )
                
                st.info("Loaded Default Files from S3 Bucket")
            except:
                st.warn("Fail to load defaults. Please the relevant files instead.")

    st.header("Step 1: Upload Inputs")

    st.subheader("Personnel File")
    personnel_uploaded_file = st.file_uploader("Ensure all available duty clerks are on this list", type=[".csv"], key="personnel_upload")
    
    if personnel_uploaded_file:
        st.session_state.personnel_df = pd.read_csv(personnel_uploaded_file)
    
    if "personnel_df" in st.session_state:
        st.session_state.personnel_df = normalize_editor_df(
            st.data_editor(st.session_state.personnel_df, hide_index=True, key="personnel_editor")
        )
        if not has_required_columns(st.session_state.personnel_df, {"Name"}):
            st.error("Personnel file must include a Name column.")
    
    st.subheader("Duty Points File")
    duty_uploaded_file = st.file_uploader("Ensure duty points listed are updated and correct", type=[".csv"], key="duty_upload")
    
    if duty_uploaded_file:
        st.session_state.duty_points_df = pd.read_csv(duty_uploaded_file)
    
    if "duty_points_df" in st.session_state:
        st.session_state.duty_points_df = normalize_editor_df(
            st.data_editor(st.session_state.duty_points_df, hide_index=True, key="duty_points_editor")
        )
        if not has_required_columns(st.session_state.duty_points_df, {"Name"}):
            st.error("Duty points file must include a Name column.")

    st.subheader("Availability Responses File")
    availability_uploaded_file = st.file_uploader(
        "Upload the availability responses export",
        type=[".csv"],
        key="availability_upload",
    )

    if availability_uploaded_file:
        st.session_state.availability_responses_df = pd.read_csv(availability_uploaded_file)

    if "availability_responses_df" in st.session_state:
        st.session_state.availability_responses_df = normalize_editor_df(
            st.data_editor(st.session_state.availability_responses_df, hide_index=True, key="availability_responses_editor")
        )
        if not has_required_columns(st.session_state.availability_responses_df, {"Timestamp", "Your Name (Select from list)"}):
            st.error("Availability responses must include Timestamp and Your Name (Select from list).")

    st.button("Next →", on_click=next_step, use_container_width=True, disabled=not step_1_is_ready())

elif st.session_state.step == 2:
    st.header("Step 2: Configure Slots And Availability")
    st.caption("Set duty points assigned per day. Weekends and Singapore public holidays default to two slots.")

    slot_config_key = f"slot_config_{year}_{month}"
    slot_editor_key = f"slot_editor_{year}_{month}"
    
    if slot_config_key not in st.session_state:
        st.session_state[slot_config_key] = build_slot_config(int(year), int(month))

    styled_df = st.session_state[slot_config_key].style.apply(highlight_special_days, axis=1)
    edited_df = st.data_editor(
        styled_df,
        key=slot_editor_key,
        disabled=["_index", "Date", "Day", "Holiday"],
        hide_index=False,
    )
    edited_df = normalize_editor_df(edited_df)
    st.session_state[slot_config_key] = edited_df
    st.caption("`Holiday` rows are Singapore public holidays (PH).")
    st.caption(table_dimensions_caption(edited_df))

    st.session_state.slots, warning_slots = slot_labels_from_config(edited_df)
    if warning_slots:
        st.markdown(f"No Slot or Invalid Slot Combination on the following days: {' '.join(warning_slots)}")

    st.markdown(f"Assigned Duty Points: {edited_df['Slot 1'].sum() + edited_df['Slot 2'].sum()}")
    
    st.header("Availability And Preferences")
    st.caption("Indicate the availability and preferences of every clerk")
    
    if (
        "personnel_df" in st.session_state
        and "duty_points_df" in st.session_state
        and "availability_responses_df" in st.session_state
        and "slots" in st.session_state
    ):
        slots = st.session_state.slots
        try:
            normalized_availability_df = build_availability_from_input( # Builds availability_df from google sheet responses
                clerks_df=st.session_state.personnel_df,
                responses_df=st.session_state.availability_responses_df,
                slots=slots,
                year=int(year),
                month=int(month),
            )
            expected_columns = ["No", "Name", *slots]
            availability_df = grid_from_normalized_availability(normalized_availability_df, slots) # add no. column to df
            availability_grid_key = (
                f"availability_grid_{year}_{month}_{len(slots)}_"
                f"{len(st.session_state.personnel_df)}_{len(st.session_state.availability_responses_df)}"
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
                availability_df.copy(), # AgGrid mutates the df in-place
                gridOptions=gb.build(),
                allow_unsafe_jscode=True,
                theme='streamlit',
                fit_columns_on_grid_load=False,
                height=min((len(availability_df) + 1) * 35 + 3, 600),
                key=availability_grid_key,
            )
            st.caption(table_dimensions_caption(availability_df))
            st.session_state.finalised_availability_df = normalize_editor_df(
                pd.DataFrame(grid_response["data"]).drop(columns=[":autouniqueid:"], errors="ignore")[expected_columns]
            )
        except Exception as exc:
            st.session_state.pop("finalised_availability_df", None)
            st.error(f"Unable to build availability grid: {exc}")

    else:
        st.error("Please configure personnel, duty points, availability responses and slots!")
    
    confirmed = st.checkbox("I confirm that all the configurations above are accurate", key="confirm_step2")

    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)
    col2.button("Next →", on_click=next_step, use_container_width=True, disabled=not (confirmed and step_2_is_ready()))
    
elif st.session_state.step == 3:
    st.header("Step 3: Duty And Reserve Point Management")
    st.caption("Tabulate duty and reserve points from the last 2 months and project next month's points.")

    if "finalised_availability_df" in st.session_state:
        try:
            duty_points_df = load_duty_points(
                points_df=st.session_state.duty_points_df,
                month=int(month),
                monthly_obligation=float(duty_obligation),
            )
            reserve_points_df = load_reserve_points(
                points_df=st.session_state.duty_points_df,
                month=int(month),
                monthly_obligation=float(reserve_obligation),
            )
            solver_config = solver_config_from_inputs()
            try:
                solver_availability_df = availability_for_solver(st.session_state.finalised_availability_df, st.session_state.slots)
            except Exception as exc:
                st.error(f"Invalid availability grid data: {exc}")
                st.stop()

            st.subheader("Name Review")
            st.caption("Review clerk-name mismatches between Availability and dutypts. Corrections apply only in memory for this app session.")

            availability_names = solver_availability_df["Name"].astype(str).str.strip().tolist()
            points_names = duty_points_df["Name"].astype(str).str.strip().tolist()
            review_signature = (tuple(availability_names), tuple(points_names))
            review_state_prefix = (
                f"name_review_{year}_{month}_"
                f"{len(availability_names)}_{len(points_names)}"
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
                st.subheader("Not In Duty Points")
                if availability_review_df.empty:
                    st.info("No availability-only names.")
                else:
                    availability_review_df = render_data_editor_with_dimensions(
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
                            "New Clerk": st.column_config.CheckboxColumn(
                                "New Clerk", 
                                help="Add the new clerk into the planner"
                            )
                        },
                    )
                    st.session_state[availability_review_key] = availability_review_df

            with points_review_col:
                st.subheader("Not In Availability")
                if points_review_df.empty:
                    st.info("No dutypts-only names.")
                else:
                    points_review_df = render_data_editor_with_dimensions(
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
                if not bool(row.get("Disable", False)) and row.get("Match To DutyPts Name", "")
            ]
            conflicting_new_clerk_matches = sorted(
                {
                    str(row["Availability Name"]).strip()
                    for _, row in availability_review_df.iterrows()
                    if not bool(row.get("Disable", False))
                    and bool(row.get("New Clerk", False))
                    and str(row.get("Match To DutyPts Name", "")).strip()
                }
            )
            if conflicting_new_clerk_matches:
                st.error(
                    "These availability names are marked as New Clerk and also matched to an existing dutypts row: "
                    + ", ".join(conflicting_new_clerk_matches)
                    + ". Choose only one action per name."
                )
                st.stop()

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
                if not bool(row.get("Disable", False)) and row.get("Match To Availability Name", "")
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

            corrected_availability_df, corrected_duty_points_df, corrected_reserve_points_df = apply_name_corrections(
                availability_df=solver_availability_df,
                duty_points_df=duty_points_df,
                reserve_points_df=reserve_points_df,
                availability_review_df=st.session_state[availability_review_key],
                points_review_df=st.session_state[points_review_key],
                duty_obligation=float(duty_obligation),
                reserve_obligation=float(reserve_obligation),
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

            duplicate_corrected_points_names = corrected_duty_points_df["Name"][
                corrected_duty_points_df["Name"].duplicated(keep=False)
            ].unique().tolist()
            if duplicate_corrected_points_names:
                st.error(
                    "Name corrections create duplicate dutypts names: "
                    + ", ".join(sorted(duplicate_corrected_points_names))
                    + ". Adjust the review tables before scheduling."
                )
                st.stop()

            corrected_availability_names = corrected_availability_df["Name"].astype(str).str.strip().tolist()
            corrected_point_names = corrected_duty_points_df["Name"].astype(str).str.strip().tolist()
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
                projected_duty_points_df = project_duties_preview(
                    availability_df=corrected_availability_df,
                    points_df=corrected_duty_points_df,
                    config=solver_config,
                    num_slots=len(st.session_state.slots)
                )
                projected_reserve_points_df = project_duties_preview(
                    availability_df=corrected_availability_df,
                    points_df=corrected_reserve_points_df,
                    config=solver_config,
                    num_slots=len(st.session_state.slots) * reserve_rounds
                )
            except Exception as exc:
                st.error(f"Unable to project points after applying name corrections: {exc}")
                st.stop()

            selected_month_name = MONTH_COLUMN_NAMES[int(month)]
            st.session_state.selected_month_name = selected_month_name
            display_duty_points_df = projected_duty_points_df.rename(columns={"Projected": selected_month_name})
            display_reserve_points_df = projected_reserve_points_df.rename(columns={"Projected": selected_month_name})
            display_duty_columns = [
                "Name",
                *[
                    column
                    for column in display_duty_points_df.columns
                    if column not in {"Name", "Duty", "Obligation", selected_month_name, "Total", "Difference"}
                ],
                selected_month_name,
                "Total",
                "Obligation",
            ]
            display_reserve_columns = [
                "Name",
                *[
                    column
                    for column in display_reserve_points_df.columns
                    if column not in {"Name", "Duty", "Obligation", selected_month_name, "Total", "Difference"}
                ],
                selected_month_name,
                "Total",
                "Obligation",
            ]


            st.subheader("Duty Points")
            edited_duty_points_df = render_data_editor_with_dimensions(
                display_duty_points_df[display_duty_columns],
                use_container_width=True,
                disabled=[col for col in display_duty_columns if col != selected_month_name],
                key="duty_projection_editor",
            )
            st.markdown(f"Projected: {edited_duty_points_df[selected_month_name].sum()}")
            st.markdown(f"Required: {len(st.session_state.slots)}")


            st.subheader("Reserve Points")
            edited_reserve_points_df = render_data_editor_with_dimensions(
                display_reserve_points_df[display_reserve_columns],
                use_container_width=True,
                disabled=[col for col in display_reserve_columns if col != selected_month_name],
                key="reserve_projection_editor",
            )
            st.markdown(f"Projected: {edited_reserve_points_df[selected_month_name].sum()}")
            st.markdown(f"Required: {len(st.session_state.slots) * reserve_rounds}")

            st.session_state.edited_duty_projection_df = edited_duty_points_df
            st.session_state.edited_reserve_projection_df = edited_reserve_points_df
            st.session_state.final_duty_points_df = build_points_override_from_projection(
                base_points_df=corrected_duty_points_df,
                edited_projection_df=edited_duty_points_df,
                selected_month_name=selected_month_name,
            )
            st.session_state.final_reserve_points_df = build_points_override_from_projection(
                base_points_df=corrected_reserve_points_df,
                edited_projection_df=edited_reserve_points_df,
                selected_month_name=selected_month_name,
            )
            st.session_state.corrected_availability_df = corrected_availability_df
            st.session_state.corrected_duty_points_df = corrected_duty_points_df
            st.session_state.corrected_reserve_points_df = corrected_reserve_points_df
            st.session_state.solver_config = solver_config


        except ValueError:
            st.error("Please check the uploaded points file. Required duty/reserve month columns for the last 2 months are missing.")
    else:
        st.error("Please finalise Availability and Preference")
    
    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)
    col2.button(
        "Next →",
        on_click=next_step,
        use_container_width=True,
        disabled=not (
            "corrected_availability_df" in st.session_state
            and "corrected_duty_points_df" in st.session_state
            and "corrected_reserve_points_df" in st.session_state
            and "final_duty_points_df" in st.session_state
            and "final_reserve_points_df" in st.session_state
            and "solver_config" in st.session_state
            and step_3_is_ready()
        ),
    )

elif st.session_state.step == 4:
    st.header("Step 4: Generate Schedules")
    st.caption("Generate the primary schedule and optional reserve schedules from the validated inputs.")

    if (
        "corrected_availability_df" not in st.session_state
        or "corrected_duty_points_df" not in st.session_state
        or "corrected_reserve_points_df" not in st.session_state
        or "final_duty_points_df" not in st.session_state
        or "final_reserve_points_df" not in st.session_state
        or "solver_config" not in st.session_state
    ):
        st.error("Please complete Duty And Reserve Point Management first.")
    else:
        primary_col, reserve_col = st.columns(2)
        generate_primary = primary_col.button("Generate Primary Schedule", use_container_width=True, type="primary")
        generate_reserves = reserve_col.button("Generate With Reserves", use_container_width=True)

        if generate_primary:
            try:
                with st.spinner("Generating primary schedule..."):
                    primary_result = generate_schedule_from_inputs(
                        availability_df=st.session_state.corrected_availability_df,
                        points_df=st.session_state.final_duty_points_df,
                        month=int(month),
                        monthly_obligation=float(duty_obligation),
                        config=st.session_state.solver_config,
                        points_df_override=st.session_state.final_duty_points_df,
                    )
                    st.session_state.primary_result_obj = primary_result
                    st.session_state.primary_result = primary_result.to_dict()
                    st.session_state.reserve_results = None
            except Exception as exc:
                st.session_state.primary_result_obj = None
                st.error(f"Schedule generation failed: {exc}")

        if generate_reserves:
            if st.session_state.get("primary_result_obj") is not None:
                try:
                    with st.spinner("Generating reserve schedules..."):
                        reserve_response = generate_reserve_schedules_from_inputs(
                            availability_df=st.session_state.corrected_availability_df,
                            points_df=st.session_state.final_duty_points_df,
                            month=int(month),
                            monthly_obligation=float(duty_obligation),
                            reserve_monthly_obligation=float(reserve_obligation),
                            config=st.session_state.solver_config,
                            reserve_rounds=int(reserve_rounds),
                            points_df_override=st.session_state.final_duty_points_df,
                            reserve_points_df_override=st.session_state.final_reserve_points_df,
                            primary_result_override=st.session_state.primary_result_obj,
                        )
                        st.session_state.reserve_results = {
                            "primary": reserve_response.primary.to_dict(),
                            "reserves": [reserve.to_dict() for reserve in reserve_response.reserves],
                        }
                        st.session_state.primary_result = st.session_state.reserve_results["primary"]
                except Exception as exc:
                    st.error(f"Schedule generation failed: {exc}")
            else:
                st.error("Please generate primary schedule first")
        if st.session_state.primary_result:
            render_result(st.session_state.primary_result, "Primary Schedule")
            
        if st.session_state.reserve_results and st.session_state.reserve_results["reserves"]:
            st.divider()
            for index, reserve_result in enumerate(st.session_state.reserve_results["reserves"], start=1):
                render_result(reserve_result, f"Reserve {index}")
        
        if st.session_state.primary_result and st.session_state.reserve_results:
            st.subheader("Availability Summary")
            schedule_df = dataframe_from_rows(st.session_state.primary_result["schedule"])
            summary_df = generate_summary(
                st.session_state.finalised_availability_df,
                schedule_df,
                "assigned_clerk",
                "Duty",
            )
            for i, reserve_result in enumerate(
                (st.session_state.reserve_results or {}).get("reserves", []),
                start=1,
            ):
                reserve_schedule = dataframe_from_rows(reserve_result["schedule"])
                summary_df = generate_summary(
                    summary_df,
                    reserve_schedule,
                    "assigned_clerk",
                    f"R{i}",
                )
            render_dataframe_with_dimensions(summary_df, hide_index=True)
        else:
            st.info("Generate a schedule to see results.")

    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)

# @st.cache_data
# def convert_for_download(df):
#     return df.to_csv(index=False).encode("utf-8")

# if st.session_state.primary_result:
#     st.subheader("Our Finalised Plan")
#     schedule_df = dataframe_from_rows(st.session_state.primary_result["schedule"])
#     summarised_df = generate_summary(st.session_state.finalised_availability_df, schedule_df, "assigned_clerk", "Duty")
#     st.dataframe(summarised_df, hide_index=True)

# csv = convert_for_download(edited_availability_df)

# st.download_button(
#     label="Download CSV",
#     data=csv,
#     file_name="data.csv",
#     mime="text/csv",
#     icon=":material/download:",
# )
