#!/bin/bash
set -e

# Ensure X11 socket directory exists. In this image, startup can run as non-root,
# so ownership changes may be disallowed by the runtime; do not fail hard on that.
mkdir -p /tmp/.X11-unix
if [ "$(id -u)" -eq 0 ]; then
    chown root:root /tmp/.X11-unix || true
fi
chmod 1777 /tmp/.X11-unix || true

# Keep Wine prefix writable by runtime user. Only root can chown recursively.
mkdir -p /config/.wine
if [ "$(id -u)" -eq 0 ]; then
    chown -R abc:abc /config/.wine || true
fi

