from __future__ import annotations

import streamlit as st

from export import GSheet
from inputs import MONTH_COLUMN_NAMES, build_slot_config, master_overview_title, send_out_title, slot_labels_from_config
from ui.helpers import (
    highlight_special_days,
    next_step,
    normalize_editor_df,
    prev_step,
    rgb,
    table_dimensions_caption,
)


def render_step2(year: int, month: int) -> None:
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
    new_sheet_title = master_overview_title(year, month)
    send_sheet_title = send_out_title(year, month)

    if "sh" not in st.session_state: # Guard: make sure sh is defined
        st.error("No Google Sheet Connected")
        st.stop()

    sh = st.session_state["sh"]

    def create_outline():
        master_ws = sh.add_worksheet(title=new_sheet_title, rows=100, cols=5+len(st.session_state.slots))
        st.session_state.mastersheetf = msf = GSheet(sh, master_ws)

        ## Duty Sheet Overview
        # Last Cell on the first row
        last_cell = master_ws.cell(1, 5+slot_count)
        last_col_address = last_cell.address.rstrip('1')

        # Col Content
        fixed_cols = ["Duty Personnel Duty", "Total Points", "Clerk", "R1", "R2"]
        master_ws.update(values=[fixed_cols], range_name="A1")
        cols = []

        row_idx = 3
        for i, row in st.session_state.updated_personnel_df.iterrows():
            clerk = row["RANK & NAME"]
            cols.append([clerk] + [
                0,
                f"=SUM(F{row_idx}:{last_col_address + str(row_idx)})",
                f"=ArrayFormula(SUM(IF(F{row_idx}:{last_col_address + str(row_idx)}=\"R\",1,0)))",
                f"=ArrayFormula(SUM(IF(F{row_idx}:{last_col_address + str(row_idx)}=\"R2\",1,0)))",
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
        msf.format_cells(start_row=0, start_col=0, end_row=2, end_col=5, fill_colour=rgb(109, 158, 235), bold=True, horiz_align="CENTER", wrap="WRAP") # Format Cell A1:E2

        # Slots
        master_ws.update(range_name="F1", values=[st.session_state.slots_as_days, st.session_state.slots])
        row, col = 1, 5 # Cell F2
        for i, slot in enumerate(st.session_state.slots):
            if "AM" in slot:
                msf.format_cells(start_row=row, 
                                    end_row=row+1,
                                    start_col=col+i,
                                    end_col=col+i+1,
                                    fill_colour=rgb(255, 255, 0), 
                                    horiz_align="CENTER", 
                                    wrap="WRAP")

            elif "PM" in slot:
                msf.format_cells(start_row=row, 
                                    end_row=row+1,
                                    start_col=col+i,
                                    end_col=col+i+1,
                                    fill_colour=rgb(255, 0, 0), 
                                    horiz_align="CENTER", 
                                    wrap="WRAP")
                
            else:
                msf.format_cells(start_row=row, 
                                    end_row=row+1,
                                    start_col=col+i,
                                    end_col=col+i+1,
                                    fill_colour=rgb(52, 168, 83), 
                                    horiz_align="CENTER", 
                                    wrap="WRAP")
        msf.set_width(5, 70, end_col=5+int(slot_count)+1)

        # Meta Rows
        START_COL = 5
        clerk_num = len(st.session_state.updated_personnel_df.index)
        slot_num = len(st.session_state.slots)
        meta_row = [
            # Total, Reserve 1, Reserve 2 Count Rows
            [f"=SUM({msf.col_letter(START_COL+i)}3:{msf.col_letter(START_COL+i)}{row_idx-1})" for i in range(slot_num)], 
            [f"=ArrayFormula(SUM(IF({msf.col_letter(START_COL+i)}3:{msf.col_letter(START_COL+i)}{row_idx-1}=\"R\",1,0)))" for i in range(slot_num)], 
            [f"=ArrayFormula(SUM(IF({msf.col_letter(START_COL+i)}3:{msf.col_letter(START_COL+i)}{row_idx-1}=\"R2\",1,0)))" for i in range(slot_num)],

            # Clerk, Reserve 1, Reserve 2 Name Rows
            [f"=IFERROR(INDEX($A$3:$A${2+clerk_num}, MATCH(1, INDEX($F$3:${msf.col_letter(START_COL+slot_num-1)}${2+clerk_num}, 0, MATCH({msf.col_letter(START_COL+i)}2, $F$2:${msf.col_letter(START_COL+slot_num-1)}$2, 0)), 0)), INDEX($A$3:$A${3+clerk_num-1}, MATCH(1, INDEX($F$3:${msf.col_letter(START_COL+slot_num-1)}${2+clerk_num}, 0, MATCH({msf.col_letter(START_COL+i)}2&\" AM\", $F$2:${msf.col_letter(START_COL+slot_num-1)}$2, 0)), 0)))" for i in range(slot_num)],
            [f"=INDEX($A$3:$A${2+clerk_num}, MATCH(\"R\", INDEX($F$3:${msf.col_letter(START_COL+slot_num-1)}${2+clerk_num}, 0, MATCH({msf.col_letter(START_COL+i)}2, $F$2:${msf.col_letter(START_COL+slot_num-1)}$2, 0)), 0))"for i in range(slot_num)],
            [f"=INDEX($A$3:$A${2+clerk_num}, MATCH(\"R2\", INDEX($F$3:${msf.col_letter(START_COL+slot_num-1)}${2+clerk_num}, 0, MATCH({msf.col_letter(START_COL+i)}2, $F$2:${msf.col_letter(START_COL+slot_num-1)}$2, 0)), 0))"for i in range(slot_num)],
        ]
        master_ws.update(range_name=f"F{3+clerk_num}", values=meta_row, value_input_option="USER_ENTERED")

        msf.execute_req()
    
    def create_send_out():
        send_ws = sh.add_worksheet(title=send_sheet_title, rows=100, cols=11)
        clerk_num = len(st.session_state.updated_personnel_df.index)
        fixed_row = ["DAY", "DATE", "CLERK", "HP NO.", "BRANCH", "STANDBY", "HP NO.", "BRANCH", "STANDBY", "HP NO.", "BRANCH"]
        slot_num = len(st.session_state.slots)
        ssf = GSheet(sh, send_ws)

        rows = [[f"{MONTH_COLUMN_NAMES[month]} {year} DUTY CLERK FORECAST"], fixed_row]

        for i in range(len(st.session_state.slots)):
            rows.append([
                st.session_state.slots_as_days[i], # Day
                st.session_state.slots[i], # Slot
                "", # Duty Clerk
                f"=VLOOKUP($C{i+3},'Personnel List'!D:F,2,0)", # Contact No.
                f"=VLOOKUP($C{i+3},'Personnel List'!D:F,3,0)", # Branch
                "", # R1
                f"=VLOOKUP($F{i+3},'Personnel List'!D:F,2,0)", # Contact No.
                f"=VLOOKUP($F{i+3},'Personnel List'!D:F,3,0)", # Branch
                "", # R2
                f"=VLOOKUP($I{i+3},'Personnel List'!D:F,2,0)", # Contact No.
                f"=VLOOKUP($i{i+3},'Personnel List'!D:F,3,0)" # Branch
            ])
        
        # Format
        # Set title
        ssf.format_cells(
            start_row=0,
            end_row=1,
            fill_colour=rgb(204, 204, 204),
            horiz_align="CENTER",
            bold=True
        )
        ssf.merge_cells(start_row=0, end_row=1, start_col=0, end_col=11)
        ssf.format_cells(
            start_row=1,
            end_row=2,
            fill_colour=rgb(204, 204, 204),
            horiz_align="CENTER", 
            bold=True,
        ) # Set Row 2

        # Duty Clerk
        ssf.set_width(0, 120, end_col=2) # Set Col A:B to width 120
        ssf.format_cells(
            start_row=2,
            end_row=slot_num+2,
            start_col=0,
            end_col=2,
            fill_colour=rgb(204, 204, 204), 
            horiz_align="CENTER", 
            bold=True,
        ) # Format Col A:B
        ssf.set_width(2, 380, end_col=3) # Set Col C to width 380
        ssf.format_cells(
            start_row=2,
            end_row=slot_num+2,
            start_col=2,
            end_col=3,
            fill_colour=rgb(183, 225, 205), 
            horiz_align="CENTER", 
        ) # Format Col C
        ssf.set_width(3, 120, end_col=5) # Set Col D:E to width 120
        ssf.format_cells(
            start_row=2,
            end_row=slot_num+2,
            start_col=3,
            end_col=5,
            horiz_align="CENTER", 
        ) # Format Col D:E

        # Reserve 1
        ssf.set_width(5, 380, end_col=6) # Set Col F to width 380
        ssf.format_cells(
            start_row=2,
            end_row=slot_num+2,
            start_col=5,
            end_col=6,
            fill_colour=rgb(249, 203, 156), 
            horiz_align="CENTER", 
        ) # Format Col F
        ssf.set_width(6, 120, end_col=8) # Set Col G:H to width 120
        ssf.format_cells(
            start_row=2,
            end_row=slot_num+2,
            start_col=6,
            end_col=8,
            horiz_align="CENTER", 
        ) # Format Col G:H

        # Reserve 2
        ssf.set_width(8, 380, end_col=9) # Set Col I to width 380    
        ssf.format_cells(
            start_row=2,
            end_row=slot_num+2,
            start_col=8,
            end_col=9,
            fill_colour=rgb(180, 167, 214), 
            horiz_align="CENTER", 
        ) # Format Col F
        ssf.set_width(9, 120, end_col=11) # Set Col G:H to width 120
        ssf.format_cells(
            start_row=2,
            end_row=slot_num+2,
            start_col=9,
            end_col=11,
            horiz_align="CENTER", 
        ) # Format Col G:H  

        # Add borders
        ssf.set_border(
            start_row=0,
            end_row=slot_num+2,
            start_col=0,
            end_col=11,
        )

        rows[2][2] = f"=TRANSPOSE('{new_sheet_title}'!F{clerk_num+6}:{ssf.col_letter(5+slot_num)}{len(st.session_state.updated_personnel_df.index)+6})"
        rows[2][5] = f"=TRANSPOSE('{new_sheet_title}'!F{clerk_num+7}:{ssf.col_letter(5+slot_num)}{len(st.session_state.updated_personnel_df.index)+7})"
        rows[2][8] = f"=TRANSPOSE('{new_sheet_title}'!F{clerk_num+8}:{ssf.col_letter(5+slot_num)}{len(st.session_state.updated_personnel_df.index)+8})"

        send_ws.update(range_name=f"A1", values=rows, value_input_option="USER_ENTERED")
        ssf.execute_req()

    def does_sheet_exists(ws):
        existing_titles = [ws.title for ws in sh.worksheets()]
        if ws in existing_titles:
            return True
        return False
    
    if does_sheet_exists(send_sheet_title):
        st.error(f"{send_sheet_title} already exists. You cannot create another one")
    else:
        st.button(f"Create {send_sheet_title}", on_click=create_send_out, use_container_width=True)
    
    if does_sheet_exists(new_sheet_title):
        st.error(f"{new_sheet_title} already exists. You cannot create another one")
    else:
        st.button(f"Create {new_sheet_title}", on_click=create_outline, type="primary", use_container_width=True)
    
    # Nav Buttons
    col1, col2 = st.columns(2)
    col1.button("← Back", on_click=prev_step, use_container_width=True)
    col2.button("Next →", on_click=next_step, use_container_width=True)
