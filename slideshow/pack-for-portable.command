#!/bin/bash
# Double-click to build a single zip on your Desktop containing everything
# the slideshow needs to run on another Mac.
# On the destination Mac: unzip, then double-click start-slideshow.command.

set -e
cd "$(dirname "$0")"
HERE="$(pwd)"
NAME="$(basename "$HERE")"
OUT="$HOME/Desktop/vicar-slideshow.zip"

echo ""
echo "  Building portable bundle..."
echo "  Source: $HERE"
echo "  Output: $OUT"
echo ""

# Use the parent dir as the zip root so unzipping creates a clean
# 'slideshow' folder rather than dumping all files into Desktop.
PARENT="$(dirname "$HERE")"
cd "$PARENT"

# Build the zip:
#   -r  recurse
#   -X  strip macOS attributes
#   -q  quiet (we print our own progress)
#   exclude __MACOSX-style cruft, dropped/preview folders, and any .DS_Store
rm -f "$OUT"
zip -r -X -q "$OUT" "$NAME" \
  -x "*.DS_Store" \
  -x "*/_dropped/*" \
  -x "*/_previews/*" \
  -x "*/__MACOSX/*" \
  -x "*/.git/*"

SIZE=$(du -h "$OUT" | awk '{print $1}')

echo "  Done. ${SIZE} → $OUT"
echo ""
echo "  Next steps:"
echo "  1. AirDrop / iMessage / USB-copy this zip to the other Mac."
echo "  2. On that Mac: unzip it (double-click), then double-click"
echo "     'start-slideshow.command' inside the unzipped slideshow folder."
echo "  3. If macOS Gatekeeper blocks the .command file, right-click it"
echo "     and choose Open."
echo ""

# Open Finder pointing at the zip so it's easy to grab
open -R "$OUT"

read -r -p "  Press return to close this window. "
