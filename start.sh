#!/bin/bash
#
# start.sh
#
# DuoGuard launcher.
#
# - Creates / activates a virtualenv in ./myenv
# - Installs requirements.txt
# - Verifies every critical Python module can actually be imported
# - Starts uvicorn in the background, writes a PID file, and
#   tails the startup log if anything goes wrong
#
# NOTE: DuoGuard talks to Upstox via plain HTTP (requests), NOT via
# the upstox-python-sdk. Do not add `upstox_client` to the import
# checks below — it is not a project dependency.

APP_NAME="duoguard"
PORT=8003

PID_FILE="${APP_NAME}.pid"
LOG_DIR="logs"
VENV_DIR="myenv"
REQUIREMENTS_FILE="requirements.txt"

echo "=========================================="
echo "  DuoGuard — TOTP Authenticator"
echo "=========================================="
echo
echo "Starting FastAPI application..."
echo

mkdir -p "$LOG_DIR"

# --------------------------------------------------
# Check if application is already running
# --------------------------------------------------

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")

    if kill -0 "$PID" 2>/dev/null; then
        echo "Application is already running."
        echo "PID: $PID"
        echo "Port: $PORT"
        exit 0
    else
        echo "Removing stale PID file."
        rm -f "$PID_FILE"
    fi
fi

# --------------------------------------------------
# Create virtual environment if it does not exist
# --------------------------------------------------

if [ ! -d "$VENV_DIR" ]; then
    echo
    echo "Virtual environment not found."
    echo "Creating $VENV_DIR..."

    if ! python3 -m venv "$VENV_DIR"; then
        echo
        echo "ERROR: Failed to create virtual environment."
        exit 1
    fi

    echo "Virtual environment created."
fi

# --------------------------------------------------
# Activate virtual environment
# --------------------------------------------------

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo
    echo "ERROR: Virtual environment activation script not found:"
    echo "$VENV_DIR/bin/activate"
    exit 1
fi

echo
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

if [ $? -ne 0 ]; then
    echo
    echo "ERROR: Failed to activate virtual environment."
    exit 1
fi

echo "Virtual environment activated."

# --------------------------------------------------
# Verify Python from virtual environment
# --------------------------------------------------

PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

if [ ! -x "$PYTHON" ]; then
    echo
    echo "ERROR: Virtual environment Python not found:"
    echo "$PYTHON"
    exit 1
fi

echo
echo "Python environment:"
echo "Python: $(which python)"
echo "Version: $(python --version)"
echo "Pip: $(which pip)"
echo

# --------------------------------------------------
# Install requirements if requirements.txt exists
# --------------------------------------------------

if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "Checking Python dependencies..."

    if ! "$PYTHON" -m pip install -r "$REQUIREMENTS_FILE"; then
        echo
        echo "ERROR: Failed to install Python packages."
        exit 1
    fi

    echo
    echo "Python dependencies are ready."
else
    echo
    echo "WARNING: $REQUIREMENTS_FILE not found."
    echo "Skipping dependency installation."
fi

# --------------------------------------------------
# Verify critical imports
# --------------------------------------------------
# Matches what requirements.txt installs. Import names differ from
# pip names in a few cases:
#     python-dotenv      -> import dotenv
#     python-multipart   -> import multipart
#     PyYAML (via ddtrace, etc.) -> not required here
#
# If any of these fail, we exit 1 BEFORE spawning uvicorn so the log
# is clean and the failure is obvious.

echo
echo "Checking critical imports..."

"$PYTHON" - <<'PYCHECK'
import sys

modules = [
    "fastapi",
    "uvicorn",
    "pymongo",
    "dotenv",
    "jinja2",
    "itsdangerous",
    "pyotp",
    "qrcode",
    "apscheduler",
    "requests",
    "multipart",
]

missing = []
for name in modules:
    try:
        __import__(name)
    except Exception as exc:
        missing.append((name, f"{type(exc).__name__}: {exc}"))

if missing:
    print("ERROR: missing or unimportable dependencies:")
    for name, err in missing:
        print(f"  - {name}: {err}")
    sys.exit(1)

print("All critical imports OK.")
PYCHECK

if [ $? -ne 0 ]; then
    echo
    echo "ERROR: Dependency verification failed. See output above."
    exit 1
fi

# --------------------------------------------------
# Start FastAPI
# --------------------------------------------------

echo
echo "Starting FastAPI..."
echo "Host: 0.0.0.0"
echo "Port: $PORT"
echo "Python: $PYTHON"
echo

# Keep startup errors in a dedicated log instead of /dev/null.
STARTUP_LOG="$LOG_DIR/fastapi_startup.log"

nohup "$PYTHON" -m uvicorn main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    >"$STARTUP_LOG" 2>&1 &

PID=$!

echo "$PID" > "$PID_FILE"

sleep 2

# --------------------------------------------------
# Verify application process
# --------------------------------------------------

if kill -0 "$PID" 2>/dev/null; then
    echo
    echo "=========================================="
    echo "Application started successfully."
    echo "=========================================="
    echo "PID:    $PID"
    echo "Port:   $PORT"
    echo "Python: $PYTHON"
    echo "Venv:   $VENV_DIR"
    echo "Startup log: $STARTUP_LOG"
    echo
else
    echo
    echo "=========================================="
    echo "ERROR: Application failed to start."
    echo "=========================================="
    echo
    echo "Startup log:"
    echo "  $STARTUP_LOG"
    echo
    echo "Last 50 lines:"
    echo "------------------------------------------"

    if [ -f "$STARTUP_LOG" ]; then
        tail -n 50 "$STARTUP_LOG"
    fi

    echo "------------------------------------------"

    rm -f "$PID_FILE"

    exit 1
fi