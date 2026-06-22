# TODO: FIX Issue if 2 clerks have very similar names

from __future__ import annotations

import calendar
import datetime as dt
from typing import Any

import pandas as pd
import streamlit as st
import json
import gspread
import re
from collections import defaultdict

from inputs import (
    MONTH_COLUMN_NAMES,
    build_availability_from_input,
    build_slot_config,
    slot_labels_from_config,
)
from scheduler_core import (
    SchedulerConfig,
    generate_reserve_schedules_from_inputs,
    generate_schedule_from_inputs,
    generate_planning_table,
)

from export import GSheet

st.set_page_config(page_title="Totally Fair Scheduler", page_icon="TF", layout="wide")

num_steps = 5
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


def solver_config_from_inputs() -> SchedulerConfig:
    return SchedulerConfig(
        min_gap_days=int(min_gap_days),
        time_limit_seconds=int(time_limit_seconds),
        use_random_seed=use_random_seed,
        random_seed=int(random_seed) if use_random_seed else 42,
    )


def normalize_editor_df(df: pd.DataFrame) -> pd.DataFrame:
    normalized_df = pd.DataFrame(df).reset_index(drop=True)
    normalized_df = normalized_df.loc[:, ~normalized_df.columns.astype(str).str.startswith(":")]
    return normalized_df


def render_result(result: dict[str, Any], heading: str) -> None:
    st.subheader(heading)

    metric_columns = st.columns(4)
    metric_columns[0].metric("Mode", result["mode"])
    metric_columns[1].metric("Assigned", result["assigned_total"])
    metric_columns[2].metric("Weekend Imbalance", result["weekend_imbalance"])
    metric_columns[3].metric("Preferred Weekends", result["preferred_weekend_assignments"])

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
today = dt.datetime.today()
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

## Servie Email
with open("../service-key.json") as f:
    service_info = json.load(f)

## Connecting to service account
gc = gspread.service_account(filename="../service-key.json")

## Progress indicator
st.progress(st.session_state.step / num_steps)
st.write(f"Step {st.session_state.step} of {num_steps}")

## Progress Tabs
if st.session_state.step == 1:
    st.header("Step 1: Connecting to Google Sheet")
    st.text(f"Share spreadsheet with {service_info['client_email']}")
    isConnected = False
    attemptConnection = st.button("Connect")

    spreadsheet_id = st.text_input("Spreadsheet ID", value="1zE5Nu6HivEL2PsfGAh116-fSePmLVqWJLUp2RpEX8U8",placeholder="1zE5Nu6HivEL2PsfGAh116-fSePmLVqWJLUp2RpEX8U8")
    
    sh = None
    if attemptConnection:
        with st.spinner("Downloading data..."):
            try:
                st.session_state["sh"] = gc.open_by_key(spreadsheet_id)
                ws = st.session_state.sh.worksheet("Personnel List")
                content = ws.get_all_values()
                headers = [h for h in content[0] if h]
                personnel_dict = defaultdict(list)
                for row in content[1:]:
                    if row[-1]:
                        _day, _month, _year = row[-1].split("/")
                        if dt.datetime(int(_year), int(_month), int(_day)) < today:
                            st.error(f"{row[3]} has ord on {row[-1]}")
                            continue
                    for i, h in enumerate(headers):
                        personnel_dict[h].append(row[i].strip())
                st.session_state.personnel_df = pd.DataFrame(personnel_dict, columns=headers).set_index(headers[0])
            except Exception as e:
                st.error(f"Connection failed: {e}")

        st.session_state.clerk_selection = {
        name: True for name in st.session_state.personnel_df["NAME"]
    }

    if "personnel_df" in st.session_state and "clerk_selection" in st.session_state:
        st.subheader("Select Clerks to Include")

        for _, row in st.session_state.personnel_df.iterrows():
            name = row["NAME"]
            rank_name = row["RANK & NAME"]
            ord_date = row["ORD"] if row["ORD"] else "-"
            st.session_state.clerk_selection[name] = st.checkbox(
                f"{rank_name} (ORD on {ord_date})",
                value=st.session_state.clerk_selection[name],
                key=f"selected_{name.replace(' ', '')}"
            )

        included = [n for n, v in st.session_state.clerk_selection.items() if v]
        excluded = [n for n, v in st.session_state.clerk_selection.items() if not v]
        st.caption(f"{len(included)} included · {len(excluded)} excluded")

        # Filtered df ready to use downstream
        st.session_state.updated_personnel_df = st.session_state.personnel_df[st.session_state.personnel_df["NAME"].isin(included)]
    st.button("Next →", on_click=next_step, use_container_width=True, disabled=not isinstance(st.session_state.get("sh", ""), gspread.Spreadsheet))

elif st.session_state.step == 2:
    # Configure Slots and Availability
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

    st.session_state.slots, st.session_state.slots_as_days, warning_slots = slot_labels_from_config(edited_df)
    if warning_slots:
        st.markdown(f"No Slot or Invalid Slot Combination on the following days: {' '.join(warning_slots)}")

    slot_count = edited_df['Slot 1'].sum() + edited_df['Slot 2'].sum()
    st.markdown(f"Assignable Duty Points: {slot_count}")
    
    # Create a new Master Overview for the month
    new_sheet_title = f"{MONTH_COLUMN_NAMES[month]}26 Master Duty Overview"

    if "sh" not in st.session_state: # Guard: make sure sh is defined
        st.error("No Google Sheet Connected")
        st.stop()

    sh = st.session_state["sh"]

    def create_outline():
        master_ws = sh.add_worksheet(title=new_sheet_title, rows=100, cols=5+len(st.session_state.slots))
        st.session_state.mastersheetf = msf = GSheet(sh, master_ws)

        # Last Cell on the first row
        last_cell = master_ws.cell(1, 5+slot_count)
        last_col_address = last_cell.address.rstrip('1')

        # Col Content
        fixed_cols = ["Duty Personnel Duty", "Total Points", "Clerk", "R1", "R2"]
        master_ws.update(values=[fixed_cols], range_name="A1")
        cols = []

        row_idx = 3
        for clerk in st.session_state.updated_personnel_df["RANK & NAME"]:
            cols.append([clerk] + [
                0,
                f"=SUM(F{row_idx}:{last_col_address + str(row_idx)})",
                f"=ArrayFormula(SUM(IF(F{row_idx}:{last_col_address + str(row_idx)}=\"R\",1,0)))",
                f"=ArrayFormula(SUM(IF(F{row_idx}:{last_col_address + str(row_idx)}=\"R1\",1,0)))",
                ])
            row_idx += 1
        
        # MetaData Columns
        cols.append(["Total", "", f"=SUM(F{row_idx}:{last_col_address + str(row_idx)})"])
        cols.append(["Reserve 1", "", f"=SUM(F{row_idx+1}:{last_col_address + str(row_idx+1)})"])
        cols.append(["Reserve 2", "", f"=SUM(F{row_idx+2}:{last_col_address + str(row_idx+2)})"])

        # Google Sheet Formatting 
        # Note: Rows and Cols are zero-indexed
        # start_row/start_col is inclusive, end_row/end_col is exclusive

        # Duty Personnel, Clerk, R1, R2
        master_ws.update(values=cols, range_name="A3", value_input_option="USER_ENTERED") # Set Values for Duty, Clerk, R1, R2 Col
        msf.set_width(0, 400) # Set Col A to width 400
        msf.set_width(1, 50, end_col=5) # Set Col B:E to width 50
        msf.freeze(cols=5, rows=2) # Freeze Col A:E and Row 1, 2
        msf.set_height(0, 35, end_row=2) # Set Row 1:2 to height 35
        msf.merge_cells(0, 0, end_row=2, end_col=5, merge_type="MERGE_COLUMNS") # Merge A1:E2
        msf.format_cells(start_row=0, end_row=2, horiz_align="CENTER", wrap="WRAP") # Set Rows to wrap-text and center align
        msf.format_cells(start_row=0, start_col=0, end_row=2, end_col=5, fill_colour=msf.rgb(109, 158, 235), bold=True, horiz_align="CENTER", wrap="WRAP") # Format Cell A1:E2

        # Slots
        master_ws.update(range_name="F1", values=[st.session_state.slots_as_days, st.session_state.slots])
        row, col = 1, 5 # Cell F2
        for i, slot in enumerate(st.session_state.slots):
            if "AM" in slot:
                msf.format_cells(start_row=row, 
                                    end_row=row+1,
                                    start_col=col+i,
                                    end_col=col+i+1,
                                    fill_colour=msf.rgb(255, 255, 0), 
                                    horiz_align="CENTER", 
                                    wrap="WRAP")

            elif "PM" in slot:
                msf.format_cells(start_row=row, 
                                    end_row=row+1,
                                    start_col=col+i,
                                    end_col=col+i+1,
                                    fill_colour=msf.rgb(255, 0, 0), 
                                    horiz_align="CENTER", 
                                    wrap="WRAP")
                
            else:
                msf.format_cells(start_row=row, 
                                    end_row=row+1,
                                    start_col=col+i,
                                    end_col=col+i+1,
                                    fill_colour=msf.rgb(52, 168, 83), 
                                    horiz_align="CENTER", 
                                    wrap="WRAP")
        msf.set_width(5, 70, end_col=5+int(slot_count)+1)

        # MetaData Rows
        START_ROW = 5
        meta_row = [
            [f"=SUM({msf.col_letter(START_ROW+i)}3:{msf.col_letter(START_ROW+i)}{row_idx-1})" for i in range(len(st.session_state.slots))], 
            [f"=ArrayFormula(SUM(IF({msf.col_letter(START_ROW+i)}3:{msf.col_letter(START_ROW+i)}{row_idx-1}=\"R\",1,0)))" for i in range(len(st.session_state.slots))], 
            [f"=ArrayFormula(SUM(IF({msf.col_letter(START_ROW+i)}3:{msf.col_letter(START_ROW+i)}{row_idx-1}=\"R2\",1,0)))" for i in range(len(st.session_state.slots))]
        ]
        master_ws.update(range_name=f"F{row_idx}", values=meta_row, value_input_option="USER_ENTERED")

        msf.execute_req()

    def does_sheet_exists(ws):
        existing_titles = [ws.title for ws in sh.worksheets()]
        if new_sheet_title in existing_titles:
            return True
        return False
    
    if does_sheet_exists(new_sheet_title):
        st.error(f"{new_sheet_title} already exists. You cannot create another one")
    else:
        st.button(f"Create {new_sheet_title}", on_click=create_outline, type="primary", use_container_width=True)

    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)
    col2.button("Next →", on_click=next_step, use_container_width=True)

elif st.session_state.step == 3:
    st.header("Step 3. Availability And Preferences")

    # Preserve values explicitly at the top of the step
    if "prompt_df" not in st.session_state:
        # Step 1
        with st.container(border=True):
            st.markdown("**Step 1 — User Input**")
            st.text_area(
                label="Copy and paste responses here",
                key="response"
            )

        # Step 2 — only shows when Step 1 is filled
        if "response" in st.session_state and st.session_state.response:
            with st.container(border=True):
                st.markdown("**Step 2 — Generate Prompt**")
                st.caption("Copy this prompt into ChatGPT or Claude.")
                st.code(f"""
        Here is a list of clerks {st.session_state.updated_personnel_df["RANK & NAME"].tolist()}.
        Here is the text with all the responses {st.session_state.response}

        I want you to find and match their responses with the clerks in the list based on their names. Use the rank and name stated in the clerks_list if there is a conflict. Ignore the month.

        For each entry,
        - List down all the dates in a range
        - Use "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Weekdays", "Weekends"

        The result should be in the below format:
        [
            [Clerk_name, unavailable dates, preferences] ["PTE Cheong Jun Kai Harry", [1, 2, 4, 5], ["Weekends"]],
            ...
        ]

        Answer only the entries.
        """, language=None, height=100)
        
            # Step 3 - collect LLM Response
            with st.container(border=True):
                st.markdown("**Step 3 - Input Response**")
                
                st.text_area(
                    label="Copy and paste LLM response here",
                    key="prompt_response"
                )
        
        if "prompt_response" in st.session_state and st.session_state.prompt_response:
            try:
                prompt_json = json.loads(st.session_state.prompt_response)
            except Exception as e:
                st.error("LLM Response is Invalid")
                st.error(e)
                st.stop()
            
            st.session_state.prompt_df = pd.DataFrame(
                [[entry[0], entry[1], json.dumps([str(p) for p in entry[2]])] for entry in list(prompt_json)],
                columns=["RANK & NAME", "Unavailable Dates", "Preferrences"]
            )
    if "prompt_df" in st.session_state:
        st.success("Successfully Loaded!")
        render_dataframe_with_dimensions(st.session_state.prompt_df)
        try:
            st.session_state.availability_df = build_availability_from_input(
                st.session_state.updated_personnel_df, 
                st.session_state.prompt_df, 
                st.session_state.slots,
                st.session_state.slots_as_days,
                )
            # render_dataframe_with_dimensions(st.session_state.availability_df)
        except Exception as err:
            st.error(err)
    
    if "availability_df" in st.session_state:
        if "mastersheetf" not in st.session_state:
            st.session_state.mastersheetf = GSheet(st.session_state.sh, st.session_state.sh.worksheet(f"{MONTH_COLUMN_NAMES[month]}26 Master Duty Overview"))

        # Colours the corresponding cells in the google sheet
        def update_availability():
            df = st.session_state.availability_df
            row_idx_no = 0
            msf = st.session_state.mastersheetf
            
            # Base Row and Base Col in Google Sheet 
            # Reference Point is F3
            base_row = 2
            base_col = 5

            for _, row in df.iterrows():
                for col_idx, col in enumerate(df.columns):
                    # st.session_state.results.append((row_idx_no, col_idx, int(row[col])))

                    # Unavailable slots
                    if int(row[col]) == 0:
                        msf.format_cells(start_row=base_row+row_idx_no, 
                                        end_row=base_row+row_idx_no+1,
                                        start_col=base_col+col_idx,
                                        end_col=base_col+col_idx+1,
                                        fill_colour=msf.rgb(0, 0, 0),
                                        horiz_align="RIGHT")
                    
                    elif int(row[col]) == 2:
                        msf.format_cells(start_row=base_row+row_idx_no, 
                                        end_row=base_row+row_idx_no+1,
                                        start_col=base_col+col_idx,
                                        end_col=base_col+col_idx+1,
                                        fill_colour=msf.rgb(255, 0, 255),
                                        horiz_align="RIGHT")
                    
                row_idx_no += 1
            msf.execute_req()

        # st.info(st.session_state.mastersheetf.batch_requests)
        st.button("Update Google Sheet", on_click=update_availability, type="primary", use_container_width=True)

    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)
    col2.button("Next →", on_click=next_step, use_container_width=True, disabled="availability_df" not in st.session_state)
    
elif st.session_state.step == 4:
    st.header("Step 4: Duty And Reserve Point Management")
    st.caption("Tabulate duty and reserve points from the last 2 months and project next month's points.")

    def retrieve_data():
        # Retrieve Duty Points
        months = [MONTH_COLUMN_NAMES[month_int] for month_int in range(7-1, 7-3, -1)]
        
        # Total Duty Points
        st.session_state.duty_points_df = pd.DataFrame(columns=["NAME"] + [col for m in months for col in [f"{m} Duty", f"{m} R1", f"{m} R2"]]
)
        st.session_state.duty_points_df["NAME"] = st.session_state.updated_personnel_df["NAME"]
        st.session_state.duty_points_df["RANK & NAME"] = st.session_state.updated_personnel_df["RANK & NAME"]
        st.session_state.duty_points_df = st.session_state.duty_points_df.set_index("NAME")

        for _month in months:
            month_sheet = f"{_month}26 Master Duty Overview"
            selected_sheet = st.session_state.sh.worksheet(month_sheet)

            # Extract points from the relevant months
            selected_sheet_dict = {}
            content = selected_sheet.get("A3:E100")
            for c in content:
                if len(c) < 4:
                    continue
                clerk, _, duty, r1, r2 = c

                selected_sheet_dict[clerk] = (duty, r1, r2)

            for name in st.session_state.updated_personnel_df["NAME"]:
                ptn = re.compile(f"{re.escape(name)}\s*$") # matches anything ending in the name, regardless of what rank prefix
                
                # Find closest key
                closest_name_key = None
                for k in selected_sheet_dict.keys():
                    if re.search(ptn, k):
                        closest_name_key = k
                        break
                
                # if closest_name_key:
                if closest_name_key:
                    duty, r1, r2 = selected_sheet_dict[closest_name_key]
                    st.session_state.duty_points_df.loc[name, f"{_month} Duty"] = float(duty)
                    st.session_state.duty_points_df.loc[name, f"{_month} R1"] = float(r1)
                    st.session_state.duty_points_df.loc[name, f"{_month} R2"] = float(r2)
                else:
                    print(f"\nNo matching value found for {name}\n")
        st.session_state.duty_points_df = st.session_state.duty_points_df.set_index("RANK & NAME") # ensure consistent index with availability_df

    if "availability_df" in st.session_state:
        if "duty_points_df" not in st.session_state:
            with st.spinner("Retrieving Historical Data"):
                retrieve_data()

        st.button(label="Reload Data", on_click=retrieve_data)
        st.caption("Historical Duty and Reserve Points")

        # Historical Data Container
        with st.expander("Show Historical Points"):
            render_dataframe_with_dimensions(st.session_state.duty_points_df)
        
        # Project Duty Points
        st.session_state.solver_config = SchedulerConfig(min_gap_days, time_limit_seconds, random_seed, use_random_seed)
        st.session_state.planning_table, preview_df, projected_df = generate_planning_table(st.session_state.availability_df, st.session_state.duty_points_df, st.session_state.solver_config, duty_obligation, reserve_obligation, len(st.session_state.slots))

        # Summed Historical Data Container
        with st.expander("Show Total & Obligated Points"):
            st.caption("H. Duty = Historical Duty Points")
            st.caption("O. Duty = Obligated Duty Points")
            render_dataframe_with_dimensions(preview_df)
        
        st.subheader("Suggested Duty/Reserve")
        render_dataframe_with_dimensions(projected_df)

    
    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)
    col2.button(
        "Next →",
        on_click=next_step,
        use_container_width=True,
        disabled=False)

elif st.session_state.step == 5:
    st.header("Step 5: Generate Schedules")
    st.caption("Generate the primary schedule and optional reserve schedules from the validated inputs.")

    if (
        "updated_personnel_df" not in st.session_state
        or "availability_df" not in st.session_state
        or "planning_table" not in st.session_state
    ):
        st.error("Please complete Duty And Reserve Point Management first.")
    else:
        def generate_schedule():
            primary_result, st.session_state.duty_planning_table = generate_schedule_from_inputs(
                planning_table=st.session_state.planning_table,
                config=st.session_state.solver_config,
                slots=st.session_state.slots,
            )
            st.session_state.primary_result = primary_result.to_dict()

            reserve_response = generate_reserve_schedules_from_inputs(
                planning_table=st.session_state.duty_planning_table,
                slots=st.session_state.slots,
                config=st.session_state.solver_config,
                reserve_rounds=int(reserve_rounds),
            )
            st.session_state.reserve_results = {
                "reserves": [reserve.to_dict() for reserve in reserve_response.reserves],
            }
        
        st.button(label="Regenerate Schedule", on_click=generate_schedule)
        if not st.session_state.primary_result or not st.session_state.reserve_results:
            try:
                with st.spinner("Generating Schedule"):
                    generate_schedule()
            except Exception as e:
                st.error(f"Schedule Generation Fail: {e}")

        # Display Duty Planning Results
        if st.session_state.primary_result:
            with st.expander("Duty Planning"):
                render_result(st.session_state.primary_result, "Primary Schedule")
            
        # Display Reserve(s) Planning Results
        if st.session_state.reserve_results and st.session_state.reserve_results["reserves"]:
            for index, reserve_result in enumerate(st.session_state.reserve_results["reserves"], start=1):
                with st.expander(f"Reserve {index} Planning"):
                    render_result(reserve_result, f"Reserve {index}")
        
        
        if st.session_state.primary_result and st.session_state.reserve_results:
            st.subheader("Overall Duty Plan")
            st.session_state.schedule_df = dataframe_from_rows(st.session_state.primary_result["schedule"])
            st.session_state.schedule_df = st.session_state.schedule_df[["date", "assigned_clerk"]]

            st.session_state.schedule_df = st.session_state.schedule_df.rename(
                columns={
                    "date": "Slot",
                    "assigned_clerk": "Duty Clerk",
                }
            ).set_index("Slot")

            for index, reserve in enumerate(st.session_state.reserve_results["reserves"]):
                st.session_state.schedule_df[f"R{index+1}"] = ""

                for reservecol in reserve["schedule"]:
                    st.session_state.schedule_df.loc[reservecol["date"], f"R{index+1}"] = reservecol["assigned_clerk"]
            st.dataframe(st.session_state.schedule_df)

    def update_schedule():
        base_row, base_col = 3, 5 # row is 1-indexed and col is 0-indexed
        batch_values = []

        for slot, row in st.session_state.schedule_df.iterrows():
            dcol = st.session_state.slots.index(slot)
            
            # Update Duty Clerk on Google Sheet
            duty_clerk = row["Duty Clerk"]
            drow = st.session_state.planning_table.index.get_loc(duty_clerk)
            batch_values.append({"range": f"{st.session_state.mastersheetf.col_letter(base_col+dcol)}{base_row+drow}",
                                "values": [[1]]})

            # Update R1 on Google Sheet
            duty_clerk = row["R1"]
            drow = st.session_state.planning_table.index.get_loc(duty_clerk)
            batch_values.append({"range": f"{st.session_state.mastersheetf.col_letter(base_col+dcol)}{base_row+drow}",
                                "values": [["R"]]})
            
            # Update R1 on Google Sheet
            duty_clerk = row["R2"]
            drow = st.session_state.planning_table.index.get_loc(duty_clerk)
            batch_values.append({"range": f"{st.session_state.mastersheetf.col_letter(base_col+dcol)}{base_row+drow}",
                                "values": [["R2"]]})



            

        
        st.session_state.mastersheetf.ws.batch_update(batch_values)
            
            
            
            

    st.button("Update Google Sheet", on_click=update_schedule, disabled="schedule_df" not in st.session_state, type="primary", use_container_width=True)
    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)
    col2.button(
        "Next →",
        on_click=next_step,
        use_container_width=True,
        disabled=not bool(st.session_state.primary_result),
    )