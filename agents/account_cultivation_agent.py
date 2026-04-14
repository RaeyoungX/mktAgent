"""
AccountCultivationAgent: manages account warmup state machine.
Same karma-phase logic as reddit-cultivate/SKILL.md:
  age < 3 days → LURK (browse + upvote only)
  karma < 600  → WARMUP (non-promo content, build credibility)
  karma >= 600 → PROMO (soft product mentions allowed)
"""

import logging
import random
import time
from datetime import date, datetime
from sqlalchemy.orm import Session

from agents.base_agent import BaseAgent
from schemas.campaign import ChannelStrategy
from schemas.metrics import AccountHealth

logger = logging.getLogger(__name__)

LURK_AGE_DAYS = 3
WARMUP_KARMA_THRESHOLD = 600


class AccountCultivationAgent(BaseAgent):
    name = "account_cultivation"

    def run(self, campaign_id: str, strategy: ChannelStrategy, accounts: dict[str, list[str]]) -> list[AccountHealth]:
        """
        Run a cultivation session for all configured accounts.
        accounts: {platform: [username, ...]}
        Returns updated AccountHealth list.
        """
        results = []
        for platform, usernames in accounts.items():
            for username in usernames:
                health = self._run_platform_session(campaign_id, platform, username, strategy)
                results.append(health)
        self.log_session(campaign_id, "account_cultivation", {"accounts_processed": len(results)})
        return results

    def _run_platform_session(self, campaign_id: str, platform: str, username: str, strategy: ChannelStrategy) -> AccountHealth:
        health = self._load_health(platform, username)

        if platform == "reddit":
            health = self._reddit_session(health, strategy, campaign_id)
        else:
            logger.info("[%s] Cultivation not implemented for %s/%s", self.name, platform, username)

        self._save_health(health)
        return health

    def _reddit_session(self, health: AccountHealth, strategy: ChannelStrategy, campaign_id: str) -> AccountHealth:
        from tools.reddit_chrome import get_me, get_hot_posts, upvote

        logger.info("[%s] Reddit session for u/%s (phase: %s)", self.name, health.username, health.warmup_phase)

        # Refresh account stats from Reddit
        me = get_me()
        if not me:
            logger.warning("Could not fetch Reddit /api/me.json — Chrome logged in?")
            return health

        import time as _time
        account_age_days = (_time.time() - me.get("created_utc", _time.time())) / 86400
        karma = me.get("karma", 0)
        health.account_age_days = account_age_days
        health.karma = karma

        # Determine phase
        if account_age_days < LURK_AGE_DAYS:
            health.warmup_phase = "lurk"
        elif karma < WARMUP_KARMA_THRESHOLD:
            health.warmup_phase = "warmup"
        else:
            health.warmup_phase = "promo"

        logger.info("[%s] u/%s: age=%.1fd karma=%d → phase=%s", self.name, health.username, account_age_days, karma, health.warmup_phase)

        if health.warmup_phase == "lurk":
            self._do_lurk(health)
        elif health.warmup_phase in ("warmup", "promo"):
            self._do_session(health, strategy, campaign_id)

        health.last_session_date = date.today().isoformat()
        health.last_checked = datetime.utcnow()
        return health

    def _do_lurk(self, health: AccountHealth):
        """Browse and upvote only — no posting."""
        from tools.reddit_chrome import get_hot_posts, upvote, get_me
        logger.info("[%s] LURK: browsing and upvoting", self.name)

        me = get_me()
        if not me:
            return
        modhash = me.get("modhash", "")

        karma_subs = ["NoStupidQuestions", "CasualConversation", "AskReddit"]
        random.shuffle(karma_subs)

        upvoted = 0
        for sr in karma_subs[:2]:
            posts = get_hot_posts(sr, limit=10)
            if not posts:
                continue
            sample = random.sample(posts[:8], min(3, len(posts)))
            for post in sample:
                upvote(post["id"], modhash)
                time.sleep(random.uniform(15, 40))
                upvoted += 1

        logger.info("[%s] Lurk session: upvoted %d posts", self.name, upvoted)

    def _do_session(self, health: AccountHealth, strategy: ChannelStrategy, campaign_id: str):
        """Warmup or promo: post or comment based on session log."""
        today = date.today()
        today_weekday = today.weekday()

        # Reset weekly post counter if new week
        if health.last_session_date:
            last_dt = date.fromisoformat(health.last_session_date)
            if last_dt.isocalendar()[1] != today.isocalendar()[1]:
                health.post_days_this_week = []

        posts_this_week = health.post_days_this_week or []

        # Decide action type (same logic as reddit-cultivate)
        if len(posts_this_week) >= 2:
            action = "comment"
        elif health.last_action_type == "post":
            action = "comment"
        else:
            action = "post" if random.random() < 0.3 else "comment"

        if action == "comment":
            self._do_comments(health, campaign_id)
        else:
            self._do_post(health, strategy, campaign_id)
            if today_weekday not in posts_this_week:
                health.post_days_this_week = posts_this_week + [today_weekday]

        health.last_action_type = action

    def _do_comments(self, health: AccountHealth, campaign_id: str):
        """Post 2-4 comments in karma-building subreddits."""
        from tools.reddit_chrome import get_new_posts, post_comment, get_me
        from db.models import ContentPiece as DBPiece

        me = get_me()
        if not me:
            return
        modhash = me.get("modhash", "")

        karma_subs = ["NoStupidQuestions", "CasualConversation", "AskReddit"]
        random.shuffle(karma_subs)
        target_count = random.randint(2, 4)
        commented = 0

        for sr in karma_subs:
            if commented >= target_count:
                break
            posts = get_new_posts(sr, limit=15)
            if not posts:
                continue
            # Pick a post with few comments (early = more visible)
            candidates = [p for p in posts if p.get("num_comments", 99) < 10]
            if not candidates:
                candidates = posts[:5]
            post = random.choice(candidates)

            # Use a warmup-mode ContentPiece if available, else skip
            piece = self.db.query(DBPiece).filter(
                DBPiece.campaign_id == campaign_id,
                DBPiece.platform == "reddit",
                DBPiece.content_type == "comment",
                DBPiece.warmup_mode == True,
                DBPiece.status == "draft",
            ).first()

            if not piece:
                logger.info("[%s] No warmup comment content available for %s", self.name, sr)
                continue

            result = post_comment(post["id"], piece.body, modhash)
            if result:
                piece.status = "posted"
                piece.posted_at = datetime.utcnow()
                self.db.commit()
                commented += 1
                logger.info("[%s] Commented in r/%s", self.name, sr)
                time.sleep(random.uniform(60, 180))

    def _do_post(self, health: AccountHealth, strategy: ChannelStrategy, campaign_id: str):
        """Post a new thread."""
        from tools.reddit_chrome import get_me, submit_post
        from db.models import ContentPiece as DBPiece

        platform_strategy = strategy.get_platform("reddit")
        if not platform_strategy or not platform_strategy.subreddits:
            return

        me = get_me()
        if not me:
            return
        modhash = me.get("modhash", "")

        # Pick subreddit
        sr = random.choice(platform_strategy.subreddits).lstrip("r/")

        # Use warmup or promo content depending on phase
        allow_promo = health.warmup_phase == "promo"
        piece = self.db.query(DBPiece).filter(
            DBPiece.campaign_id == campaign_id,
            DBPiece.platform == "reddit",
            DBPiece.content_type == "post",
            DBPiece.status == "draft",
            DBPiece.product_mention_allowed == allow_promo,
        ).first()

        if not piece:
            logger.info("[%s] No suitable post content available", self.name)
            return

        result = submit_post(sr, piece.title or "Untitled", piece.body, modhash)
        if result:
            piece.status = "posted"
            piece.post_url = result.get("url", "")
            piece.posted_at = datetime.utcnow()
            self.db.commit()
            logger.info("[%s] Posted to r/%s: %s", self.name, sr, piece.title)

        health.last_post_type = "post"

    def _load_health(self, platform: str, username: str) -> AccountHealth:
        from db.models import AccountHealth as DBHealth
        row = self.db.query(DBHealth).filter(
            DBHealth.platform == platform,
            DBHealth.username == username,
        ).first()
        if row:
            return AccountHealth(
                platform=row.platform,
                username=row.username,
                account_age_days=row.account_age_days or 0,
                karma=row.karma,
                followers=row.followers,
                is_shadowbanned=row.is_shadowbanned,
                warmup_phase=row.warmup_phase or "lurk",
                last_action_type=row.last_action_type,
                last_post_type=row.last_post_type,
                last_post_length=row.last_post_length,
                post_days_this_week=row.post_days_this_week_json or [],
                last_session_date=row.last_session_date,
            )
        return AccountHealth(platform=platform, username=username)

    def _save_health(self, health: AccountHealth):
        from db.models import AccountHealth as DBHealth
        row = self.db.query(DBHealth).filter(
            DBHealth.platform == health.platform,
            DBHealth.username == health.username,
        ).first()
        if not row:
            row = DBHealth(platform=health.platform, username=health.username)
            self.db.add(row)

        row.account_age_days = health.account_age_days
        row.karma = health.karma
        row.followers = health.followers
        row.is_shadowbanned = health.is_shadowbanned
        row.warmup_phase = health.warmup_phase
        row.last_action_type = health.last_action_type
        row.last_post_type = health.last_post_type
        row.last_post_length = health.last_post_length
        row.post_days_this_week_json = health.post_days_this_week
        row.last_session_date = health.last_session_date
        row.last_checked = health.last_checked or datetime.utcnow()
        self.db.commit()
