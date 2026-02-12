#!/bin/zsh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Installing/updating Python requirements..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "Launching MeshStudio Lite..."
python3 src/TileDL.py

