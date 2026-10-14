@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto use_py
python "%~dp0run.py" gui
if errorlevel 1 pause
exit /b
:use_py
where py >nul 2>nul
if errorlevel 1 goto missing_python
py -3 "%~dp0run.py" gui
if errorlevel 1 pause
exit /b
:missing_python
echo CuePull needs Python 3.10 or newer. Install it from python.org, then reopen CuePull.
pause
