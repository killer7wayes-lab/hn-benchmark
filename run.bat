@echo off
title House Nutrition - veille concurrence
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
  echo Python not found - installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
  echo.
  echo Python installed. Close this window and double-click run.bat again.
  pause
  exit /b
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
)
call ".venv\Scripts\activate.bat"
if exist site if not exist docs\data.js ( if exist site\data.js move /y site\data.js docs\ >nul & if exist site\ads move /y site\ads docs\ads >nul )

echo Checking packages...
python -m pip install -r requirements.txt -q --disable-pip-version-check
python -m playwright install chromium

echo.
echo ===== Collecting prices and ads (the dashboard opens when first results are ready) =====
python run_daily.py --open %*

echo.
echo Finished. Dashboard: docs\index.html  (refresh the page to see the final data)
pause
