#!/bin/bash
# Double-click this file to start the Medication Assistant app.
# A browser window opens automatically. Keep the small black window open while
# you use the app; close it (or press Control-C) to stop.

cd "$(dirname "$0")/aim2_engine" || { echo "Could not find the app folder."; read -r; exit 1; }

echo "============================================================"
echo "  Starting the Medication Assistant..."
echo "  A browser window will open at  http://127.0.0.1:8000"
echo ""
echo "  First time only: a one-time doctor password is shown just"
echo "  below. Log in with username 'doctor', then change it under"
echo "  the 'Password' menu."
echo ""
echo "  Keep this window open while you work. Close it to stop."
echo "============================================================"
echo ""

# Stop any older copy still running on this port so you always get the latest
# code (otherwise the stale server keeps serving and edits appear to "not show").
lsof -ti tcp:8000 2>/dev/null | xargs kill -9 2>/dev/null
sleep 1

# Open the browser a couple of seconds after the server starts.
( sleep 2; open "http://127.0.0.1:8000/login" ) >/dev/null 2>&1 &

exec python3 clinic.py
