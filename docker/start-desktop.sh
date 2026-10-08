#!/bin/sh
set -eu
export XDG_CURRENT_DESKTOP=XFCE
export XDG_SESSION_DESKTOP=xfce
export DESKTOP_SESSION=xfce
# Supervisor priority orders process starts, but does not wait for X readiness.
attempt=0
until xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "X display $DISPLAY did not become ready" >&2
    exit 1
  fi
  sleep 1
done
# Keep the session bus and desktop lifetime separate from Chromium.
exec dbus-run-session -- xfce4-session
