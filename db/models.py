"""SQLAlchemy ORM models for mktAgent state persistence."""

import json
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, ForeignKey, JSON
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Product(Base):
    """A product the user wants to market. Replaces YAML campaign configs."""
    __tablename__ = "products"

    id = Column(String, primary_key=True)           # UUID
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    description = Column(String)                    # auto-filled after analysis
    enabled_platforms_json = Column(JSON, default=dict)  # {platform: {enabled, accounts, subreddits}}
    status = Column(String, default="active")       # active / paused
    created_at = Column(DateTime, default=datetime.utcnow)
    last_run_at = Column(DateTime)


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(String, primary_key=True)          # campaign_id from config
    product_url = Column(String, nullable=False)
    product_name = Column(String, nullable=False)
    status = Column(String, default="active")      # active / paused / archived
    config_yaml = Column(Text)                     # raw YAML snapshot
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    analyses = relationship("ProductAnalysis", back_populates="campaign")
    strategies = relationship("ChannelStrategy", back_populates="campaign")
    content_pieces = relationship("ContentPiece", back_populates="campaign")
    session_logs = relationship("SessionLog", back_populates="campaign")


class ProductAnalysis(Base):
    __tablename__ = "product_analyses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(String, ForeignKey("campaigns.id"), nullable=False)
    analysis_json = Column(JSON, nullable=False)   # Full ProductAnalysis Pydantic dict
    scraped_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    campaign = relationship("Campaign", back_populates="analyses")


class ChannelStrategy(Base):
    __tablename__ = "channel_strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(String, ForeignKey("campaigns.id"), nullable=False)
    strategy_json = Column(JSON, nullable=False)   # Full ChannelStrategy Pydantic dict
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)

    campaign = relationship("Campaign", back_populates="strategies")


class ContentPiece(Base):
    __tablename__ = "content_pieces"

    id = Column(String, primary_key=True)          # UUID content_id
    campaign_id = Column(String, ForeignKey("campaigns.id"), nullable=False)
    platform = Column(String, nullable=False)      # reddit / twitter / linkedin / xhs
    content_type = Column(String, nullable=False)  # post / comment / thread
    title = Column(String)
    body = Column(Text, nullable=False)
    hashtags_json = Column(JSON, default=list)
    scheduled_for = Column(DateTime)
    warmup_mode = Column(Boolean, default=False)
    product_mention_allowed = Column(Boolean, default=False)
    status = Column(String, default="draft")       # draft / posted / failed
    post_url = Column(String)                      # set after successful post
    error_msg = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    posted_at = Column(DateTime)

    campaign = relationship("Campaign", back_populates="content_pieces")
    metrics = relationship("PostMetrics", back_populates="content_piece")


class PostMetrics(Base):
    __tablename__ = "post_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content_id = Column(String, ForeignKey("content_pieces.id"), nullable=False)
    platform = Column(String, nullable=False)
    post_url = Column(String)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    views = Column(Integer, default=0)
    upvotes = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0.0)
    checked_at = Column(DateTime, default=datetime.utcnow)

    content_piece = relationship("ContentPiece", back_populates="metrics")


class AccountHealth(Base):
    __tablename__ = "account_health"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String, nullable=False)
    username = Column(String, nullable=False)
    account_age_days = Column(Float, default=0)
    karma = Column(Integer)                        # Reddit-specific
    followers = Column(Integer)
    following = Column(Integer)
    is_shadowbanned = Column(Boolean, default=False)
    is_restricted = Column(Boolean, default=False)
    warmup_phase = Column(String, default="lurk")  # lurk / warmup / promo
    last_action_type = Column(String)              # post / comment
    last_post_type = Column(String)                # emotional / tips / discussion
    last_post_length = Column(String)              # short / medium / long
    post_days_this_week_json = Column(JSON, default=list)
    last_session_date = Column(String)             # ISO date string
    last_checked = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # Unique per platform + username
        {"sqlite_autoincrement": True},
    )


class SessionLog(Base):
    __tablename__ = "session_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(String, ForeignKey("campaigns.id"))
    agent_name = Column(String, nullable=False)
    action = Column(String, nullable=False)
    result_json = Column(JSON)
    error = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)

    campaign = relationship("Campaign", back_populates="session_logs")
