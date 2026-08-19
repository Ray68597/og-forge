# OG Forge — 部署与变现指南（操作者手册）

> 本文档面向接手此资产的人类操作者。按步骤执行，预计 2-4 小时完成部署上线，之后进入获客阶段。
> **诚实声明：本产品由 AI 代理自主构建。对外宣传时请如实标注。**

## 变现模式（三层收入）

1. **Hosted SaaS**（主要）：免费层 10图/分钟 限流引流，Pro $9/月 无限制（Stripe 收款）
2. **GitHub Sponsors / 开源赞助**：代码 MIT 开源，README 挂赞助链接
3. **定制服务**：企业定制模板/私有部署，$200-500/单（从 issue 和咨询邮件转化）

对标市场：Bannerbear（$60K MRR）、Placid、og-image 服务。此产品差异化 = **极简、自托管友好、无 AI 依赖**。

---

## 第一步：部署（约 30 分钟，成本 $0）

### 方案 A：Fly.io（推荐，有免费额度）

```bash
# 1. 注册 fly.io，安装 CLI
fly launch --name og-forge-你的后缀 --no-deploy
fly deploy
# 2. 绑定域名（在 fly.io 面板加证书）
```

### 方案 B：Railway / Render

连接 GitHub 仓库，选 Docker 部署，平台自动构建。免费额度足够起步。

### 方案 C：自有 VPS

```bash
docker build -t og-forge .
docker run -d -p 80:8000 --restart unless-stopped og-forge
```

### 验证清单

- [ ] `curl https://你的域名/v1/health` 返回 `{"status":"ok"}`
- [ ] 浏览器打开首页，实时预览可用
- [ ] 生成一张图：`/v1/generate?title=Test&template=gradient`

---

## 第二步：收款（约 1 小时）

### Stripe Checkout（推荐）

1. 注册 Stripe（需身份证/银行账户 — 这是 AI 无法代办、必须人工的步骤）
2. 创建产品："OG Forge Pro"，$9/月订阅
3. 创建 Payment Link，替换落地页 Pricing 区按钮链接
4. **API Key 自动发放**：用 Stripe Webhook（`checkout.session.completed`）触发：
   - 简易方案：用 [Lemon Squeezy](https://lemonsqueezy.com) 替代，内置 license key 生成
   - 进阶方案：写 20 行代码接 webhook，生成 `ogf_live_xxx` key 写入 `API_KEYS`

### 落地页修改点

`static/landing.html` 中 Pricing 区的 Pro 卡片加按钮：

```html
<a class="btn btn-primary" href="https://你的-stripe-支付链接">Get Pro — $9/mo</a>
```

---

## 第三步：获客（持续，成本 $0）

按优先级执行：

### 3.1 产品分发站点（第 1 周）

- [ ] 提交 [Product Hunt](https://producthunt.com)（周二/四上午发布最佳）
- [ ] 提交 [Hacker News](https://news.ycombinator.com) Show HN（标题：*Show HN: I built an OG image API that renders in 35ms*）
- [ ] 提交 [dev.to](https://dev.to) 写技术文章（"How I generate social cards without a headless browser"）
- [ ] 提交 [Indie Hackers](https://indiehackers.com)
- [ ] V2EX / 少数派（中文市场）

### 3.2 SEO（第 2 周起）

目标关键词（月搜索量估算）：
- "og image generator" / "open graph image api" — 主战场
- "dynamic og image" / "social card generator"
- "meta tag image generator"

动作：给落地页加博客模块，写 3 篇教程（每篇嵌入 API 用法）。

### 3.3 开源引流（持续）

- [ ] 代码推 GitHub，README 挂 hosted 版链接
- [ ] 开 GitHub Sponsors
- [ ] 每个 star 都是潜在转化；在 issue 区快速响应

### 3.4 集成市场（第 2 月）

- [ ] 写一个 [Vercel OG](https://vercel.com/docs/functions/og-image-generation) 对比页
- [ ] 框架插件：Next.js / Astro / Hugo 的 `<OgImage>` 组件，npm 发布，文档链回 SaaS

---

## 关键指标（每周检查）

| 指标 | 目标（首月） | 工具 |
|---|---|---|
| API 调用量 | 1,000+/天 | 服务器日志 |
| 落地页转化 | >3% | 任意分析工具（Plausible 自托管） |
| Pro 付费 | 10 个（$90 MRR） | Stripe 面板 |
| GitHub stars | 100 | GitHub |

## 定价建议

| 层 | 价格 | 限额 |
|---|---|---|
| Free | $0 | 10 图/分钟/IP，带品牌水印（可选改造点） |
| Pro | $9/月 | 无限制，自定义字体，模板 API |
| Team | $29/月 | 5 seats，协作模板，优先支持 |

> 起步阶段勿加水印 — 先积累用户。达到 500 日调用后再加水印驱动转化。

---

## 常见问题

**Q: 免费层被滥用怎么办？**
A: 当前为内存限流。升级 Redis（Fly.io 免费额度内），或加 Cloudflare（免费）前置防 DDoS。

**Q: 如何证明收入潜力？**
A: 同类验证：Bannerbear $60K MRR（图片生成 API）、Simple Analytics $39K MRR（隐私分析，同为"反巨头"定位）。市场真实存在，关键在执行获客。

**Q: 代码可信吗？**
A: 全部代码 AI 生成后经本地测试（5 模板渲染、限流、长文本压力测试均通过，见 04-logs/session-001.md）。MIT 许可，可自行审计。

## 下一步产品路线（按需求信号触发）

1. **自定义字体上传**（Pro 功能，最常被要求的同类功能）
2. **HTML-to-image 端点**（`POST /v1/html`，覆盖高级用例）
3. **模板市场**（用户创建模板，创作者分成 — 平台化机会）
