from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from export import GSheet
from inputs import MONTH_COLUMN_NAMES, build_availability_from_input
from ui.helpers import next_step, prev_step, render_dataframe_with_dimensions, rgb


def render_step3(month: int) -> None:
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
                [[entry[0], json.dumps(entry[1]), json.dumps([str(p) for p in entry[2]])] for entry in list(prompt_json)],
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
                                        fill_colour=rgb(0, 0, 0),
                                        horiz_align="RIGHT")
                    
                    elif int(row[col]) == 2:
                        msf.format_cells(start_row=base_row+row_idx_no, 
                                        end_row=base_row+row_idx_no+1,
                                        start_col=base_col+col_idx,
                                        end_col=base_col+col_idx+1,
                                        fill_colour=rgb(255, 0, 255),
                                        horiz_align="RIGHT")
                    
                row_idx_no += 1
            msf.execute_req()

        st.button("Update Google Sheet", on_click=update_availability, type="primary", use_container_width=True)

    # Nav Buttons
    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)
    col2.button("Next →", on_click=next_step, use_container_width=True, disabled="availability_df" not in st.session_state)
