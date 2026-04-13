# Frontend

Minimal Streamlit frontend for the scheduler backend.

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

By default the frontend calls the backend at `http://127.0.0.1:8000`.
