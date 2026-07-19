@echo off
REM Quick launcher for Windows
if not exist ".venv" (
    echo First run detected. Running setup_studio.py...
    python setup_studio.py
)
.venv\Scripts\python main.py %*
