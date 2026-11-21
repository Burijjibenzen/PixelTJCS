@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo Python virtual environment not found. See README.md.
    popd
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -X utf8 -B -m scripts.start_roamchestra
set "ROAMCHESTRA_EXIT=%ERRORLEVEL%"
popd
if not "%ROAMCHESTRA_EXIT%"=="0" pause
exit /b %ROAMCHESTRA_EXIT%
