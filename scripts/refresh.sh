#!/usr/bin/env bash
# Refresh profile cards: pull, fetch data (needs GH_TOKEN), rebuild, commit and push if changed.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --rebase --autostash
python3 scripts/fetch_data.py
python3 scripts/build_cards.py
git add data cards README.md
if git diff --cached --quiet; then echo "Nothing to commit"; exit 0; fi
git commit -m "chore: refresh profile cards"
git push
