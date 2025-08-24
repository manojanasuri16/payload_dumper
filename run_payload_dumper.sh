#!/bin/bash

# Payload Dumper Runner
# Usage: ./run_payload_dumper.sh <path_to_payload.bin>

if [ $# -eq 0 ]; then
    echo "Usage: $0 <path_to_payload.bin>"
    echo "Example: $0 /path/to/payload.bin"
    exit 1
fi

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Change to script directory
cd "$SCRIPT_DIR"

# Activate virtual environment and run payload dumper
echo "Activating virtual environment..."
source venv/bin/activate

echo "Running payload dumper on: $1"
python payload_dumper.py "$1"

echo "Done! Check the current directory for extracted .img files."
