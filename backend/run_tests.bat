@echo off
REM Runs Matcher's automated tests (no AI key needed: they use a stand-in AI).
cd /d "%~dp0"
if not exist ".venv\installed.ok" (
  echo Run run.bat once first to install everything.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pytest -q
pause
