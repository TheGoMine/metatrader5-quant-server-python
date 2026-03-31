#!/bin/bash

source /scripts/02-common.sh

log_message "RUNNING" "07-start-wine-flask.sh"

log_message "INFO" "Starting Flask server in Wine environment..."

# Run the Flask app using Wine's Python
# wine python -m debugpy --listen 0.0.0.0:5678 /app/app.py &
$wine_executable python /app/app.py >> /var/log/mt5_setup.log 2>&1 &

FLASK_PID=$!

# Give the server a generous window to bind port 5001.
for i in $(seq 1 120); do
    if nc -z 127.0.0.1 "${MT5_API_PORT:-5001}" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Check if the Flask server is running
if ps -p $FLASK_PID > /dev/null && nc -z 127.0.0.1 "${MT5_API_PORT:-5001}" >/dev/null 2>&1; then
    log_message "INFO" "Flask server in Wine started successfully with PID $FLASK_PID."
else
    # Do not crash startup; Wine/MT5 cold starts can exceed readiness window.
    log_message "WARN" "Flask process started but did not pass readiness check yet."
fi