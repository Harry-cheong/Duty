from __future__ import annotations

import streamlit as st

from scheduling import generate_reserve_schedules_from_inputs, generate_schedule_from_inputs
from ui.helpers import dataframe_from_rows, prev_step, render_result, rgb


def render_step5(reserve_rounds: int) -> None:
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
        msf = st.session_state.mastersheetf

        for slot, row in st.session_state.schedule_df.iterrows():
            dcol = st.session_state.slots.index(slot)
            
            # Update Duty Clerk on Google Sheet
            duty_clerk = row["Duty Clerk"]
            drow = st.session_state.planning_table.index.get_loc(duty_clerk)
            batch_values.append({"range": f"{st.session_state.mastersheetf.col_letter(base_col+dcol)}{base_row+drow}",
                                "values": [[1]]})
            msf.format_cells(
                start_row=base_row+drow-1,
                end_row=base_row+drow,
                start_col=base_col+dcol,
                end_col=base_col+dcol+1,
                fill_colour=rgb(183, 225, 205), 
                horiz_align="RIGHT")

            # Update R1 on Google Sheet
            duty_clerk = row["R1"]
            drow = st.session_state.planning_table.index.get_loc(duty_clerk)
            batch_values.append({"range": f"{st.session_state.mastersheetf.col_letter(base_col+dcol)}{base_row+drow}",
                                "values": [["R"]]})
            msf.format_cells(
                start_row=base_row+drow-1,
                end_row=base_row+drow,
                start_col=base_col+dcol,
                end_col=base_col+dcol+1,
                fill_colour=rgb(249, 203, 156), 
                horiz_align="LEFT")
            
            # Update R1 on Google Sheet
            duty_clerk = row["R2"]
            drow = st.session_state.planning_table.index.get_loc(duty_clerk)
            batch_values.append({"range": f"{st.session_state.mastersheetf.col_letter(base_col+dcol)}{base_row+drow}",
                                "values": [["R2"]]})
            msf.format_cells(
                start_row=base_row+drow-1,
                end_row=base_row+drow,
                start_col=base_col+dcol,
                end_col=base_col+dcol+1,
                fill_colour=rgb(180, 167, 214), 
                horiz_align="LEFT")
            
            msf.execute_req()
        st.session_state.mastersheetf.ws.batch_update(batch_values)

    st.button("Update Schedule", on_click=update_schedule, disabled="schedule_df" not in st.session_state, type="primary", use_container_width=True)
    st.divider()

    # Nav Buttons
    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)
