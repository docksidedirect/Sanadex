@echo off
title Sanadex.wanis.online - reset administrator password
cd /d "%~dp0"
echo.
echo   Sanadex.wanis.online - administrator password recovery
echo   Folder: %~dp0
echo   ------------------------------------------------
echo.
python manage.py reset-admin
echo.
pause
