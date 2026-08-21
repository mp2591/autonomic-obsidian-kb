#!/usr/bin/env sh
set -eu
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
PYTHONPATH=src python -m unittest discover -s tests -v
printf '%s\n' 'Installed. Run: kb init /path/to/vault'
