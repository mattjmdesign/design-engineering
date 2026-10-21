#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

python3 scripts/validate.py
python3 -m compileall -q skills scripts
python3 skills/component-compose/scripts/ui_inventory.py selftest
python3 skills/design-system/scripts/token_scan.py selftest
python3 skills/slop-removal/scripts/slop_scan.py selftest

printf '%s\n' 'OK deterministic helper self-tests passed'
