#!/usr/bin/env bash
# Refresh the profile cards from your own machine: fetch data, render, commit, push.
# Needs: python3 + `pip install -r scripts/requirements.txt`, and either `gh auth login` or GH_TOKEN.
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --rebase --autostash -q
python3 scripts/fetch_data.py
python3 scripts/build_cards.py

git add data cards README.md
if git diff --cached --quiet; then echo "Nothing to commit"; exit 0; fi
git commit -qm "chore: refresh profile cards"
git push -q
echo "Cards refreshed and pushed."
