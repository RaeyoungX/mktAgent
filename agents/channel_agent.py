"""ChannelAgent: determines per-platform strategy based on product analysis."""

import logging
from datetime import datetime
from sqlalchemy.orm import Session

from agents.base_agent import BaseAgent
from schemas.product import ProductAnalysis
from schemas.campaign import ChannelStrategy
from schemas.metrics import FeedbackReport

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a CMO-level channel strategist.
Given a product analysis (and optionally a feedback report), decide:
1. Which platforms to prioritize and why
2. Posting cadence per platform
3. Content formats that fit each platform's culture
4. Tone adjustments per platform
5. Reddit-specific subreddits — you will be given REAL subreddits found via Reddit search. Pick the most relevant ones from that list only. Do not invent subreddits.
6. Platform-appropriate hashtags (Reddit: none; XHS: Chinese hashtags; LinkedIn/Twitter: English)

Platform rules:
- reddit: No hashtags. Text-first. Subreddit fit is critical. Requires warmup for new accounts.
- xhs: Chinese content preferred. Emojis. Hashtags in Chinese. Visual-first.
- twitter: 280 chars or threads. Conversational. Hashtags sparingly (1-2 max).
- linkedin: Professional tone. Thought leadership. 3-5 posts/week max.
- tiktok/youtube/reels: Script-based. Visual hooks. Not suitable for immediate automation.

Set tiktok/youtube/reels enabled=false unless the product is highly visual/consumer.
Prioritize platforms where the target audience actually spends time.
"""


class ChannelAgent(BaseAgent):
    name = "channel"

    def run(
        self,
        product: ProductAnalysis,
        campaign_id: str,
        campaign_config: dict,
        feedback: FeedbackReport = None,
        previous_version: int = 0,
    ) -> ChannelStrategy:
        logger.info("[%s] Building channel strategy for %s", self.name, product.product_name)

        enabled_platforms = [
            p for p, cfg in campaign_config.get("platforms", {}).items()
            if cfg.get("enabled", False)
        ]

        user_msg = (
            f"Product: {product.product_name}\n"
            f"Description: {product.description}\n"
            f"Target audience: {product.target_audience.primary}\n"
            f"Pain points: {', '.join(product.pain_points_solved[:3])}\n"
            f"Content themes: {', '.join(product.content_themes)}\n"
            f"Pricing: {product.pricing_tier}\n"
            f"Enabled platforms (user-configured): {', '.join(enabled_platforms)}\n"
        )

        if feedback:
            user_msg += (
                f"\nPrevious feedback:\n"
                f"What worked: {', '.join(feedback.what_worked[:3])}\n"
                f"Recommendations: {', '.join(feedback.recommendations[:3])}\n"
            )

        # Reddit subreddit discovery
        reddit_config = campaign_config.get("platforms", {}).get("reddit", {})
        user_subreddits = reddit_config.get("target_subreddits", [])
        if user_subreddits:
            # User explicitly specified — trust them
            user_msg += f"\nReddit target subreddits (user-specified, use these): {', '.join(user_subreddits)}\n"
        elif "reddit" in enabled_platforms:
            # Search Reddit for real subreddits based on product keywords
            real_subreddits = self._discover_subreddits(product)
            if real_subreddits:
                lines = "\n".join(
                    f"  r/{s['name']} — {s['subscribers']:,} subscribers — {s['description']}"
                    for s in real_subreddits
                )
                user_msg += f"\nReal subreddits found via Reddit search (pick the most relevant, only from this list):\n{lines}\n"
            else:
                user_msg += "\nReddit search unavailable — use your best judgment for subreddits.\n"

        strategy = self.call_llm(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            output_model=ChannelStrategy,
            max_tokens=3000,
        )

        strategy.campaign_id = campaign_id
        strategy.version = previous_version + 1
        strategy.created_at = datetime.utcnow()

        self._save(campaign_id, strategy)
        self.log_session(campaign_id, "channel_strategy", {"version": strategy.version})
        logger.info("[%s] Strategy v%d created, %d platforms", self.name, strategy.version, len(strategy.enabled_platforms()))
        return strategy

    def _get_search_keywords(self, product: ProductAnalysis) -> list[str]:
        """Ask Claude to generate the best Reddit search keywords for this product."""
        from pydantic import BaseModel

        class Keywords(BaseModel):
            keywords: list[str]  # 4-6 search terms

        result = self.call_llm(
            system="You are a Reddit community researcher.",
            messages=[{
                "role": "user",
                "content": (
                    f"Product: {product.product_name}\n"
                    f"Description: {product.description}\n"
                    f"Target audience: {product.target_audience.primary}\n"
                    f"Pain points: {', '.join(product.pain_points_solved[:4])}\n\n"
                    "Generate 4-6 short search keywords to find the most relevant Reddit communities "
                    "where this product's target audience hangs out. "
                    "Think about what topics, hobbies, or problems they discuss on Reddit. "
                    "Return only the keywords list — no subreddit names, no r/ prefix."
                ),
            }],
            output_model=Keywords,
            max_tokens=200,
        )
        return result.keywords

    def _score_subreddits(self, candidates: list[dict], product: ProductAnalysis, historical_notes: list[dict] = None) -> list[dict]:
        """Ask Claude to score each candidate subreddit 0-10 for relevance to the product."""
        from pydantic import BaseModel

        class SubredditScore(BaseModel):
            name: str
            score: int  # 0-10

        class ScoreOutput(BaseModel):
            scores: list[SubredditScore]

        lines = "\n".join(
            f"r/{s['name']} ({s['subscribers']:,} subs) — {s['description']}"
            for s in candidates
        )

        history_block = ""
        if historical_notes:
            history_lines = "\n".join(
                f"  r/{h['subreddit']}: {h['notes']}"
                for h in historical_notes
            )
            history_block = f"\nHistorical performance notes from previous runs:\n{history_lines}\nUse these to boost scores for proven subreddits.\n"

        result = self.call_llm(
            system="You are a Reddit community analyst. Score each subreddit's relevance to a product strictly and honestly.",
            messages=[{
                "role": "user",
                "content": (
                    f"Product: {product.product_name}\n"
                    f"Description: {product.description}\n"
                    f"Target audience: {product.target_audience.primary}\n"
                    f"Pain points: {', '.join(product.pain_points_solved[:3])}\n"
                    f"{history_block}\n"
                    f"Score each subreddit 0-10 for how relevant it is for promoting this product:\n"
                    f"- 9-10: community is exactly about this product's topic, very high chance of receptive audience\n"
                    f"- 7-8: community is related and audience would care\n"
                    f"- 5-6: tangentially related, audience might care\n"
                    f"- 0-4: not relevant, posting here would feel off-topic or spammy\n\n"
                    f"Subreddits to score:\n{lines}"
                ),
            }],
            output_model=ScoreOutput,
            max_tokens=800,
        )

        # Claude sometimes returns names with r/ prefix — strip it
        score_map = {s.name.lstrip("r/").lower(): s.score for s in result.scores}
        for s in candidates:
            s["relevance_score"] = score_map.get(s["name"].lower(), 0)

        # Keep only score >= 7, sort by score desc then subscribers desc
        filtered = [s for s in candidates if s["relevance_score"] >= 7]
        filtered.sort(key=lambda x: (x["relevance_score"], x["subscribers"]), reverse=True)
        logger.info("[channel] Scored %d candidates, %d passed relevance filter (>=7)", len(candidates), len(filtered))

        # Record selected subreddits to FTS memory
        from db.database import upsert_subreddit_memory
        for s in filtered:
            notes = f"score={s['relevance_score']} subs={s['subscribers']} desc={s['description']}"
            try:
                upsert_subreddit_memory(product.product_name, s["name"], notes)
            except Exception as e:
                logger.warning("[channel] Failed to upsert subreddit memory for r/%s: %s", s["name"], e)

        return filtered

    def _discover_subreddits(self, product: ProductAnalysis) -> list[dict]:
        """Search Reddit public API for real subreddits, then score for relevance."""
        import requests
        from db.database import search_subreddit_memory

        try:
            keywords = self._get_search_keywords(product)
        except Exception as e:
            logger.warning("[channel] keyword generation failed: %s", e)
            keywords = product.content_themes[:4]

        logger.info("[channel] Searching subreddits for keywords: %s", keywords)

        # Prepend historical FTS memory to help Claude make better choices
        historical_notes = []
        for kw in keywords[:3]:
            try:
                hits = search_subreddit_memory(product.product_name, kw, limit=3)
                historical_notes.extend(hits)
            except Exception as e:
                logger.warning("[channel] FTS search failed for '%s': %s", kw, e)

        if historical_notes:
            logger.info("[channel] Found %d historical subreddit notes from FTS memory", len(historical_notes))

        headers = {"User-Agent": "mktAgent/1.0 (community research)"}
        seen: set[str] = set()
        results: list[dict] = []

        for kw in keywords[:5]:
            try:
                resp = requests.get(
                    "https://www.reddit.com/subreddits/search.json",
                    params={"q": kw, "sort": "relevance", "limit": 10},
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue
                for child in resp.json().get("data", {}).get("children", []):
                    s = child.get("data", {})
                    name = s.get("display_name", "")
                    subs = s.get("subscribers") or 0
                    if name and name not in seen and subs >= 5000:
                        seen.add(name)
                        results.append({
                            "name": name,
                            "title": s.get("title", ""),
                            "subscribers": subs,
                            "active_user_count": s.get("active_user_count") or 0,
                            "description": (s.get("public_description") or "")[:150],
                        })
            except Exception as e:
                logger.warning("[channel] subreddit search failed for '%s': %s", kw, e)

        logger.info("[channel] Found %d candidate subreddits, scoring relevance...", len(results))

        if not results:
            return []

        # Score and filter by relevance
        return self._score_subreddits(results, product, historical_notes=historical_notes)

    def _save(self, campaign_id: str, strategy: ChannelStrategy):
        from db.models import ChannelStrategy as DBStrategy
        row = DBStrategy(
            campaign_id=campaign_id,
            strategy_json=strategy.model_dump(mode="json"),
            version=strategy.version,
        )
        self.db.add(row)
        self.db.commit()
