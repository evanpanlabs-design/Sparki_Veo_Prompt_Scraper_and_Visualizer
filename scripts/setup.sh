#!/usr/bin/env bash
# Setup script for X/Twitter Scraper
# Installs Python dependencies and validates the environment

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== X Scraper Setup ==="

# Check Python
if ! command -v python &>/dev/null; then
    echo "Error: Python is not installed or not in PATH"
    exit 1
fi

PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
echo "Python version: $PYTHON_VERSION"

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -r "$PROJECT_ROOT/requirements.txt"

# Validate twikit
echo ""
echo "Validating twikit installation..."
python -c "import twikit; print(f'twikit version: {twikit.__version__}')" 2>/dev/null || \
python -c "import twikit; print('twikit imported successfully')"

# Create outputs directory
mkdir -p "$PROJECT_ROOT/outputs"

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Run auth setup:  python scripts/auth_setup.py"
echo "  2. Run scraper:     python scripts/x_scraper.py --query \"veo prompt\""
