"""Local test suite for waitlist auto-restore (ephemeral disk recovery)."""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ["OGF_WAITLIST_DB"] = "/tmp/ogf_restore_test.db"
os.environ["OGF_GH_TOKEN"] = "t"
os.environ["OGF_BACKUP_REPO"] = "Ray68597/og-forge"
os.environ["OGF_BACKUP_KEY"] = "k"
if os.path.exists("/tmp/ogf_restore_test.db"):
    os.remove("/tmp/ogf_restore_test.db")

from app import main
from app.main import _restore_waitlist_if_empty, _waitlist_db

BACKUP_CSV = "alice@example.com,2026-08-19 10:00:00\nbob@test.io,2026-08-19 11:00:00\nnot-an-email,x\n,2026-08-19 12:00:00\n"

print("=== 1. empty DB + backup available -> restore ===")
main._fetch_latest_backup = lambda: BACKUP_CSV
_restore_waitlist_if_empty()
conn = _waitlist_db()
rows = conn.execute("SELECT email, created_at FROM waitlist ORDER BY id").fetchall()
conn.close()
assert len(rows) == 2, rows  # corrupt lines ignored
assert rows[0] == ("alice@example.com", "2026-08-19 10:00:00"), rows
assert rows[1] == ("bob@test.io", "2026-08-19 11:00:00"), rows
print("PASS: restored", len(rows), "valid row(s), corrupt lines ignored")

print("=== 2. non-empty DB -> skip (no fetch) ===")
called = []
main._fetch_latest_backup = lambda: called.append(1) or "should@not.com,2026-01-01 00:00:00"
_restore_waitlist_if_empty()
conn = _waitlist_db()
n = conn.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0]
conn.close()
assert not called and n == 2, (called, n)
print("PASS: skipped fetch, still", n, "rows")

print("=== 3. fetch fails -> start empty, no crash ===")
for f in ["/tmp/ogf_restore_test.db", "/tmp/ogf_restore_test.db-journal"]:
    if os.path.exists(f):
        os.remove(f)
main._fetch_latest_backup = lambda: None
_restore_waitlist_if_empty()
conn = _waitlist_db()
n = conn.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0]
conn.close()
assert n == 0
print("PASS: graceful empty start")

print("=== 4. idempotent: restore twice -> no duplicates ===")
main._fetch_latest_backup = lambda: BACKUP_CSV
_restore_waitlist_if_empty()
_restore_waitlist_if_empty()
conn = _waitlist_db()
n = conn.execute("SELECT COUNT(*) FROM waitlist").fetchone()[0]
conn.close()
assert n == 2, n
print("PASS:", n, "rows after double restore")

print("=== 5. restored data flows into next backup CSV ===")
csv = main._waitlist_csv()
assert "alice@example.com" in csv and "bob@test.io" in csv
print("PASS: backup loop will re-push restored rows")

print("\nALL RESTORE TESTS PASSED")
