@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto USE_PY
py -3 -X utf8 apply_patch_382.py
goto END
:USE_PY
python -X utf8 apply_patch_382.py
:END
echo.
pause
