"""Content piece and batch Pydantic schemas."""

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ContentPiece(BaseModel):
    content_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    campaign_id: str
    platform: str                          # reddit / twitter / linkedin / xhs
    content_type: str                      # post / comment / thread / note
    title: Optional[str] = None
    body: str
    hashtags: list[str] = []
    media_prompts: list[str] = []          # descriptions of images/video needed
    target_subreddit: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    warmup_mode: bool = False              # True = no product mention
    product_mention_allowed: bool = False
    status: str = "draft"                  # draft / posted / failed
    post_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ContentBatch(BaseModel):
    campaign_id: str
    pieces: list[ContentPiece]
    week_number: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def total_pieces(self) -> int:
        return len(self.pieces)

    def by_platform(self, platform: str) -> list[ContentPiece]:
        return [p for p in self.pieces if p.platform == platform]
