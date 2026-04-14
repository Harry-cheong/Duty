# Frontend

Single-user Streamlit desktop app for the scheduler.

## Install

```bash
cd frontend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

The app runs the scheduling logic in-process. No separate backend service is required.
