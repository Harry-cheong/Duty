import pandas as pd
import json 
import calendar
import re

YEAR = 2026
MONTH = 4
weekday_string = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

with open("json/april.json", "r") as f:
    data = json.load(f)

# Load all the Clerks in
clerks_df = pd.read_csv("april_clerks.csv")
clerk_hash = {clerk: False for clerk in clerks_df["Name"].tolist()}

# Construct the outline of the DataFrame
_, last_day = calendar.monthrange(YEAR, MONTH)
days = pd.date_range(f"{YEAR}-{MONTH:02d}-01", periods=last_day)

duty_slots = [day.strftime("%d/%m/%Y") for day in days]
row = 0

# on weekends, create 2 slots
new_duty_slots = []
for slot in duty_slots:
    dt = pd.to_datetime(slot, format="%d/%m/%Y")
    if dt.weekday() >= 5:
        new_duty_slots.extend([f"{slot} AM", f"{slot} PM"])
    else:
        new_duty_slots.append(slot)

duty_slots = new_duty_slots
    
# sort the days of the month into weekdays
weekdays = {}
for day in days:
    dayidx = day.weekday() % 7
    if dayidx not in weekdays:
        weekdays[dayidx] = []
    weekdays[dayidx].append(day.strftime("%d/%m/%Y"))

df = pd.DataFrame(columns=["Name"] + duty_slots + ["Availability"])

def expand_to_slots(date_string, suffix=None):
    date_obj = pd.to_datetime(date_string, format="%d/%m/%Y")
    if date_obj.weekday() >= 5:
        if suffix in ["AM", "PM"]:
            return [f"{date_string} {suffix}"]
        return [f"{date_string} AM", f"{date_string} PM"]
    return [date_string]

def prompt_days():
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
                i = 0
                while i < len(split_response):
                    specified_day = split_response[i][:3].title()
                    suffix = None
                    if i + 1 < len(split_response) and split_response[i + 1].upper() in ["AM", "PM"]:
                        suffix = split_response[i + 1].upper()
                        i += 1

                    if specified_day in weekday_string:
                        weekday_idx = weekday_string.index(specified_day)
                    else:
                        print(f"{specified_day} is Invalid")
                        i += 1
                        continue

                    for selected_date in weekdays[weekday_idx]:
                        temp_selected.extend(expand_to_slots(selected_date, suffix))

                    if suffix:
                        print(f"You selected {specified_day} {suffix}")
                    else:
                        print(f"You selected {specified_day}")
                    i += 1
                
                # confirm user selection
                confirm = input("Do you confirm? (y/n) ")
                if confirm == "y":
                    selected_dates = selected_dates + temp_selected
                else:
                    continue

            # user response is 2 integer: start_date, end_date
            else:
                if not split_response:
                    print("Invalid Date Specified")
                    continue

                suffix = None
                if split_response[-1].upper() in ["AM", "PM"]:
                    suffix = split_response[-1].upper()
                    split_response = split_response[:-1]

                for token in split_response:
                    if "-" in token:
                        start, end = token.split("-", 1)
                        if not (start.isdigit() and end.isdigit()):
                            print(f"{token} is Invalid")
                            continue

                        for i in range(int(start), int(end) + 1):
                            selected_date = f"{i:02d}/{MONTH:02d}/{YEAR}"
                            temp_selected.extend(expand_to_slots(selected_date, suffix))
                    elif token.isdigit():
                        selected_date = f"{int(token):02d}/{MONTH:02d}/{YEAR}"
                        temp_selected.extend(expand_to_slots(selected_date, suffix))
                    else:
                        print(f"{token} is Invalid")
                selected_dates = selected_dates + temp_selected
                print(f"Selected Days are {temp_selected}")
        elif not format_response:
            break
        else:
            print("Invalid Date Specified")
            
    print(f"> All selected days are {selected_dates}")
    return list(set(selected_dates))

def extract_slots(name, date_string):
    '''Parses date, weekday, and weekend slot expressions into concrete duty slots.'''
    selected_slots = []
    parts = date_string.strip().split()

    if not parts:
        return selected_slots

    suffix = None
    if parts[-1].upper() in ["AM", "PM"]:
        suffix = parts[-1].upper()
        parts = parts[:-1]

    if not parts:
        return selected_slots

    token = " ".join(parts)
    normalised = token.lower()

    if normalised in ["weekends", "all weekends"]:
        for day in days:
            if day.weekday() >= 5:
                selected_slots.extend(expand_to_slots(day.strftime("%d/%m/%Y"), suffix))
        return list(set(selected_slots))

    weekday_token = token[:3].title()
    if weekday_token in weekday_string and len(parts) == 1:
        weekday_idx = weekday_string.index(weekday_token)
        for selected_date in weekdays[weekday_idx]:
            selected_slots.extend(expand_to_slots(selected_date, suffix))
        return list(set(selected_slots))

    range_pattern = r"^(\d+)-(\d+)$"
    match_range = re.match(range_pattern, token)
    if match_range:
        start = int(match_range.group(1))
        end = int(match_range.group(2))
        for i in range(start, end + 1):
            selected_date = f"{i:02d}/{MONTH:02d}/{YEAR}"
            selected_slots.extend(expand_to_slots(selected_date, suffix))
        return list(set(selected_slots))

    day_pattern = r"^\d+$"
    if re.match(day_pattern, token):
        selected_date = f"{int(token):02d}/{MONTH:02d}/{YEAR}"
        return expand_to_slots(selected_date, suffix)

    print(f"{name} indicated {date_string}.")
    return prompt_days()

# Transfer clerk responses to the dataframe
for item in data:
    name = item["name"]
    df.loc[row] = [name] + [1 for _ in range(len(duty_slots))] + [None]

    if name not in clerk_hash:
        print(f"> {name} Not Found in Clerk List")

    clerk_hash[name] = True

    # unavailable days
    unavailable_days = item["unavailable_dates"]

    for unavailable_obj in unavailable_days:
        date_string = unavailable_obj["dates"]
        
        selected_days = extract_slots(name, date_string)
        
        for selected in selected_days:
            if selected in df.columns:
                df.loc[row, selected] = 0
                continue

            selected_date = pd.to_datetime(selected, format="%d/%m/%Y")
            if selected_date.weekday() >= 5:
                for suffix in ["AM", "PM"]:
                    weekend_slot = f"{selected} {suffix}"
                    if weekend_slot in df.columns:
                        df.loc[row, weekend_slot] = 0
            elif selected in df.columns:
                df.loc[row, selected] = 0

    # preferences
    preferences = item["preferred_dates"]
    for preference in preferences:
        preferred_slots = extract_slots(name, preference)

        for selected in preferred_slots:
            if selected in df.columns and df.loc[row, selected] != 0:
                df.loc[row, selected] = 2

    df.loc[row, "Availability"] = (df.iloc[row, 1:-1] > 0).sum()
        
    row += 1

# Patch missing clerk responses
for clerk, indicated in clerk_hash.items():
    if not indicated:
        row_data = [1 for _ in range(len(duty_slots))]
        df.loc[row] = [clerk] + row_data + [len(duty_slots)]
        row += 1

df.to_csv("availability.csv", index=False)
