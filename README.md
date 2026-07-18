# Totally Fair Scheduler

Handoff guide for running, operating, and extending the duty/reserve clerk scheduler.

A single-user **Streamlit** app builds a fair monthly **duty** schedule plus **reserve** (R1/R2) schedules, then writes results into Google Sheets. The solver runs **in-process** with Google OR-Tools CP-SAT — there is no separate API or backend service.

## Table of contents

1. [Quick start](#quick-start)
2. [What the system does](#what-the-system-does)
3. [Prerequisites](#prerequisites)
4. [Google Cloud & secrets](#google-cloud--secrets)
5. [Google Sheet layout](#google-sheet-layout)
6. [Operator workflow (5 steps)](#operator-workflow-5-steps)
7. [Sidebar settings](#sidebar-settings)
8. [Availability encoding & preferences](#availability-encoding--preferences)
9. [How scheduling works](#how-scheduling-works)

## Quick start

```bash
git clone <repo-url>
cd "Totally Fair Scheduler/app"
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Configure secrets (see Google Cloud & secrets), then:
streamlit run app.py
```

Open the URL Streamlit prints (usually `http://localhost:8501`).

Always run from the `app/` directory so imports like `from scheduling import …` and `from ui…` resolve. Do **not** run `streamlit run app/app.py` from the repo root unless you also adjust `PYTHONPATH`.

Optional theme lives in `app/.streamlit/config.toml` (dark theme, green accent). That folder is gitignored — if missing locally, Streamlit still runs with defaults.

Before Step 1 works, you must:

1. Put a GCP service account into `app/.streamlit/secrets.toml`
2. Share the target spreadsheet with that service account as **Editor**

## What the system does

End-to-end monthly planning:

1. Load clerks from a `Personnel List` worksheet
2. Define assignable **slots** for the month (weekdays usually 1 slot; weekends / Singapore public holidays default to AM+PM)
3. Build an **availability matrix** from clerk responses (optionally normalized with an LLM)
4. Pull recent **duty / R1 / R2 point totals** from prior Master Overview sheets
5. **Project** how many duties and reserves each clerk should take this month
6. Solve a **primary duty** assignment with CP-SAT
7. Solve **reserve rounds** sequentially (default R1 then R2)
8. Write duty=`1`, R1=`R`, R2=`R2` into the month’s Master Duty Overview grid

Fairness goals (soft/hard depending on mode):

- Respect availability and preferences
- Match projected duty/reserve counts where possible
- Enforce a minimum gap between a clerk’s assignments (including across rounds)
- Balance weekend load; prefer clerks who marked weekend preference

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| Python **3.11+** | Developed against 3.11 / 3.13 locally |
| Network access | Google Sheets / Drive APIs |
| GCP service account | Sheets + Drive scopes (see secrets) |
| Target Google Spreadsheet | Must match sheet naming/layout expectations below |
| Optional: ChatGPT / Claude | Used manually in Step 3 to normalize free-text responses into JSON |

Core libraries (see `app/requirements.txt` for pins):

- `streamlit` — UI
- `gspread` + `google-auth` — Sheets access
- `ortools` — CP-SAT solver
- `pandas` — tables
- `holidays` — Singapore public holidays

## Google Cloud & secrets

### 1. Create a service account

1. In Google Cloud Console, create (or reuse) a project
2. Enable **Google Sheets API** and **Google Drive API**
3. Create a **service account** and download a JSON key
4. Copy the `client_email` from that JSON

### 2. Share the spreadsheet

Share the planning spreadsheet with the service account email as **Editor**.

The Spreadsheet ID is the long ID in the URL:

`https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`

### 3. Configure Streamlit secrets

Create `app/.streamlit/secrets.toml` (not committed — `.streamlit` is in `.gitignore`).

Map fields from the service-account JSON into a TOML table named `gcp_service_account`:

```toml
[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\nYOUR_KEY_HERE\n-----END PRIVATE KEY-----\n"
client_email = "your-sa@your-project.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/..."
universe_domain = "googleapis.com"
```

**Important:** In TOML, keep `\n` inside the `private_key` string (as in the downloaded JSON). Do not paste a multi-line PEM unless you format it correctly for TOML.

There may also be a root `service-key.json` used historically — it is gitignored. Prefer Streamlit secrets; the app reads `st.secrets["gcp_service_account"]` only.

Scopes used by the app (`app/app.py`):

- `https://www.googleapis.com/auth/spreadsheets`
- `https://www.googleapis.com/auth/drive`

## Google Sheet layout

The app assumes a specific spreadsheet layout. Changing column order or sheet titles without code changes will break steps.

## Operator workflow (5 steps)

Progress is stored in `st.session_state.step` (1–5). Use **Next / Back** at the bottom of each step. Sidebar inputs apply across steps.

### Step 1 — Connect & select clerks

**File:** `ui/step1_connect.py`

1. Share the sheet with the service account email shown in the UI
2. Paste Spreadsheet ID (default ID in the text box may be an existing team sheet — change if needed)
3. Click **Connect**
4. Tick/untick clerks to include
5. **Next** (enabled only when a spreadsheet is connected)

Creates `personnel_df`, `clerk_selection`, `updated_personnel_df`, and `sh` in session state.

### Step 2 — Configure slots & create worksheets

**File:** `ui/step2_slots.py`

1. Review/edit the slot grid (`Slot 1` / `Slot 2` toggles). Weekends and SG public holidays default to two slots
2. Optionally click **Create … Send Out** and **Create … Master Duty Overview**
   - Buttons are disabled (error shown) if worksheets with those titles already exist
3. **Next**

Slot labels:

- One slot: `dd-mm-yy`
- Two slots: `dd-mm-yy (AM)` and `dd-mm-yy (PM)`

### Step 3 — Availability & preferences

**File:** `ui/step3_availability.py`

1. Paste raw clerk responses into the text area
2. Copy the generated LLM prompt into ChatGPT/Claude
3. Paste the LLM JSON array back into the app
4. App builds `availability_df` via `inputs.build_availability_from_input`
5. Click **Update Google Sheet** to colour unavailable/preferred cells on the Master Overview

Expected LLM JSON shape (array of triples):

```json
[
  ["PTE Example Name", [1, 2, 4, 5], ["Weekends"]],
  ["CPL Other Name", ["Weekdays"], ["Saturday AM"]]
]
```

- Entry `[0]`: must match `RANK & NAME` from personnel
- Entry `[1]`: unavailable dates / tokens (day numbers, weekday names, `Weekdays` / `Weekends`)
- Entry `[2]`: preference tokens (same vocabulary; two-word tokens like `"Weekends AM"` supported)

Column name in code is spelled `Preferrences` (typo kept for compatibility).

If a clerk is missing from the LLM output, they stay fully available (`1` on all slots).

### Step 4 — Historical points & projections

**File:** `ui/step4_points.py`

1. Loads historical duty/R1/R2 from the **previous two calendar months** relative to the sidebar year/month (`previous_months` in `inputs.py`, wrapping across year boundaries — e.g. planning Feb 2027 → JAN27 + DEC26)
2. Builds planning table + projected duty/reserve counts
3. Shows expanders for historical and obligated totals, plus suggested projections

Those prior Master Overview sheets must already exist with matching `{MMM}{YY}` titles.

### Step 5 — Generate & write schedules

**File:** `ui/step5_schedule.py`

1. Solves primary duty schedule, then reserve rounds
2. Shows metrics (mode, assigned count, weekend imbalance, preferred weekends) plus Schedule / Summary / Compliance tabs
3. Shows combined Overall Duty Plan (Duty + R1 + R2 columns)
4. **Update Schedule** writes values and colours into the Master Overview
5. **Regenerate Schedule** re-runs the solver with current sidebar/session inputs

## Sidebar settings

| Control | Role | Default (UI) |
|---------|------|----------------|
| Year / Month | Slot calendar for Step 2; month drives sheet title prefix | Next calendar month |
| Duty Per Month | Obligation rate used in projections (`O. Duty`) | `1.33` |
| Reverse Per Month | Reserve obligation rate (`O. Reserve`) — label typo for “Reserve” | `3` |
| Min Gap Days | Minimum days between a clerk’s assignments (within and across rounds) | `7` |
| Solver Time Limit | CP-SAT `max_time_in_seconds` | `10` |
| Use Fixed Random Seed | Stabilises projection tie-breaking | On |
| Random Seed | Seed when fixed seed enabled | `42` |
| Reserve Rounds | Number of reserve solves after duty (2 ⇒ R1+R2) | `2` |

## Availability encoding & preferences

### Cell codes (solver / matrix)

| Value | Meaning |
|------:|---------|
| `0` | Unavailable — cannot be assigned |
| `1` | Available |
| `2` | Available **and preferred** (boosts weekend preference objective) |
| `3` | Already assigned in a previous round — blocked for that slot; used for gap constraints |

### Preference / unavailability tokens

Parsed in `inputs.py`:

**Simple (one word / number):** weekday name (`Monday`…), `Weekdays`, `Weekends`, or day-of-month number (`11`)

**Complex (two words):** e.g. `Weekends AM` — both tokens must match slot metadata (`day`, `day_type`, `day_name`, and shift where applicable)

Unavailable tokens win over preferred tokens on the same slot.

Public holidays: Singapore (`holidays.country_holidays("SG", …)`), shown as `PH: …` in the slot config.

## How scheduling works

### Pipeline

```text
availability_df + duty_points_df
        │
        ▼
generate_planning_table()     ← scheduling/planning.py
        │  adds H./O./P. Duty & Reserve columns
        ▼
generate_schedule_from_inputs()   ← scheduling/solver.py  (duty / P. Duty)
        │  marks assigned slots as 3
        ▼
generate_reserve_schedules_from_inputs()  ← scheduling/reserves.py
        │  R1 on slots with 1 prior assignment, then R2 with 2, …
        ▼
ScheduleResult(s) → UI → optional Google Sheet write
```

### Planning columns (`scheduling/config.py`)

| Column | Meaning |
|--------|---------|
| `H. Duty` / `H. Reserve` | Historical totals |
| `O. Duty` / `O. Reserve` | Obligation = active months × sidebar rates |
| `P. Duty` / `P. Reserve` | Projected assignments this month (heap-based distribution) |
| `Active (Months)` | How many months the clerk appears active (including current) |

### Solver behaviour (`scheduling/solver.py`)

For each solve (duty or a reserve round):

1. Try **strict** mode first: exact projected counts + hard gap constraints; maximize preferred weekends − weekend imbalance
2. If infeasible, **fallback** mode: soft projected diffs and soft gap violations; minimize a weighted penalty

Constraints include:

- Exactly one assignee per slot in that round
- Unavailable (`0`) blocked
- Gap vs prior-round assignments (`value == 3`) and within-round pairs closer than `min_gap_days`
- Weekend balance / preference terms in the objective

### Reserve rounds (`scheduling/reserves.py`)

Each slot can hold up to three occupants across rounds: **duty + R1 + R2**.

- Round 0 (R1): only slots that already have exactly 1 assignment (the duty clerk)
- Round 1 (R2): slots with exactly 2 prior assignments
- Assigned clerks are zeroed out on that slot so they cannot be picked again
- Gap rules still see all prior `3`s on the shared planning table

### Result objects (`models.py`)

- `ScheduleResult` — mode, metrics, `schedule` / `summary` / `compliance` rows
- `ReserveScheduleResponse` — list of reserve `ScheduleResult`s

## Handoff checklist

For the person taking over:

- [ ] Clone repo and create `app/.venv`, install `requirements.txt`
- [ ] Obtain or create a GCP service account; enable Sheets + Drive APIs
- [ ] Create `app/.streamlit/secrets.toml` from the JSON key
- [ ] Confirm access to the planning spreadsheet; share with the SA email
- [ ] Run `streamlit run app.py` from `app/`
- [ ] Walk Steps 1–5 on a **copy** of the sheet
- [ ] Know who owns the production spreadsheet and the GCP project

If something in this doc disagrees with the code, **trust the code** and update this README.
