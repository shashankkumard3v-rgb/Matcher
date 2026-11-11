@echo off
REM ============================================================
REM  Matcher - starts the server (API + app) on this laptop.
REM  First run: creates .venv and installs packages (a few minutes).
REM ============================================================
cd /d "%~dp0"

REM --- find Python (prefer the "py" launcher from python.org) ---
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul || goto :nopython

REM --- install (again) if the last install didn't finish ---
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv || goto :error
)
REM --- (re)install packages when requirements.txt changed since the last install ---
if not exist ".venv\installed.req" goto :install
fc /b requirements.txt ".venv\installed.req" >nul 2>nul || goto :install
goto :installed
:install
echo Installing packages - this takes a few minutes the first time...
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
copy /y requirements.txt ".venv\installed.req" >nul
:installed

REM --- settings file with your API key ---
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo  Created backend\.env
  echo  Open it in Notepad, paste your key after GEMINI_API_KEY=  ^(or another provider's key^),
  echo  save, then double-click run.bat again.
  echo.
  pause
  exit /b 1
)

echo.
echo  ============================================================
echo   Matcher is starting.
echo   Open:             http://localhost:8000   (exactly this address: Google sign-in needs it)
echo   Who can open it:  only this laptop. Other devices on your Wi-Fi are refused.
echo   Sign in:          with Google. Each account sees only its own runs and notes.
echo   Stop the server:  close this window, or press Ctrl+C
echo   Auto-restart:     ON - edits to app, config or skills restart it by themselves
echo  ============================================================
echo.
REM serve.py = uvicorn with auto-restart (like nodemon), checking files by polling because the
REM project is in OneDrive. Runs and your notes are not watched, so saving work never restarts it.
".venv\Scripts\python.exe" serve.py
if errorlevel 1 (
  echo.
  echo  The server stopped with an error. If it says the address is already in use,
  echo  Matcher is probably already running in another window.
  pause
)
goto :eof

:nopython
echo Python was not found. Install Python 3.11 or newer from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup. Then run this again.
pause
exit /b 1

:error
echo.
echo Setup failed (see the message above). Check your internet connection and run this again;
echo it will pick up where it left off.
pause
exit /b 1
