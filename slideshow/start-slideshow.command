#!/bin/bash
# Double-click this file to launch the VICAR slideshow.
# It (1) regenerates _manifest.json from slideshow_media/ contents,
#    (2) starts a local web server in this folder,
#    (3) opens the slideshow in your default browser.
#
# To reorder slides: rename files in slideshow_media/ (change the NN_ prefix).
# To edit captions: open captions.csv next to this file. The slideshow looks up
#   captions by the 'title' column, which is the filename without the NN_ prefix
#   and without the extension. Renumbering files does NOT break captions.

set -e
cd "$(dirname "$0")"

PORT=8765
URL="http://localhost:${PORT}/slideshow.html"

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "Python is not installed. Install Python 3 to run the slideshow."
  read -r -p "Press return to close this window."
  exit 1
fi

# Regenerate manifest from current folder contents
"${PYTHON}" - <<'PY'
import json, os
folder = 'slideshow_media'
files = sorted([
    f for f in os.listdir(folder)
    if not f.startswith('.') and not f.startswith('_')
])
with open('_manifest.json', 'w') as fp:
    json.dump({'folder': folder, 'files': files}, fp, indent=2)
print(f"  manifest: {len(files)} files in {folder}/")
PY

# Generate any missing video thumbnails for the grid view
if command -v ffmpeg >/dev/null 2>&1; then
  mkdir -p _thumbs
  for f in slideshow_media/*.mp4 slideshow_media/*.mov slideshow_media/*.webm; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    name="${base%.*}"
    out="_thumbs/${name}.jpg"
    if [ ! -f "$out" ]; then
      echo "  thumb: ${name}"
      ffmpeg -y -loglevel error -ss 0.5 -i "$f" -vframes 1 -vf "scale=480:-2" -q:v 4 "$out" 2>/dev/null
    fi
  done
fi

echo ""
echo "  VICAR slideshow"
echo "  ----------------"
echo "  Serving:  $(pwd)"
echo "  URL:      ${URL}"
echo ""
echo "  Opening browser in 2 seconds. Press Ctrl+C in this window to stop the server."
echo ""

( sleep 2 && open "${URL}" ) &

exec "${PYTHON}" -m http.server "${PORT}" --bind 127.0.0.1
