#!/usr/bin/env bash
# Quick launcher — run from repo root
set -e
if [ ! -d ".venv" ]; then
    echo "First run detected. Running setup_studio.py..."
    python3 setup_studio.py
fi
.venv/bin/python main.py "$@"
