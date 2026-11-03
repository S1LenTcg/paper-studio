@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m paperstudio.server --open
) else (
  python -m paperstudio.server --open
)
if errorlevel 1 pause
