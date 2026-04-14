"""Campaign and channel strategy Pydantic schemas."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class PlatformStrategy(BaseModel):
    platform: str                        # reddit / twitter / linkedin / xhs / tiktok
    enabled: bool = True
    priority: int = 1                    # 1 = highest
    posting_frequency: str = "2x_per_week"
    best_times: list[str] = []           # ["09:00", "19:00"] local time
    content_formats: list[str] = []      # text_post / video_script / image_caption
    tone: str = "casual"                 # casual / professional / educational
    account_warmup_required: bool = True
    subreddits: list[str] = []           # Reddit-specific
    hashtags: list[str] = []


class ChannelStrategy(BaseModel):
    campaign_id: str
    platforms: list[PlatformStrategy]
    overall_narrative: str
    content_calendar_weeks: int = 2
    version: int = 1
    created_at: datetime = None

    def model_post_init(self, __context):
        if self.created_at is None:
            from datetime import datetime
            self.created_at = datetime.utcnow()

    def get_platform(self, name: str) -> Optional[PlatformStrategy]:
        for p in self.platforms:
            if p.platform == name:
                return p
        return None

    def enabled_platforms(self) -> list[PlatformStrategy]:
        return [p for p in self.platforms if p.enabled]
