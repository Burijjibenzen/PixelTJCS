@echo off
setlocal
pushd "%~dp0" || exit /b 1
if exist "launcher.local.bat" call "launcher.local.bat"

set "PYTHON_EXE="
if defined ROAMCHESTRA_PYTHON set "PYTHON_EXE=%ROAMCHESTRA_PYTHON%"
if defined PIXELTJCS_PYTHON set "PYTHON_EXE=%PIXELTJCS_PYTHON%"
if not defined PYTHON_EXE if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
if not defined PYTHON_EXE if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE for %%P in (python.exe) do set "PYTHON_EXE=%%~$PATH:P"
if not defined PYTHON_EXE goto missing_python
if not exist "%PYTHON_EXE%" goto missing_python

"%PYTHON_EXE%" -X utf8 -B -m scripts.start_pixeltjcs
set "PIXELTJCS_EXIT=%ERRORLEVEL%"
popd
if not "%PIXELTJCS_EXIT%"=="0" pause
exit /b %PIXELTJCS_EXIT%

:missing_python
echo Python not found. Set PIXELTJCS_PYTHON in launcher.local.bat or activate your Conda environment.
popd
pause
exit /b 1
