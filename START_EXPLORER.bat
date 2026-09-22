@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 goto directory_error

rem Prefer the Python launcher, then fall back to python.exe.
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import sys; assert sys.version_info >= (3, 9)" >nul 2>nul
if errorlevel 1 goto try_python
set "PYTHON=py -3"
goto python_ready

:try_python
where python >nul 2>nul
if errorlevel 1 goto python_missing
python -c "import sys; assert sys.version_info >= (3, 9)" >nul 2>nul
if errorlevel 1 goto python_missing
set "PYTHON=python"
goto python_ready

:python_missing
echo ERROR: Python 3.9 or newer was not found.
echo Install Python from https://www.python.org/downloads/
goto failed

:python_ready
echo Using %PYTHON%
%PYTHON% -c "import paramiko" >nul 2>nul
if not errorlevel 1 goto start_app

echo Installing SSH dependency Paramiko...
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 goto install_failed
%PYTHON% -c "import paramiko" >nul 2>nul
if errorlevel 1 goto install_failed

:start_app
echo Starting AKUZ Log Explorer...
%PYTHON% akuz_app.py
if errorlevel 1 goto app_failed
popd
exit /b 0

:directory_error
echo ERROR: Cannot open the Explorer directory.
goto failed_no_popd

:install_failed
echo ERROR: Cannot install or import Paramiko.
echo Check pip access or install it manually in the selected Python.
goto failed

:app_failed
echo ERROR: AKUZ Log Explorer stopped with an error.
echo Read the error above. If port 8765 is in use, close the previous Explorer.
goto failed

:failed
popd
:failed_no_popd
pause
exit /b 1
