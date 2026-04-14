"""Product analysis Pydantic schemas."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TargetAudience(BaseModel):
    primary: str
    secondary: str
    demographics: dict[str, str] = {}    # age_range, geography, profession
    psychographics: list[str] = []       # motivations, fears, values


class ProductAnalysis(BaseModel):
    product_name: str
    product_url: str
    description: str
    key_features: list[str]
    unique_selling_points: list[str]
    target_audience: TargetAudience
    pain_points_solved: list[str]
    competitive_positioning: str
    pricing_tier: str                    # free / freemium / paid / enterprise
    content_themes: list[str]            # 5-8 themes to build content around
    scraped_at: datetime
    raw_text_snippet: str = ""           # first 2000 chars for audit trail
