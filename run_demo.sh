#!/usr/bin/env bash
# One-shot demo: install deps, seed fake data, launch dashboard.
# Run from the repo root.
set -euo pipefail

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
python seed_demo.py
echo ""
echo "Dashboard starting on http://localhost:8000  (Ctrl-C to stop)"
exec uvicorn server:app --port 8000
