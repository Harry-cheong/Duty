import pandas as pd
import numpy as np
import json 
import calendar
import re

YEAR = 2026
MONTH = 4

with open("json/april.json", "r") as f:
    data = json.load(f)

# Load all the Clerks in
clerks_df = pd.read_csv("april_clerks.csv")
clerk_hash = {clerk: False for clerk in clerks_df["Name"].tolist()}

# Construct the outline of the DataFrame
_, last_day = calendar.monthrange(YEAR, MONTH)
days = pd.date_range(f"{YEAR}-{MONTH:02d}-01", periods=last_day)

weekdays = {}
for day in days:
    dayidx = day.weekday() % 7
    if dayidx not in weekdays:
        weekdays[dayidx] = []
    weekdays[dayidx].append(day.strftime("%d/%m/%y"))

df = pd.DataFrame(columns=["Name"] + [day.strftime("%d/%m/%y") for day in days] + ["Availability"])
row = 0

def prompt_days():
    confirmdays = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        
    selected_dates = []

    while True:
        format_response = input("> Please amend to the day/date format: ")
        
        # guard clause
        if format_response == "day" or format_response == "date":
            response = input("Selected days: ")
            split_response = response.split()
            temp_selected = []

            # user response specifies day
            if format_response == "day":

                # row_data[split_response[0]-1]=0
                for specified_day in split_response:
                    specified_day = specified_day.lower()[:3]
                    i = confirmdays.index(specified_day)

                    if i in weekdays:
                        temp_selected = temp_selected + weekdays[i]
                        print(f"You selected {confirmdays[i]}")
                    else:
                        print("Invalid weekday")
                
                # confirm user selection
                confirm = input("Do you confirm? (y/n) ")
                if confirm == "y":
                    selected_dates = selected_dates + temp_selected
                else:
                    continue

            # user response is 2 integer: start_date, end_date
            else:
                for i in range(int(split_response[0]), int(split_response[1])+1):
                    temp_selected.append(f"{i:02d}/{MONTH:02d}/{YEAR}")
                selected_dates = selected_dates + temp_selected
                print(f"Selected Days are {temp_selected}")
        elif not format_response:
            break
        else:
            print("Invalid Date Specified")
            
    print(f"> All selected days are {selected_dates}")
    return list(set(selected_dates))

# Transfer clerk responses to the dataframe
for item in data:
    name = item["name"]
    df.loc[row] = [name] + [1 for _ in range(last_day)] + [None]

    if name not in clerk_hash:
        print(f"> {name} Not Found in Clerk List")

    clerk_hash[name] = True

    # unavailable days
    unavailable_days = item["unavailable_dates"]

    for unavailable_obj in unavailable_days:
        date_string = unavailable_obj["dates"]

        # match date range format
        range_pattern = r"(\d+)\s*-\s*(\d+)\s+"
        match_range = re.search(range_pattern, date_string)

        if match_range:
            start = int(match_range.group(1))
            end = int(match_range.group(2))
            for i in range(start-1, end):
                df.iloc[row, i+1] = 0
            continue
        
        # match single day format
        day_pattern = r"(\d+)\s*([A-Za-z]+)"
        match_day = re.search(day_pattern, date_string)

        if match_day:
            df.iloc[row, int(match_day[1])-1]=0
            continue
        
        # prompt if format is unmatched
        print(f"\n{name} indicated {date_string}.")
        selected_days = prompt_days()

        for selected in selected_days:
            df.loc[row, selected] = 0

    df.loc[row, "Availability"] = df.iloc[row, 1:-1].sum()

    # preferences
    # preferences = item["preferred_dates"]
    
    # # if preference given,
    # if preferences:
    #     print(f"\n{name} indicated {preferences}")
    #     selected_days = prompt_days()
    row += 1
        

# Patch missing clerk responses
for clerk, indicated in clerk_hash.items():
    if not indicated:
        row_data = [1 for _ in range(last_day)]
        df.loc[row] = [clerk] + row_data + [last_day]
        row += 1

df.to_csv("april_availability.csv", index=False)