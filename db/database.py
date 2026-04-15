"""Database session factory and initialization."""

import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from db.models import Base

_engine = None
_SessionFactory = None


def get_db_path() -> str:
    home = Path(os.environ.get("MKTAGENT_HOME", Path.home() / ".mktagent"))
    home.mkdir(parents=True, exist_ok=True)
    return str(home / "mktAgent.db")


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(f"sqlite:///{get_db_path()}", echo=False)
    return _engine


def create_fts_tables(engine):
    """Create FTS5 virtual tables for full-text search across agent memory."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS subreddit_memory_fts
            USING fts5(
                campaign_id,
                subreddit,
                notes,
                content='',
                tokenize='porter ascii'
            )
        """))
        conn.commit()


def init_db():
    """Create all tables if they don't exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    create_fts_tables(engine)


def get_session() -> Session:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory()


def search_subreddit_memory(campaign_id: str, query: str, limit: int = 5) -> list[dict]:
    """Full-text search across subreddit performance history."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT subreddit, notes FROM subreddit_memory_fts
            WHERE campaign_id = :cid AND subreddit_memory_fts MATCH :q
            LIMIT :limit
        """), {"cid": campaign_id, "q": query, "limit": limit}).fetchall()
    return [{"subreddit": r[0], "notes": r[1]} for r in rows]


def upsert_subreddit_memory(campaign_id: str, subreddit: str, notes: str):
    """Insert a performance note into FTS index."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO subreddit_memory_fts(campaign_id, subreddit, notes)
            VALUES (:cid, :sr, :notes)
        """), {"cid": campaign_id, "sr": subreddit, "notes": notes})
        conn.commit()
