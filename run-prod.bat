@echo off
REM Sanadex.wanis.online - run the production server on Windows (waitress)
cd /d "%~dp0"
if not exist ".venv" (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install --quiet --upgrade pip
pip install --quiet "jinja2>=3.1" "waitress>=3.0"
echo.
echo   Sanadex.wanis.online is starting on http://localhost:8000
echo   Close this window to stop it.
echo   Cannot sign in? Run: python manage.py reset-admin
echo.
python run_prod.py
pause
