#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python scripts/preflight.py
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/validate_contract.py
.venv/bin/python training/smoke_dkt.py
