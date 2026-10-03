#!/usr/bin/env bash
# One-command setup for the Undercroft Wallet. Run this once:
#   bash setup_wallet.sh
# It installs any missing dependencies, finds your compiled bitcoind on
# its own (falls back to asking ONCE if it truly can't find it), and then
# launches the wallet. No manual pip commands, no editing a settings
# screen, no hunting through folders yourself.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[1/3] Checking for dependencies..."
if ! python3 -c "import PySide6" 2>/dev/null; then
    echo "      Installing PySide6 (one-time, ~1 minute)..."
    pip3 install PySide6 --break-system-packages --quiet
fi
if ! python3 -c "import cryptography" 2>/dev/null; then
    echo "      Installing cryptography (one-time, ~10 seconds)..."
    pip3 install cryptography --break-system-packages --quiet
fi

mkdir -p bin

if [ -f bin/bitcoind ] || [ -f bin/bitcoind.exe ]; then
    echo "[2/3] Daemon already in place, skipping search."
else
    echo "[2/3] Looking for your compiled bitcoind under $HOME ..."
    FOUND=$(find "$HOME" -maxdepth 8 \( -name "bitcoind" -o -name "bitcoind.exe" \) -type f 2>/dev/null | head -n 1)

    if [ -n "$FOUND" ]; then
        echo "      Found: $FOUND"
        cp "$FOUND" bin/
        chmod +x "bin/$(basename "$FOUND")" 2>/dev/null || true
    else
        echo "      Couldn't find it automatically."
        echo "      Paste the full path to your compiled bitcoind and press Enter:"
        read -r MANUAL_PATH
        if [ ! -f "$MANUAL_PATH" ]; then
            echo "      '$MANUAL_PATH' doesn't exist -- stopping here. Re-run this script once you have the right path."
            exit 1
        fi
        cp "$MANUAL_PATH" bin/
        chmod +x "bin/$(basename "$MANUAL_PATH")" 2>/dev/null || true
    fi
fi

echo "[3/3] Launching Undercroft Wallet..."
python3 run_undercroft_wallet.py
