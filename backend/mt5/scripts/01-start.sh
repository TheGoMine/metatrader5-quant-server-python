#!/bin/bash

# Source common variables and functions
source /scripts/02-common.sh

# Normalize X11 and Wine prefix permissions before any GUI/Wine startup.
/scripts/00-fix-x11.sh

# Run installation scripts
/scripts/03-install-mono.sh
/scripts/04-install-mt5.sh
/scripts/05-install-python.sh
/scripts/06-install-libraries.sh

# Start servers
/scripts/07-start-wine-flask.sh

# Keep the script running
tail -f /dev/null