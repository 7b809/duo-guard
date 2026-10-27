@echo off
cls
cd /d "%~dp0"

echo ================================================
echo      DuoGuard - TOTP Authenticator
echo ================================================
echo.

set "VENV_PATH="

if exist "myenv\Scripts\python.exe" (
    set "VENV_PATH=%CD%\myenv"
    echo Virtual environment found in current folder.
) else (
    if exist "..\myenv\Scripts\python.exe" (
        set "VENV_PATH=%CD%\..\myenv"
        echo Virtual environment found in parent folder.
    ) else (
        echo Virtual environment not found.
        echo Creating virtual environment in current folder...
        echo.

        python -m venv myenv

        if errorlevel 1 (
            echo.
            echo ERROR: Failed to create virtual environment.
            echo Make sure Python is installed and available in PATH.
            pause
            exit /b 1
        )

        set "VENV_PATH=%CD%\myenv"
        echo.
        echo Virtual environment created successfully.
    )
)

echo.
echo Using virtual environment:
echo %VENV_PATH%
echo.

echo Activating virtual environment...
call "%VENV_PATH%\Scripts\activate.bat"

if errorlevel 1 (
    echo.
    echo ERROR: Failed to activate virtual environment.
    pause
    exit /b 1
)

echo.
echo Python:
where python
python --version
echo.

REM -----------------------------------------------------------
REM Detect the LAN IPv4 address (skips loopback + APIPA).
REM Uses PowerShell first; falls back to ipconfig parsing.
REM -----------------------------------------------------------
set "LAN_IP="
for /f "usebackq tokens=*" %%a in (`powershell -NoProfile -Command "(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1).IPv4Address.IPAddress" 2^>nul`) do set "LAN_IP=%%a"

if not defined LAN_IP (
    for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
        if not defined LAN_IP (
            set "LAN_IP=%%a"
        )
    )
    if defined LAN_IP set "LAN_IP=%LAN_IP: =%"
)

echo ================================================
echo Starting DuoGuard with Uvicorn on port 8003...
echo ================================================
echo.
echo   On this PC:      http://localhost:8003


echo ================================================
echo   Press CTRL+C in this window to stop the server.
echo ================================================
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8003