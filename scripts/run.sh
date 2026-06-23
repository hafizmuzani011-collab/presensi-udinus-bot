#!/bin/bash
# ==============================================
# Run script untuk Presensi Udinus Bot
# Untuk STB Armbian / Linux / Termux
# ==============================================
set -e

cd "$(dirname "$0")"
LOG_FILE="bot.log"

echo "[$(date)] Starting bot..."

# Cek python
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python tidak ditemukan"
    exit 1
fi

# Cek virtual env, pakai kalau ada
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
    echo "Using venv: $PYTHON"
fi

# Cek playwright browsers
$PYTHON -c "from playwright.sync_api import sync_playwright" 2>/dev/null || {
    echo "Install playwright browsers..."
    $PYTHON -m playwright install chromium
}

# Loop: restart otomatis kalau crash
while true; do
    echo "[$(date)] Starting bot instance..."
    $PYTHON bot.py
    EXIT_CODE=$?
    echo "[$(date)] Bot exited with code $EXIT_CODE. Restart in 5s..."
    sleep 5
done
