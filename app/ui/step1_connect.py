from __future__ import annotations

import datetime as dt
from collections import defaultdict

import gspread
import pandas as pd
import streamlit as st

from ui.helpers import next_step


def render_step1(gc: gspread.Client, today: dt.datetime) -> None:
    st.header("Step 1: Connecting to Google Sheet")
    st.text(f"Share spreadsheet with {st.secrets.gcp_service_account.client_email}")
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
