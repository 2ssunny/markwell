@echo off
setlocal

cd /d "%~dp0"

REM Prefer the conda environment. PySide6 cannot load inside a plain venv built
REM on Anaconda's base interpreter -- the base ships older CRT/Qt DLLs that
REM shadow the ones PySide6 expects. A dedicated conda env has neither.
set "PYTHON=%USERPROFILE%\anaconda3\envs\markwell\python.exe"
if not exist "%PYTHON%" set "PYTHON=venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Python environment not found.
    echo.
    echo Create it with:
    echo     conda create -n markwell python=3.13 -y
    echo     conda run -n markwell pip install -r requirements.txt
    pause
    exit /b 1
)

"%PYTHON%" -m markwell.gui.app
if errorlevel 1 pause
