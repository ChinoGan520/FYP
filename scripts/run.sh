#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON_BIN="${IRICE_PYTHON:-.venv/bin/python}"
# Data initialization and organizer provisioning are explicit one-time CLI tasks.
exec "$PYTHON_BIN" -m flask --app irice run --host "${IRICE_HOST:-127.0.0.1}" --port "${IRICE_PORT:-5000}"
