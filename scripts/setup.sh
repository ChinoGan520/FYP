#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 -m venv .venv
PIP_CACHE_DIR="${PIP_CACHE_DIR:-/tmp/irice-pip-cache}" .venv/bin/python -m pip install -r requirements.txt
echo 'Dependencies installed. Configure Firebase, then run flask --app irice init-db and create-admin as described in README.md.'
