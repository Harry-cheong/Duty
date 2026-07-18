from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

NUM_STEPS = 5


def next_step() -> None:
    st.session_state.step = min(NUM_STEPS, st.session_state.step + 1)


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


def rgb(red, green, blue):
    return {"red": round(red/255, 3), "green": round(green/255, 3), "blue": round(blue/255, 3)}


def highlight_special_days(row, weekend_color="#2C3A4A", holiday_color="#244B36"):
    holiday_label = row.get("Holiday", "")
    if pd.notna(holiday_label) and str(holiday_label).strip():
        return [f"background-color: {holiday_color}"] * len(row)
    if row.Day in ["Sat", "Sun"]:
        return [f"background-color: {weekend_color}"] * len(row)
    return [""] * len(row)
