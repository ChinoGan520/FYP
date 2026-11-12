#!/usr/bin/env bash
# Disposable emulator only; never starts or resets a real Firebase database.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export FIREBASE_PROJECT_ID=demo-irice-test
export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
export IRICE_TEST_FIRESTORE=1
PYTHON_BIN="${IRICE_PYTHON:-.venv/bin/python}"
if ! "$PYTHON_BIN" -c 'import google.cloud.firestore' >/dev/null 2>&1; then
  echo 'Install the pinned Python dependencies first: bash scripts/setup.sh' >&2
  exit 1
fi
if [ "${IRICE_USE_RUNNING_EMULATOR:-0}" = "1" ]; then
  # Explicitly reuse only the local test emulator; the fixture also verifies it.
  exec "$PYTHON_BIN" -m pytest -q
elif [ -n "${FIREBASE_CLI:-}" ]; then
  "$FIREBASE_CLI" emulators:exec --only firestore --project demo-irice-test "$PYTHON_BIN -m pytest -q"
else
  npm_config_cache="${npm_config_cache:-/tmp/irice-npm-cache}" npx --yes firebase-tools@15.36.0 emulators:exec --only firestore --project demo-irice-test "$PYTHON_BIN -m pytest -q"
fi
