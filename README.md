# mktAgent

A CMO-orchestrated multi-agent marketing automation system for macOS. Input any product URL — the system analyzes it, builds a channel strategy, generates platform-specific content, manages account warmup, distributes posts, and measures feedback.

All browser interactions use **AppleScript → real Chrome** (same-origin JS injection). No platform APIs. No Playwright. Undetectable.

![Python](https://img.shields.io/badge/Python-3.9+-blue) ![NiceGUI](https://img.shields.io/badge/NiceGUI-3.x-green) ![Claude](https://img.shields.io/badge/Claude-Sonnet-orange) ![Platform](https://img.shields.io/badge/Platform-macOS-lightgrey)

---

## How it works

```
Product URL
    │
    ▼
CMO Agent (orchestrator)
    ├── ProductAnalysisAgent  → scrape + Claude structured extraction
    ├── ChannelAgent          → Reddit search + Claude strategy
    ├── ContentAgent          → platform-specific content drafts
    ├── AccountCultivationAgent → karma-aware warmup state machine
    ├── DistributionAgent     → post approved content via Chrome
    └── FeedbackAgent         → collect metrics, inform next cycle
```

**Content flow**: `draft → approved → posted`  
Content must be manually approved in the GUI before it goes live.

---

## Platforms

| Platform | Method |
|----------|--------|
| Reddit | Chrome JS → `/api/submit`, `/api/comment` |
| Twitter/X | Chrome JS → GraphQL `CreateTweet` |
| LinkedIn | Chrome JS → Voyager `/ugcPosts` |
| 小红书 (XHS) | xhs SDK + computer-use instructions |

---

## Agent Architecture

### CMO Agent
Top-level orchestrator. Runs 4 phases: **ANALYZE → OPERATE → MEASURE → ADJUST**. Decides which sub-agents to invoke and in what order.

### ProductAnalysisAgent
Scrapes the product URL with `requests` + BeautifulSoup (Playwright fallback for JS-rendered pages). Sends page text to Claude with a structured schema — extracts features, USPs, target audience, pain points, content themes, and pricing tier. Caches results in DB; won't re-scrape if analysis already exists.

### ChannelAgent
Builds per-platform strategy. For Reddit subreddit discovery:
1. Claude generates 4–6 search keywords from the product analysis
2. Queries Reddit's public `/subreddits/search.json` API (no auth needed)
3. Filters out communities with < 5,000 subscribers
4. Claude picks the most relevant ones from the real list — never invents subreddits

### ContentAgent
Generates platform-native content batches using Claude. Respects each platform's culture: Reddit text posts, Twitter threads, LinkedIn thought leadership, XHS Chinese emoji posts.

### AccountCultivationAgent
Karma-aware warmup state machine:
- `lurk` (account < 3 days old) — browse only
- `warmup` (karma < 600) — comment on non-promotional posts
- `promo` (karma ≥ 600) — post product content

### DistributionAgent
Posts `status=approved` content only. Idempotent — checks `post_url` before posting to prevent duplicates.

### FeedbackAgent
Fetches post metrics (score, comments, upvote ratio) and generates a structured feedback report for the next CMO cycle.

---

## Tech Stack

- **LLM**: Claude Sonnet via Anthropic API (`tool_use` forced structured output)
- **GUI**: NiceGUI 3.x, native macOS window (`pywebview`)
- **DB**: SQLite + SQLAlchemy 2.0
- **Browser**: AppleScript → Chrome JS injection (same-origin `fetch` with `credentials: "include"`)
- **Scraping**: requests + BeautifulSoup, Playwright fallback

---

## Setup

**Requirements**: macOS, Python 3.9+, Chrome with active sessions on target platforms

```bash
git clone https://github.com/RaeyoungX/mktAgent.git
cd mktAgent
pip3 install -r requirements.txt

cp .env.example .env
# Add your Anthropic API key to .env
```

`.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```

**Run**:
```bash
python3 app.py
```

Opens at `http://127.0.0.1:8000` and as a native macOS window.

---

## Usage

1. Click **+ 添加产品** → enter product name and URL
2. Go to **⚙ 平台配置** → enable platforms, add account usernames
3. Go to **▶ 运行 Agent** → select agents to run, click Start
4. Go to **📡 渠道策略** → review the generated strategy and subreddits
5. Go to **📝 内容审批** → approve content before it goes live
6. Run **distribution** agent to post approved content

---

## Project Structure

```
mktAgent/
├── app.py                  # NiceGUI desktop app
├── main.py                 # CLI entry point
├── scheduler.py            # APScheduler background runner
├── agents/
│   ├── cmo_agent.py        # Main orchestrator
│   ├── product_analysis_agent.py
│   ├── channel_agent.py
│   ├── content_agent.py
│   ├── account_cultivation_agent.py
│   ├── distribution_agent.py
│   └── feedback_agent.py
├── tools/
│   ├── chrome.py           # AppleScript + JS injection core
│   ├── reddit_chrome.py
│   ├── twitter_chrome.py
│   ├── linkedin_chrome.py
│   ├── xhs_sdk.py
│   └── scraper.py
├── schemas/                # Pydantic models for structured LLM output
├── db/                     # SQLAlchemy models + session management
└── config/                 # Platform and product YAML configs
```

---

## Notes

- `.env` and `db/*.db` are gitignored — never committed
- All LLM calls use `tool_use` with strict Pydantic schemas — no markdown drift
- `load_dotenv(override=True)` is required if your shell has an empty `ANTHROPIC_API_KEY`
