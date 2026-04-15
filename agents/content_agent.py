"""ContentAgent: generates platform-specific content batches."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from sqlalchemy.orm import Session

from agents.base_agent import BaseAgent
from schemas.product import ProductAnalysis
from schemas.campaign import ChannelStrategy, PlatformStrategy
from schemas.content import ContentBatch, ContentPiece

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You write social content that blends in — not content that screams "marketing".

Your job: read the real examples from the community, absorb their voice, then write new posts that sound like they came from the same people.

Universal rules (never break):
- Simple everyday words. Short sentences. No fancy vocabulary.
- No em-dashes, no "As someone who...", no "In today's world", no "It's worth noting"
- No exclamation marks unless extremely natural
- Don't start with "I" or "So," or "Just wanted to"
- Sounds like a real person typed it quickly, not a content team
- Typos and informal grammar are fine (dont, its, wanna, gonna, tbh, ngl)
- Never sound like an ad, a press release, or a LinkedIn influencer

Platform specifics:
- reddit: match the exact tone of the example posts provided. titles: specific and relatable, not clickbait. body: conversational, 2-5 sentences max for simple posts.
- xhs: Chinese. emojis throughout. punchy first line. short paragraphs. Chinese hashtags at end.
- twitter: 280 chars or thread (max 6). casual. max 2 hashtags.
- linkedin: professional but personal. hook first. 3-5 short paragraphs. end with observation.

Warmup content: NO product mention. Just be a real community member.
Promo content: mention product once, naturally, only if it genuinely fits — never lead with it.
"""


def _pieces_schema() -> dict:
    """Schema for a list of ContentPiece dicts."""
    return {
        "type": "object",
        "properties": {
            "pieces": {
                "type": "array",
                "items": ContentPiece.model_json_schema(),
            }
        },
        "required": ["pieces"],
    }


class ContentAgent(BaseAgent):
    name = "content"

    def run(
        self,
        strategy: ChannelStrategy,
        product: ProductAnalysis,
        campaign_id: str,
        week_number: int = 1,
        account_phases: dict[str, str] = None,  # platform → warmup_phase
    ) -> ContentBatch:
        """Generate a full ContentBatch for all enabled platforms."""
        logger.info("[%s] Generating content batch week %d", self.name, week_number)
        account_phases = account_phases or {}
        all_pieces: list[ContentPiece] = []

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self._generate_for_platform, ps, product, campaign_id, week_number, account_phases.get(ps.platform, "warmup")): ps.platform
                for ps in strategy.enabled_platforms()
            }
            for future in as_completed(futures):
                platform = futures[future]
                try:
                    pieces = future.result()
                    all_pieces.extend(pieces)
                except Exception as e:
                    logger.error("[content] Platform %s failed: %s", platform, e)

        batch = ContentBatch(
            campaign_id=campaign_id,
            pieces=all_pieces,
            week_number=week_number,
        )

        self._save(batch)
        self.log_session(campaign_id, "content_batch", {"total_pieces": batch.total_pieces, "week": week_number})
        logger.info("[%s] Generated %d pieces across %d platforms", self.name, batch.total_pieces, len(strategy.enabled_platforms()))
        return batch

    def _generate_for_platform(
        self,
        platform_strategy: PlatformStrategy,
        product: ProductAnalysis,
        campaign_id: str,
        week_number: int,
        warmup_phase: str,
    ) -> list[ContentPiece]:
        platform = platform_strategy.platform
        in_warmup = warmup_phase in ("lurk", "warmup")
        in_promo = warmup_phase == "promo"

        # How many pieces to generate based on frequency
        freq_map = {"daily": 7, "3x_per_week": 3, "2x_per_week": 2, "1x_per_week": 1}
        count = freq_map.get(platform_strategy.posting_frequency, 2)

        # Load recently used themes to enforce diversity
        from db.models import UsedContentTheme
        cutoff = datetime.utcnow().replace(day=1)  # current month
        used_themes = [
            row.theme for row in self.db.query(UsedContentTheme).filter(
                UsedContentTheme.campaign_id == campaign_id,
                UsedContentTheme.platform == platform,
                UsedContentTheme.used_at >= cutoff,
            ).all()
        ]

        user_msg = (
            f"Generate {count} content pieces for {platform}.\n\n"
            f"Product: {product.product_name} — {product.description}\n"
            f"Target audience: {product.target_audience.primary}\n"
            f"Key pain points: {', '.join(product.pain_points_solved[:3])}\n"
            f"Content themes to draw from: {', '.join(product.content_themes)}\n"
            f"Tone: {platform_strategy.tone}\n"
            f"Account warmup phase: {warmup_phase}\n"
            f"Product mention allowed: {'YES — mention naturally, never lead with it' if in_promo else 'NO — warmup content only, no product mention'}\n"
        )
        if used_themes:
            user_msg += f"\nThemes already used this month (avoid repeating these angles): {', '.join(set(used_themes))}\n"
            user_msg += "Each piece must cover a different angle from the ones above.\n"

        if platform == "reddit":
            subreddits = platform_strategy.subreddits or ["r/travel"]
            user_msg += f"Target subreddits: {', '.join(subreddits)}\n"
            user_msg += "Rotate pieces across different subreddits.\n"

            # Fetch real posts as style reference
            examples = self._fetch_reddit_examples(subreddits)
            if examples:
                user_msg += "\nReal posts from these communities (study their voice and style):\n"
                for ex in examples:
                    user_msg += f"---\nTitle: {ex['title']}\n"
                    if ex.get("body"):
                        user_msg += f"Body: {ex['body']}\n"
                user_msg += "---\nMatch this community's tone exactly. Do NOT copy — write new content in the same voice.\n"

        if platform == "xhs":
            user_msg += "Write in Chinese. Include relevant Chinese hashtags.\n"

        # Minimal schema — only fields Claude can actually fill
        from pydantic import BaseModel
        from typing import Optional

        class LLMPiece(BaseModel):
            content_type: str               # post / comment / thread / note
            title: Optional[str] = None
            body: str
            hashtags: list[str] = []
            media_prompts: list[str] = []
            target_subreddit: Optional[str] = None

        class PiecesOutput(BaseModel):
            pieces: list[LLMPiece]

        result = self.call_llm(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            output_model=PiecesOutput,
            max_tokens=4096,
        )

        # Stamp fields that the LLM doesn't know
        full_pieces = []
        for llm_piece in result.pieces:
            piece = ContentPiece(
                campaign_id=campaign_id,
                platform=platform,
                content_type=llm_piece.content_type,
                title=llm_piece.title,
                body=llm_piece.body,
                hashtags=llm_piece.hashtags,
                media_prompts=llm_piece.media_prompts,
                target_subreddit=llm_piece.target_subreddit,
                warmup_mode=in_warmup,
                product_mention_allowed=in_promo,
                status="draft",
            )
            full_pieces.append(piece)

        # Record used themes for future diversity enforcement
        from db.models import UsedContentTheme
        for piece in full_pieces:
            theme_hint = (piece.title or piece.body[:60]).strip()
            self.db.add(UsedContentTheme(
                campaign_id=campaign_id,
                platform=platform,
                theme=theme_hint,
            ))
        self.db.commit()

        return full_pieces

    def _fetch_reddit_examples(self, subreddits: list[str], per_sub: int = 3) -> list[dict]:
        """Fetch real hot posts from subreddits as style reference. Uses public API, no auth needed."""
        import requests
        headers = {"User-Agent": "mktAgent/1.0"}
        examples = []
        seen_titles: set[str] = set()

        for sr in subreddits[:2]:  # max 2 subreddits to keep prompt size reasonable
            sr_name = sr.lstrip("r/")
            try:
                resp = requests.get(
                    f"https://www.reddit.com/r/{sr_name}/hot.json",
                    params={"limit": 10},
                    headers=headers,
                    timeout=8,
                )
                if resp.status_code != 200:
                    continue
                posts = resp.json().get("data", {}).get("children", [])
                count = 0
                for child in posts:
                    p = child.get("data", {})
                    title = p.get("title", "")
                    body = (p.get("selftext") or "").strip()
                    # Skip mod posts, pinned, or very short posts
                    if p.get("stickied") or not title or title in seen_titles:
                        continue
                    # Skip link posts (no body) unless title is strong
                    if not body and len(title.split()) < 5:
                        continue
                    seen_titles.add(title)
                    examples.append({
                        "title": title,
                        "body": body[:300] if body else "",
                        "subreddit": sr_name,
                    })
                    count += 1
                    if count >= per_sub:
                        break
            except Exception as e:
                logger.warning("[content] Failed to fetch examples from r/%s: %s", sr_name, e)

        return examples

    def _save(self, batch: ContentBatch):
        from db.models import ContentPiece as DBPiece
        for piece in batch.pieces:
            row = DBPiece(
                id=piece.content_id,
                campaign_id=piece.campaign_id,
                platform=piece.platform,
                content_type=piece.content_type,
                title=piece.title,
                body=piece.body,
                hashtags_json=piece.hashtags,
                scheduled_for=piece.scheduled_for,
                warmup_mode=piece.warmup_mode,
                product_mention_allowed=piece.product_mention_allowed,
                status=piece.status,
            )
            self.db.merge(row)
        self.db.commit()
