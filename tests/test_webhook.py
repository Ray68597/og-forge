"""Local test suite for webhook + key provisioning + config endpoint."""
import sys, os, json, hmac, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ["CREEM_WEBHOOK_SECRET"] = "whsec_test_123"
os.environ["OGF_WAITLIST_DB"] = "/tmp/ogf_test.db"
# Explicitly disable checkout so test 2 covers the "payments off" path;
# the code default (live Creem product link) is asserted in test 11.
os.environ["OGF_CHECKOUT_URL"] = ""
if os.path.exists("/tmp/ogf_test.db"):
    os.remove("/tmp/ogf_test.db")

from fastapi.testclient import TestClient
from app.main import app, API_KEYS, DEFAULT_CHECKOUT_URL, _load_api_keys, _provision_key

c = TestClient(app)
SECRET = b"whsec_test_123"


def signed(payload: dict) -> dict:
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
    return {"content": raw, "headers": {"creem-signature": sig, "Content-Type": "application/json"}}


print("=== 1. health ===")
r = c.get("/v1/health")
assert r.status_code == 200 and r.json() == {"status": "ok"}
print("PASS")

print("=== 2. config (no checkout_url) ===")
r = c.get("/v1/config")
assert r.status_code == 200 and r.json()["checkout_url"] is None
print("PASS:", r.json())

print("=== 3. webhook bad signature -> 401 ===")
r = c.post("/v1/webhook/creem", json={"eventType": "checkout.completed"}, headers={"creem-signature": "bad"})
assert r.status_code == 401, (r.status_code, r.text)
print("PASS: rejected")

print("=== 4. webhook checkout.completed -> key granted ===")
payload = {
    "eventType": "checkout.completed",
    "object": {
        "id": "ch_test_001",
        "customer": {"email": "buyer@example.com"},
        "subscription": {"id": "sub_test_001", "status": "active"},
    },
}
r = c.post("/v1/webhook/creem", **signed(payload))
assert r.status_code == 200, (r.status_code, r.text)
assert len(API_KEYS) == 1, API_KEYS
key = list(API_KEYS)[0]
assert key.startswith("ogf_live_")
print("PASS: key =", key[:18] + "...")

print("=== 5. pro key bypasses rate limit ===")
r = c.get("/v1/generate", params={"title": "Pro test"}, headers={"x-api-key": key})
assert r.status_code == 200 and r.headers["x-og-forge-tier"] == "pro", (r.status_code, dict(r.headers))
print("PASS: pro tier, png bytes =", len(r.content))

print("=== 6. idempotent: same subscription -> same key ===")
r = c.post("/v1/webhook/creem", **signed(payload))
assert r.status_code == 200
assert len(API_KEYS) == 1 and list(API_KEYS)[0] == key
print("PASS: idempotent")

print("=== 7. subscription.canceled -> key revoked ===")
cancel = {
    "eventType": "subscription.canceled",
    "object": {
        "customer": {"email": "buyer@example.com"},
        "subscription": {"id": "sub_test_001", "status": "canceled"},
    },
}
r = c.post("/v1/webhook/creem", **signed(cancel))
assert r.status_code == 200
assert len(API_KEYS) == 0, f"expected 0, got {len(API_KEYS)}"
print("PASS: revoked")

print("=== 8. revoked key falls back to free tier ===")
r = c.get("/v1/generate", params={"title": "free again"}, headers={"x-api-key": key})
assert r.status_code == 200 and r.headers["x-og-forge-tier"] == "free"
print("PASS: falls back to free")

print("=== 9. keys survive restart (db reload) ===")
_provision_key("buyer2@example.com", "sub_test_002")
API_KEYS.clear()
_load_api_keys()
assert len(API_KEYS) == 1, f"reload got {len(API_KEYS)}"
print("PASS: reload ok")

print("=== 10. unknown event type -> 200 (no retry spam) ===")
r = c.post("/v1/webhook/creem", **signed({"eventType": "refund.created", "object": {}}))
assert r.status_code == 200
print("PASS: ignored gracefully")

print("=== 11. default checkout URL is the live Creem product link ===")
assert DEFAULT_CHECKOUT_URL.startswith("https://creem.io/product/prod_"), DEFAULT_CHECKOUT_URL
print("PASS:", DEFAULT_CHECKOUT_URL)

print("=== 12. /welcome page served ===")
r = c.get("/welcome")
assert r.status_code == 200 and "API key" in r.text, (r.status_code, r.text[:200])
print("PASS: welcome page ok")

print("=== 13. pro key retrieval by subscription_id ===")
r = c.get("/v1/pro/key", params={"subscription_id": "sub_test_002"})
assert r.status_code == 200, (r.status_code, r.text)
assert r.json()["api_key"].startswith("ogf_live_") and r.json()["email"] == "buyer2@example.com"
print("PASS: key delivered for active sub")

print("=== 14. revoked / unknown subscription -> 404, missing param -> 400 ===")
r = c.get("/v1/pro/key", params={"subscription_id": "sub_test_001"})  # revoked in test 7
assert r.status_code == 404, (r.status_code, r.text)
r = c.get("/v1/pro/key", params={"subscription_id": "sub_does_not_exist"})
assert r.status_code == 404
r = c.get("/v1/pro/key")
assert r.status_code == 400
print("PASS: 404 revoked/unknown, 400 missing")

print()
print("ALL 14 TESTS PASSED")
