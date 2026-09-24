#!/bin/bash
set -euo pipefail
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
bash setup.sh
if [ "$#" -gt 0 ]; then
  .venv/bin/python cloud.py --local "$@"
elif [ -s urls.txt ]; then
  .venv/bin/python cloud.py --local --urls-file urls.txt
else
  echo 'No user target supplied: running the explicitly labelled public sample only.'
  .venv/bin/python cloud.py --local --max-comments 5
fi
