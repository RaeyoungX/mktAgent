# mktAgent 系统文档

## 概述

mktAgent 是一个运行在 macOS 上的营销自动化桌面应用。用户输入产品网址，系统自动分析产品、制定渠道策略、生成内容、管理账号暖号、分发内容并收集反馈。

整体架构是 **CMO 主 agent 负责调度，6 个子 agent 各司其职**，所有浏览器操作通过 AppleScript 注入真实 Chrome 完成（不用官方 API，不用 Playwright，不会被平台检测）。

---

## 架构图

```
用户 (NiceGUI 桌面端)
        │
        ▼
   CMO Agent          ← 主调度器，决定运行哪些 agent、顺序
        │
   ┌────┴────────────────────────────────────┐
   │                                         │
   ▼                                         ▼
Phase 1: ANALYZE                      Phase 2: OPERATE
  ├─ ProductAnalysisAgent               ├─ AccountCultivationAgent
  ├─ ChannelAgent                       └─ DistributionAgent
  └─ ContentAgent
                                       Phase 3: MEASURE
                                         └─ FeedbackAgent
                                         
                                       Phase 4: ADJUST
                                         └─ CMO 根据反馈调整策略
```

---

## 四个运行阶段

### Phase 1 — ANALYZE（分析）
1. **ProductAnalysisAgent** 抓取产品页面，提取结构化信息
2. **ChannelAgent** 根据产品分析制定渠道策略（选平台、找 subreddit）
3. **ContentAgent** 批量生成各平台内容草稿

### Phase 2 — OPERATE（执行）
1. **AccountCultivationAgent** 检查账号状态，执行暖号流程
2. **DistributionAgent** 发布用户已审批的内容

### Phase 3 — MEASURE（度量）
- **FeedbackAgent** 采集已发布内容的互动数据（点赞、评论、点击）

### Phase 4 — ADJUST（调整）
- CMO 根据反馈报告调整下一轮策略

---

## 六个子 Agent

### 1. ProductAnalysisAgent
**文件**: `agents/product_analysis_agent.py`

**职责**: 抓取产品页面，提取结构化营销洞察

**流程**:
```
DB 有历史分析？
  ├─ 是 → 直接复用（不重新爬）
  └─ 否 → requests + BeautifulSoup 抓 HTML
            内容 < 200 字？→ 切换 Playwright 重抓
            ↓
           Claude 结构化提取（tool_use 强制输出）
            ↓
           存入 DB
```

**输出 schema** (`schemas/product.py`):
- `product_name`, `description`
- `key_features`: 核心功能列表
- `unique_selling_points`: 真正差异化卖点
- `target_audience`: 主/次受众 + 人口特征 + 心理特征
- `pain_points_solved`: 解决的用户痛点
- `competitive_positioning`: 市场定位
- `pricing_tier`: free / freemium / paid / enterprise
- `content_themes`: 5-8 个适合发内容的主题

---

### 2. ChannelAgent
**文件**: `agents/channel_agent.py`

**职责**: 制定各平台渠道策略，发现真实 subreddit

**Subreddit 发现流程**（解决 Claude 乱猜的问题）:
```
用户手动指定了 subreddit？
  ├─ 是 → 直接用
  └─ 否 → Claude 根据产品信息生成 4-6 个搜索关键词
            ↓
           对每个关键词请求 Reddit 公开 API
           GET /subreddits/search.json?q=keyword
            ↓
           过滤订阅数 < 5000 的社区
           按订阅数排序，取 top 15
            ↓
           Claude 从真实列表里选最相关的（不能自己发明）
```

**输出**: 各平台的发帖频率、语调、内容格式、最佳时间、subreddit、hashtag

---

### 3. ContentAgent
**文件**: `agents/content_agent.py`

**职责**: 根据渠道策略批量生成各平台内容草稿

**内容状态机**:
```
draft → approved → posted
```
用户在「内容审批」Tab 审核后才能发布，防止直接发出未经审核的内容。

---

### 4. AccountCultivationAgent
**文件**: `agents/account_cultivation_agent.py`

**职责**: 管理账号暖号，根据账号状态决定行为

**状态机**（以 Reddit 为例）:
```
lurk    (账号 < 3 天)   → 只浏览，不互动
warmup  (karma < 600)   → 评论非产品相关帖子，积累真实 karma
promo   (karma ≥ 600)   → 可以发产品相关内容
```

---

### 5. DistributionAgent
**文件**: `agents/distribution_agent.py`

**职责**: 将 `status=approved` 的内容发布到对应平台

**关键设计**: 幂等性检查——发布前检查 `post_url` 是否已存在，防止重复发帖

---

### 6. FeedbackAgent
**文件**: `agents/feedback_agent.py`

**职责**: 采集已发布内容的互动指标，生成反馈报告供 CMO 调整策略

---

## 浏览器操作层

**原则**: 所有平台操作通过 AppleScript 控制真实 Chrome，利用已登录的 session 直接调用平台 JSON API，完全不可被检测。

### chrome.py — 基础工具
- `chrome_js(js)`: 在 Chrome 当前 Tab 执行 JS
- `chrome_js_fetch(js, result_prefix)`: 执行 JS 并通过 `document.title` 轮询结果
- `chrome_nav(url)`: 导航到指定 URL

### 各平台工具

| 文件 | 平台 | 核心方法 |
|------|------|---------|
| `reddit_chrome.py` | Reddit | `get_me()`, `submit_post()`, `post_comment()`, `search_subreddits()` |
| `twitter_chrome.py` | Twitter/X | `tweet()` — 调用 GraphQL CreateTweet |
| `linkedin_chrome.py` | LinkedIn | `create_post()` — 调用 Voyager `/ugcPosts` |
| `xhs_sdk.py` | 小红书 | `search_notes()` — xhs SDK |

### JS 注入原理
```python
# Python 用 osascript 把 JS 注入 Chrome
js = 'fetch("/api/me.json", {credentials: "include"}).then(...document.title = "ME:" + JSON.stringify(result))'
subprocess.run(['osascript', '-e', f'tell app "Chrome" to execute front window\'s active tab javascript "{js}"'])

# 轮询 document.title 拿结果
while time.time() < deadline:
    title = get_chrome_title()
    if title.startswith("ME:"):
        return json.loads(title[3:])
```

---

## 数据层

**数据库**: SQLite (`db/mktAgent.db`)，SQLAlchemy 2.0

### 主要表

| 表 | 说明 |
|----|------|
| `products` | 产品信息，包含各平台配置（JSON） |
| `product_analyses` | 产品分析结果，多版本追加 |
| `channel_strategies` | 渠道策略，有版本号 |
| `content_pieces` | 内容草稿，status: draft/approved/posted |
| `post_metrics` | 发布后的互动指标 |
| `account_health` | 各账号的 karma、状态、最后活跃时间 |
| `session_logs` | agent 运行日志 |

---

## GUI 层

**框架**: NiceGUI 3.x，`native=True` 以 macOS 原生窗口运行

### 页面结构

```
/ (首页)
  └─ 产品列表 + 添加产品

/product/{id}
  ├─ ▶ 运行 Agent    — 勾选要运行的 agent，实时日志流
  ├─ 📡 渠道策略     — 查看产品分析和渠道策略
  ├─ 📝 内容审批     — 审批/拒绝内容草稿
  ├─ ⚙ 平台配置     — 启用平台、配置账号和 subreddit
  ├─ 👤 账号健康     — 查看各账号状态和 karma
  └─ 📊 数据看板     — 各平台发布数量和互动数据
```

### 关键技术点

**非阻塞执行**: agent 运行在线程池，不阻塞 UI
```python
await run.io_bound(_sync_run)
```

**实时日志流**:
```python
# QueueLogHandler 把日志推入队列
# ui.timer(0.15) 每 150ms 把队列内容刷到 ui.log
```

---

## 环境配置

```
ANTHROPIC_API_KEY=sk-ant-...   # Claude API，用 load_dotenv(override=True)
```

`override=True` 是必须的，否则 shell 里的空 `ANTHROPIC_API_KEY=""` 会阻止 dotenv 覆盖。

### 运行

```bash
cd /Users/rae/mktAgent
python3 app.py          # 启动桌面端，访问 http://127.0.0.1:8000
python3 scheduler.py    # 可选：定时自动运行
```

---

## 设计原则

1. **不用官方 API** — 全部通过真实浏览器操作，规避平台检测和 API 审核门槛
2. **人工审批** — 内容必须经过 `draft → approved` 才能发布
3. **幂等性** — 每次运行前检查 DB，有历史数据就复用，防止重复操作
4. **真实数据驱动** — subreddit 从 Reddit 真实搜索，不靠 Claude 凭空生成
5. **版本追溯** — 产品分析、渠道策略都有版本号，可以对比历史
