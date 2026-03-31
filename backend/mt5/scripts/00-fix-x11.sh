#!/bin/bash
set -e

# Ensure X11 socket directory exists with required sticky permissions.
mkdir -p /tmp/.X11-unix
chown root:root /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

# Keep Wine prefix writable by the runtime user when persisted via host volume.
mkdir -p /config/.wine
chown -R abc:abc /config/.wine

