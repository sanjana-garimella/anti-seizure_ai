#!/usr/bin/env python3
"""
reset_doctor_pw.py - reset the clinic app's 'doctor' login password.

You type the new password (twice); it is never printed, logged, or stored in
plain text - only the salted PBKDF2 hash (clinic_store.hash_pw) is saved.

Same safe DB procedure as the other tools/: copy the DB off-mount with
sqlite3.Connection.backup(), update there, PRAGMA integrity_check, timestamped
backup, then swap in with an atomic rename.

USAGE
    cd aim2_engine
    python3 tools/reset_doctor_pw.py
"""
import getpass
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, here)
from clinic_store import hash_pw  # noqa: E402


def main() -> int:
    live = os.path.join(here, "clinic_data", "clinic.db")
    if not os.path.isfile(live):
        print(f"no database at {live}")
        return 1
    pw = getpass.getpass("New doctor password: ")
    if len(pw) < 8:
        print("Too short (use at least 8 characters). Nothing changed.")
        return 1
    if getpass.getpass("Type it again: ") != pw:
        print("The two entries didn't match. Nothing changed.")
        return 1

    work = os.path.join(tempfile.mkdtemp(prefix="pwreset_"), "clinic.db")   # OFF the mount
    src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    dst = sqlite3.connect(work)
    src.backup(dst)
    src.close()
    salt, h = hash_pw(pw)
    n = dst.execute("UPDATE users SET pw_salt=?, pw_hash=? WHERE username='doctor'",
                    (salt, h)).rowcount
    dst.commit()
    ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    dst.close()
    if n != 1 or ok != "ok":
        print(f"Not applied (users updated={n}, integrity={ok}). Nothing changed.")
        return 1

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(live, f"{live}.bak-{ts}")
    staged = f"{live}.new-{ts}"
    shutil.copy2(work, staged)
    os.replace(staged, live)
    print(f"Done. Log in as 'doctor' with your new password. Backup: clinic.db.bak-{ts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
