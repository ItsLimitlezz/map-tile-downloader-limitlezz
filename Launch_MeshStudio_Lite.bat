@echo off
setlocal

cd /d "%~dp0"

echo Installing/updating Python requirements...
py -3 -m pip install --upgrade pip >nul 2>&1
if errorlevel 1 (
    python -m pip install --upgrade pip
) else (
    py -3 -m pip install -r requirements.txt
    goto run_app
)

python -m pip install -r requirements.txt

:run_app
echo Launching MeshStudio Lite...
py -3 src\TileDL.py >nul 2>&1
if errorlevel 1 (
    python src\TileDL.py
)

endlocal
