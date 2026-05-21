#!/bin/bash
# VICAR Lab screensaver launcher.
# Starts a tiny local web server in this folder, then opens Chrome
# in fullscreen "app" mode pointing at screensaver.html.
# Server and browser shut down together when the browser window closes.
#
# Exit the screensaver: Esc, q, click, or move the mouse a bit.

set -e
cd "$(dirname "$0")"

PORT=8766                                # different from start-slideshow.command (8765)
URL="http://localhost:${PORT}/screensaver.html"

if command -v python3 >/dev/null 2>&1; then PYTHON=python3
elif command -v python  >/dev/null 2>&1; then PYTHON=python
else
  zenity --error --text="Python 3 is required to run the screensaver." 2>/dev/null \
    || echo "Python 3 is required to run the screensaver."
  exit 1
fi

# Refresh manifest from current folder contents
"${PYTHON}" - <<'PY'
import json, os
folder = 'slideshow_media'
files = sorted(f for f in os.listdir(folder)
               if not f.startswith('.') and not f.startswith('_'))
with open('_manifest.json', 'w') as fp:
    json.dump({'folder': folder, 'files': files}, fp, indent=2)
PY

# If something else is on the port, reuse it (assume our own prior run)
if ! ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":${PORT}\$"; then
  "${PYTHON}" -m http.server "${PORT}" --bind 127.0.0.1 >/dev/null 2>&1 &
  SERVER_PID=$!
  # Wait for it to be ready
  for _ in $(seq 1 30); do
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":${PORT}\$" && break
    sleep 0.1
  done
fi

# Temporarily un-pin the Ubuntu dock so it hides behind our fullscreen
# kiosk window (intellihide already covers it once dock-fixed is off).
# Original value is restored on exit.
DOCK_FIXED_PREV=""
if gsettings writable org.gnome.shell.extensions.dash-to-dock dock-fixed >/dev/null 2>&1; then
  DOCK_FIXED_PREV=$(gsettings get org.gnome.shell.extensions.dash-to-dock dock-fixed 2>/dev/null || true)
  gsettings set org.gnome.shell.extensions.dash-to-dock dock-fixed false 2>/dev/null || true
fi

cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then kill ${SERVER_PID} 2>/dev/null || true; fi
  if [ -n "${DOCK_FIXED_PREV}" ]; then
    gsettings set org.gnome.shell.extensions.dash-to-dock dock-fixed "${DOCK_FIXED_PREV}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Pick a browser. Chrome's --app mode is cleanest (no tab strip, no URL bar).
BROWSER=""
if   command -v google-chrome    >/dev/null 2>&1; then BROWSER=google-chrome
elif command -v chromium         >/dev/null 2>&1; then BROWSER=chromium
elif command -v chromium-browser >/dev/null 2>&1; then BROWSER=chromium-browser
fi

if [ -n "${BROWSER}" ]; then
  # Dedicated profile dir so it doesn't reuse a running Chrome session.
  PROFILE_DIR="${HOME}/.cache/vicar-screensaver-profile"
  mkdir -p "${PROFILE_DIR}"
  # Background helper: once the kiosk window appears, force EWMH fullscreen
  # so the GNOME top bar gets covered as well as the dock. wmctrl matches
  # against the <title> in screensaver.html.
  ( for _ in $(seq 1 40); do
      if command -v wmctrl >/dev/null 2>&1 \
         && wmctrl -l 2>/dev/null | grep -q "VICAR Lab"; then
        wmctrl -r "VICAR Lab" -b add,fullscreen 2>/dev/null || true
        wmctrl -r "VICAR Lab" -b add,above      2>/dev/null || true
        break
      fi
      sleep 0.25
    done ) &

  "${BROWSER}" \
    --user-data-dir="${PROFILE_DIR}" \
    --app="${URL}" \
    --kiosk \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --disable-features=TranslateUI \
    --no-first-run --no-default-browser-check \
    --autoplay-policy=no-user-gesture-required \
    --disable-pinch --overscroll-history-navigation=0
elif command -v firefox >/dev/null 2>&1; then
  firefox --kiosk "${URL}"
else
  xdg-open "${URL}"
  # Without a known browser we can't wait on it; just keep the server up
  wait
fi
