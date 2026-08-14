@echo off
REM Wrapper for the daily exception screen, invoked by Windows Task Scheduler.
REM %~dp0 resolves to this script's folder, so the job never depends on the
REM scheduler's working directory. Adjust the python invocation if you use a venv.
cd /d "%~dp0.."
if not exist ".tmp" mkdir ".tmp"
echo ==== %DATE% %TIME% screen start ==== >> .tmp\screen.log
python tools\daily_screen.py >> .tmp\screen.log 2>&1
echo ==== %DATE% %TIME% screen end (exit %ERRORLEVEL%) ==== >> .tmp\screen.log
