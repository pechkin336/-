@echo off
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 goto TRY_PYTHON
py -3.12 -X utf8 build_exe.py %*
if not errorlevel 9009 goto DONE
py -3 -X utf8 build_exe.py %*
goto DONE

:TRY_PYTHON
where python >nul 2>nul
if errorlevel 1 goto NO_PYTHON
python -X utf8 build_exe.py %*
goto DONE

:NO_PYTHON
echo [ERROR] Python 3 is not installed or not in PATH.
pause
exit /b 1

:DONE
exit /b %errorlevel%
