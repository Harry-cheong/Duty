# TODO: FIX Issue if 2 clerks have very similar names

from __future__ import annotations

import datetime as dt

import gspread
import streamlit as st
from google.oauth2.service_account import Credentials

from ui.helpers import NUM_STEPS
from ui.step1_connect import render_step1
from ui.step2_slots import render_step2
from ui.step3_availability import render_step3
from ui.step4_points import render_step4
from ui.step5_schedule import render_step5

st.set_page_config(page_title="Totally Fair Scheduler", page_icon="TF", layout="wide")

if "step" not in st.session_state:
    st.session_state.step = 1

if "primary_result" not in st.session_state:
    st.session_state.primary_result = None
if "primary_result_obj" not in st.session_state:
    st.session_state.primary_result_obj = None
if "reserve_results" not in st.session_state:
    st.session_state.reserve_results = None


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


## Introduction
st.title("Totally Fair Scheduler")
st.caption("Follow the steps to generate a new planning schedule")

## Service Account Setup
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

@st.cache_resource
def get_gspread_client() -> gspread.Client:
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=SCOPES,
    )
    return gspread.authorize(creds)

## Connecting to service account
gc = get_gspread_client()

## Progress indicator
st.progress(st.session_state.step / NUM_STEPS)
st.write(f"Step {st.session_state.step} of {NUM_STEPS}")

## Progress Tabs
if st.session_state.step == 1:
    render_step1(gc, today)
elif st.session_state.step == 2:
    render_step2(year, month)
elif st.session_state.step == 3:
    render_step3(month)
elif st.session_state.step == 4:
    render_step4(
        min_gap_days=min_gap_days,
        time_limit_seconds=time_limit_seconds,
        random_seed=random_seed,
        use_random_seed=use_random_seed,
        duty_obligation=duty_obligation,
        reserve_obligation=reserve_obligation,
    )
elif st.session_state.step == 5:
    render_step5(reserve_rounds)
