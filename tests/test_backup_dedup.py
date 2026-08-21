"""Local tests for backup content-hash dedup (wakes must stay silent)."""
import sys, os, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
DB = "/tmp/ogf_dedup_test.db"
os.environ["OGF_WAITLIST_DB"] = DB
os.environ["OGF_GH_TOKEN"] = "t"
os.environ["OGF_BACKUP_REPO"] = "Ray68597/og-forge"
for f in (DB, DB + "-journal"):
    if os.path.exists(f):
        os.remove(f)

import urllib.request
from cryptography.fernet import Fernet
os.environ["OGF_BACKUP_KEY"] = Fernet.generate_key().decode()
from app import main
from app.main import (
    _push_backup, _waitlist_db, _waitlist_csv,
    _restore_waitlist_if_empty, _get_backup_state,
)

pushes = []


class _FakeResp:
    status = 200
    def read(self): return b"{}"
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _fake_urlopen(req, timeout=None):
    pushes.append(req.full_url.split("/contents/")[1].split("?")[0])
    return _FakeResp()


urllib.request.urlopen = _fake_urlopen


def _add(email):
    conn = _waitlist_db()
    try:
        conn.execute("INSERT OR IGNORE INTO waitlist(email) VALUES (?)", (email,))
        conn.commit()
    finally:
        conn.close()


print("=== 1. first push goes out and stores hash ===")
_add("alice@example.com")
_push_backup()
assert len(pushes) == 1, pushes
assert _get_backup_state("last_csv_sha256") is not None
print("PASS: pushed", pushes[-1])

print("=== 2. wake / no change -> silent (no push) ===")
_push_backup()
_push_backup()
assert len(pushes) == 1, pushes
print("PASS: no redundant push on wake")

print("=== 3. new signup -> push goes out ===")
_add("bob@test.io")
_push_backup()
assert len(pushes) == 2, pushes
print("PASS: pushed on change")

print("=== 4. redeploy (disk wiped) -> restore stashes hash -> silent ===")
for f in (DB, DB + "-journal"):
    if os.path.exists(f):
        os.remove(f)
_backup_csv = "alice@example.com,2026-08-19 10:00:00\nbob@test.io,2026-08-19 11:00:00"
main._fetch_latest_backup = lambda: _backup_csv
_restore_waitlist_if_empty()
_push_backup()
assert len(pushes) == 2, pushes  # restore repopulated identical content -> no push
print("PASS: restore + first cycle after redeploy stay silent")

print("=== 5. backup with corrupt line -> restored CSV differs -> self-heal push ===")
for f in (DB, DB + "-journal"):
    if os.path.exists(f):
        os.remove(f)
main._fetch_latest_backup = lambda: _backup_csv + "\nnot-an-email,x"
_restore_waitlist_if_empty()
_push_backup()
assert len(pushes) == 3, pushes
print("PASS: cleaned snapshot re-pushed once, then stable")
_push_backup()
assert len(pushes) == 3, pushes
print("PASS: stable after self-heal")

print("\nALL DEDUP TESTS PASSED")
