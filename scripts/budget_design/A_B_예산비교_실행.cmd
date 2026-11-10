@echo off
if not exist "%~dp0..\..\.venv\Scripts\pythonw.exe" (
  echo Project Python environment .venv was not found.
  pause
  exit /b 1
)
start "" "%~dp0..\..\.venv\Scripts\pythonw.exe" "%~dp0compare_window.py"
