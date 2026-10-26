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
echo Starting DuoGuard with Uvicorn on port 8009...
echo ================================================
echo.
echo   On this PC:      http://localhost:8009

if defined LAN_IP (
    echo   On same Wi-Fi:   http://%LAN_IP%:8009
    echo.
    echo   ---- Share with phones/tablets/laptops on the same network ----
    echo     Open this exact URL on the other device:
    echo         http://%LAN_IP%:8009
    echo.
    echo   Two things that commonly block this:
    echo     1^) Windows Firewall - if the other device times out, allow
    echo        Python / port 8009 for Private networks. You can run this
    echo        once in an Administrator Command Prompt:
    echo           netsh advfirewall firewall add rule name="DuoGuard 8009" dir=in action=allow protocol=TCP localport=8009 profile=private
    echo.
    echo     2^) Camera QR scanning - browsers only grant camera access on
    echo        HTTPS or http://localhost. When you access the app through
    echo        the LAN IP above, the "Scan QR code" tab will fail. Use the
    echo        "Enter manually" tab instead, or expose the site over HTTPS
    echo        via a tunnel such as ngrok and use the tunnel URL.
) else (
    echo.
    echo   Could not detect a LAN IP. Make sure you are connected to Wi-Fi
    echo   or Ethernet, then re-run this script.
)

echo ================================================
echo   Press CTRL+C in this window to stop the server.
echo ================================================
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8009