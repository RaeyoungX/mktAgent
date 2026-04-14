"""Metrics, account health, and feedback Pydantic schemas."""

from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, Field


class PostMetrics(BaseModel):
    content_id: str
    platform: str
    post_url: str
    posted_at: datetime
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0
    upvotes: int = 0
    engagement_rate: float = 0.0
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class AccountHealth(BaseModel):
    platform: str
    username: str
    account_age_days: float = 0
    karma: Optional[int] = None          # Reddit
    followers: Optional[int] = None
    following: Optional[int] = None
    is_shadowbanned: bool = False
    is_restricted: bool = False
    warmup_phase: str = "lurk"           # lurk / warmup / promo
    last_action_type: Optional[str] = None      # post / comment
    last_post_type: Optional[str] = None        # emotional / tips / discussion
    last_post_length: Optional[str] = None      # short / medium / long
    post_days_this_week: list[int] = []         # weekday numbers (0=Mon)
    last_session_date: Optional[str] = None     # ISO date


class FeedbackReport(BaseModel):
    campaign_id: str
    period_start: datetime
    period_end: datetime
    top_performing_content: list[str] = []       # content_ids
    platform_performance: dict[str, dict] = {}   # platform → {avg_engagement, post_count, ...}
    what_worked: list[str] = []
    what_didnt: list[str] = []
    recommendations: list[str] = []             # CMO-digestible action items
    strategy_adjustments: list[str] = []
    generated_at: datetime = Field(default_factory=datetime.utcnow)
