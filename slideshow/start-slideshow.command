#!/bin/bash
# VICAR Lab slideshow launcher (double-click on macOS, or run from a terminal).
# Thin wrapper: finds Python 3.10 or newer and runs `showcase.py slideshow`
# from the repository root. All behaviour lives in showcase.py and vicar_showcase/.
# The content folder comes from settings.json (see README.md); extra arguments
# such as --content DIR or --port N are passed through.

set -e
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PYTHON=""
for candidate in python3 python; do
  if command -v "${candidate}" >/dev/null 2>&1 \
     && "${candidate}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PYTHON="${candidate}"
    break
  fi
done

if [ -z "${PYTHON}" ]; then
  echo "Python 3.10 or newer is required to run the slideshow."
  if [ -t 0 ]; then read -r -p "Press return to close this window."; fi
  exit 1
fi

exec "${PYTHON}" showcase.py slideshow "$@"
