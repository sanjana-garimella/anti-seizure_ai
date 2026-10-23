#!/bin/bash
# DOUBLE-CLICK for a demo. Starts the app on its OWN demo database
# (clinic_data/demo.db), reset to a clean set of synthetic example patients each
# launch, and opens your browser. Log in: doctor / demo1234.
# The real database (clinic_data/clinic.db) and its doctor password are never
# touched; use "Start Clinic.command" for that one.
# Keep this window open while you demo; close it (or press Control-C) to stop.

cd "$(dirname "$0")/aim2_engine" || { echo "Could not find the app folder."; read -r; exit 1; }

# Separate demo database: everything below (seeding, the demo password, the app)
# reads and writes this file only.
export ASM_DB="$PWD/clinic_data/demo.db"
export ASM_DOCTOR_PASSWORD="demo1234"

echo "============================================================"
echo "  Medication Assistant - DEMO"
# Stop any older copy still running on this port so you always get the latest.
lsof -ti tcp:8000 2>/dev/null | xargs kill -9 2>/dev/null
echo "  Loading synthetic example patients..."
# Start each demo from a clean, freshly seeded set (synthetic data only).
# --reset only clears the demo database named in ASM_DB above.
python3 demo_seed.py --reset
echo "  A browser window will open at  http://127.0.0.1:8000/login"
echo "  Log in:   username  doctor    password  demo1234"
echo "  Keep this window open during the demo. Close it to stop."
echo "============================================================"

( sleep 2; open "http://127.0.0.1:8000/login" ) >/dev/null 2>&1 &
exec python3 clinic.py
