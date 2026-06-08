#!/bin/bash
# Install auto-test watch script — re-runs the ground-truth harness whenever
# any engine module is modified. Logs to /tmp/auto_test_watch.log with
# timestamps and result summaries.
#
# After installing, you (or Claude on a future paste) just edit a file in
# /home/roofmeasure/engine/roofmeasure/ and the harness re-runs automatically.
# Tail the log to see results live.

set -e

# Ensure inotify-tools is available
if ! command -v inotifywait > /dev/null 2>&1; then
    echo "Installing inotify-tools..."
    sudo apt-get install -y inotify-tools 2>&1 | tail -3
fi

# Write the watch script
sudo tee /usr/local/bin/roofmeasure-auto-test.sh > /dev/null << 'WATCHEOF'
#!/bin/bash
# Re-runs ground-truth harness on any change to engine modules.
# Logs to /tmp/auto_test_watch.log

ENGINE_DIR=/home/roofmeasure/engine/roofmeasure
TEST_SCRIPT=/home/roofmeasure/engine/tests/ground_truth_harness.py
GT_CSV=/home/roofmeasure/engine/tests/ground_truth.csv
LOG=/tmp/auto_test_watch.log
VENV=/home/roofmeasure/engine/venv/bin/python

mkdir -p "$(dirname "$LOG")"
echo "[$(date -u +%FT%TZ)] auto-test watcher started" >> "$LOG"

# Run an initial harness
run_harness() {
    local trigger="$1"
    echo "" >> "$LOG"
    echo "================================" >> "$LOG"
    echo "[$(date -u +%FT%TZ)] HARNESS RUN ($trigger)" >> "$LOG"
    echo "================================" >> "$LOG"
    sudo -E "$VENV" "$TEST_SCRIPT" "$GT_CSV" 2>&1 | tee -a "$LOG" | tail -30
    echo "[$(date -u +%FT%TZ)] run complete" >> "$LOG"
}

# Run once at startup
run_harness "startup"

# Watch for changes
inotifywait -m -e modify,create,move "$ENGINE_DIR" --format '%f %e' 2>/dev/null | \
while read file event; do
    # Only react to .py changes
    case "$file" in
        *.py)
            # Debounce: wait 5s for batched edits to settle, then run
            sleep 5
            # Drain any queued events from the same edit batch
            run_harness "$file changed"
            ;;
    esac
done
WATCHEOF
sudo chmod +x /usr/local/bin/roofmeasure-auto-test.sh

# Write a systemd service unit
sudo tee /etc/systemd/system/roofmeasure-auto-test.service > /dev/null << 'SVCEOF'
[Unit]
Description=RoofMeasure ground-truth auto-test watcher
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/roofmeasure-auto-test.sh
Restart=on-failure
RestartSec=10
StandardOutput=append:/tmp/auto_test_watch.log
StandardError=append:/tmp/auto_test_watch.log

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable roofmeasure-auto-test.service
sudo systemctl restart roofmeasure-auto-test.service

echo
echo "================================================================"
echo "  AUTO-TEST WATCHER INSTALLED"
echo "================================================================"
echo
echo "Service status:"
sudo systemctl status roofmeasure-auto-test.service --no-pager | head -10
echo
echo "Live log: tail -f /tmp/auto_test_watch.log"
echo "Stop watcher: sudo systemctl stop roofmeasure-auto-test"
echo
echo "From now on, any edit to /home/roofmeasure/engine/roofmeasure/*.py"
echo "will re-trigger the full 11-address ground-truth harness within ~5s."
echo
echo "An initial harness run was triggered — wait ~10 min then check the log."
