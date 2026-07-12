# Elyton ECM

Desktop app for the ECM-converted Ender 3: G-code generator (Phase 1), simulator (Phase 2), machine control (Phase 3).

Full guide: [USAGE.md](USAGE.md)

## Setup (once)

    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt

## Run

    .venv\Scripts\python app.py

## Self-check

    .venv\Scripts\python test_toolpath.py
