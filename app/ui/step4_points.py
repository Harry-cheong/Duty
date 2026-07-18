from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from inputs import MONTH_COLUMN_NAMES
from scheduling import SchedulerConfig, generate_planning_table
from ui.helpers import next_step, prev_step, render_dataframe_with_dimensions


def render_step4(
    min_gap_days: int,
    time_limit_seconds: int,
    random_seed: int,
    use_random_seed: bool,
    duty_obligation: float,
    reserve_obligation: float,
) -> None:
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

    # Nav Buttons
    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)
    col2.button(
        "Next →",
        on_click=next_step,
        use_container_width=True,
        disabled=False)
