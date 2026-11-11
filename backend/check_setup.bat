@echo off
REM Checks backend\.env and the MongoDB / Google settings. Shows no passwords.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :novenv
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" check_setup.py
pause
exit /b 0
:novenv
echo Run run.bat once first: it creates the Python environment.
pause
exit /b 1
