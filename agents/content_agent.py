"""ContentAgent: generates platform-specific content batches."""

import logging
from datetime import datetime
from sqlalchemy.orm import Session

from agents.base_agent import BaseAgent
from schemas.product import ProductAnalysis
from schemas.campaign import ChannelStrategy, PlatformStrategy
from schemas.content import ContentBatch, ContentPiece

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a professional content creator who writes platform-native content.
You adapt voice, format, length, and tone precisely per platform.

Platform writing rules:
- reddit: No hashtags. Casual, first-person. Lowercase "i" is fine. Sounds typed at 1am.
  Forbidden: "So,", "Just wanted to share", em-dash overuse, ending with questions every time.
  Titles: 6-12 words. Relatable, not clickbait. Body: 3-6 sentences for casual, 2 paragraphs for detailed.
- xhs (小红书): Chinese language. Emojis throughout. Punchy opening line. Short paragraphs.
  Hashtags in Chinese at the end. Visual descriptions welcome.
- twitter: 280 chars OR a thread (max 6 posts). Conversational. Max 2 hashtags.
  Thread format: each tweet self-contained but builds on the last.
- linkedin: Professional but personal. Thought leadership. Start with a hook. 3-5 short paragraphs.
  End with a question or observation. No more than 3 hashtags.

Warmup content (warmup_mode=true): NO product mention. Focus on the topic/community.
Promo content (product_mention_allowed=true): mention naturally, never lead with it.

For each piece, also provide media_prompts (what image/graphic would help this post).
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

        for platform_strategy in strategy.enabled_platforms():
            platform = platform_strategy.platform
            warmup_phase = account_phases.get(platform, "warmup")
            pieces = self._generate_for_platform(
                platform_strategy, product, campaign_id, week_number, warmup_phase
            )
            all_pieces.extend(pieces)

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

        if platform == "reddit":
            subreddits = platform_strategy.subreddits or ["r/general"]
            user_msg += f"Target subreddits: {', '.join(subreddits)}\n"
            user_msg += "Rotate pieces across different subreddits.\n"

        if platform == "xhs":
            user_msg += "Write in Chinese. Include relevant Chinese hashtags.\n"

        # Use the base LLM call with a raw schema for a list of pieces
        from pydantic import BaseModel
        from typing import Optional

        class PiecesOutput(BaseModel):
            pieces: list[ContentPiece]

        result = self.call_llm(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            output_model=PiecesOutput,
            max_tokens=4096,
        )

        # Stamp fields that the LLM won't know
        for piece in result.pieces:
            piece.campaign_id = campaign_id
            piece.platform = platform
            piece.warmup_mode = in_warmup
            piece.product_mention_allowed = in_promo
            piece.status = "draft"

        return result.pieces

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
