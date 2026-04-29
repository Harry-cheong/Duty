import pandas as pd

def generate_summary(availability_df: pd.DataFrame, schedule: pd.DataFrame, schedule_name_key:str, label:str) -> pd.DataFrame:
    summarised_df = availability_df.copy().reset_index(drop=True)
    summarised_df = summarised_df.loc[:, ~summarised_df.columns.astype(str).str.startswith(":")]

    schedule_dates = {
        str(row["date"]).strip()
        for _, row in schedule.iterrows()
        if "date" in row and pd.notna(row["date"])
    }
    for date in schedule_dates:
        if date in summarised_df.columns:
            summarised_df[date] = summarised_df[date].astype("string")

    for _, row in schedule.iterrows():
        name = str(row[schedule_name_key]).strip()
        date = str(row["date"]).strip()
        if date not in summarised_df.columns:
            raise ValueError(f"{date} not found")
        summarised_df.loc[summarised_df["Name"].astype(str).str.strip() == name, date] = label

    return summarised_df
