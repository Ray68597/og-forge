"""Edge-case fuzz tests: hostile/weird inputs must never 500."""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ["OGF_WAITLIST_DB"] = "/tmp/ogf_fuzz.db"
if os.path.exists("/tmp/ogf_fuzz.db"):
    os.remove("/tmp/ogf_fuzz.db")

from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
fails = []

def check(name, r, allowed=(200, 400, 422, 429)):
    ok = r.status_code in allowed
    status = "PASS" if ok else f"FAIL({r.status_code})"
    if not ok:
        fails.append((name, r.status_code, r.text[:120]))
    print(f"{status}: {name} -> {r.status_code}")

# --- generate endpoint fuzzing ---
print("--- /v1/generate 边界 ---")
check("空 title", c.get("/v1/generate"))
check("超长 title (10k)", c.get("/v1/generate", params={"title": "A" * 10000}))
check("emoji + 中文 + RTL", c.get("/v1/generate", params={"title": "🚀 你好 مرحبا", "subtitle": "mixed 🎨"}))
check("非法颜色值", c.get("/v1/generate", params={"title": "x", "bg_color": "notacolor"}))
check("颜色带 # 前缀", c.get("/v1/generate", params={"title": "x", "bg_color": "#ff0000"}))
check("负数宽度", c.get("/v1/generate", params={"title": "x", "width": -5}))
check("超宽 99999", c.get("/v1/generate", params={"title": "x", "width": 99999}))
check("未知模板", c.get("/v1/generate", params={"title": "x", "template": "nonexistent"}))
check("SQL 注入式 title", c.get("/v1/generate", params={"title": "'; DROP TABLE--"}))
check("XSS 式 title", c.get("/v1/generate", params={"title": "<script>alert(1)</script>"}))
check("换行注入 title", c.get("/v1/generate", params={"title": "line1\nline2"}))
check("全空格 title", c.get("/v1/generate", params={"title": "   "}))

# --- all templates render ---
print("--- 5 个模板全部渲染 ---")
for t in ["gradient", "minimal", "bold", "split", "dark"]:
    check(f"template={t}", c.get("/v1/generate", params={"title": "T", "template": t}))

# --- waitlist fuzzing ---
print("--- /v1/waitlist 边界 ---")
check("合法邮箱", c.post("/v1/waitlist", json={"email": "a@b.com"}))
check("非邮箱字符串", c.post("/v1/waitlist", json={"email": "not-an-email"}))
check("空 email 字段", c.post("/v1/waitlist", json={"email": ""}))
check("无 email 键", c.post("/v1/waitlist", json={}))
check("非 JSON body", c.post("/v1/waitlist", content=b"garbage", headers={"Content-Type": "application/json"}))
check("超长邮箱 300 字符", c.post("/v1/waitlist", json={"email": "a" * 300 + "@b.com"}))
check("大写邮箱转小写", c.post("/v1/waitlist", json={"email": "UPPER@CASE.COM"}))

# --- export 无 key → 404 ---
check("export 无 key", c.get("/v1/waitlist/export"), allowed=(404,))

# --- webhook 未配置 secret → 503 ---
check("webhook 未配置", c.post("/v1/webhook/creem", json={"x": 1}), allowed=(503,))

print()
if fails:
    print(f"❌ {len(fails)} 项失败:")
    for name, code, body in fails:
        print(f"  - {name}: {code} {body}")
    sys.exit(1)
print("✅ 全部边界测试通过（无 500 错误）")
