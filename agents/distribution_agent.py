"""
DistributionAgent: posts pending ContentPieces to their target platforms.
Uses AppleScript → real Chrome for Reddit/Twitter/LinkedIn.
Generates computer-use instructions for XHS.
"""

import logging
import random
import time
from datetime import datetime
from sqlalchemy.orm import Session

from agents.base_agent import BaseAgent
from db.models import ContentPiece as DBPiece

logger = logging.getLogger(__name__)


class DistributionAgent(BaseAgent):
    name = "distribution"

    def run(self, campaign_id: str, platform: str = None, dry_run: bool = False) -> list[dict]:
        """
        Post all pending (draft, scheduled) content for a campaign.
        Optionally filter by platform.
        Returns list of result dicts.
        """
        pieces = self._get_pending(campaign_id, platform)
        if not pieces:
            logger.info("[%s] No pending content to post", self.name)
            return []

        logger.info("[%s] Found %d pieces to post", self.name, len(pieces))
        results = []

        for piece in pieces:
            if dry_run:
                logger.info("[DRY RUN] Would post: %s / %s / %s", piece.platform, piece.content_type, (piece.title or piece.body[:40]))
                results.append({"content_id": piece.id, "platform": piece.platform, "dry_run": True})
                continue

            result = self._post_piece(piece)
            results.append(result)

            # Rate limit between posts
            if result.get("success") and pieces.index(piece) < len(pieces) - 1:
                delay = random.uniform(60, 180)
                logger.info("Waiting %.0fs before next post...", delay)
                time.sleep(delay)

        self.log_session(campaign_id, "distribution", {"posted": len([r for r in results if r.get("success")])})
        return results

    def _get_pending(self, campaign_id: str, platform: str = None) -> list[DBPiece]:
        now = datetime.utcnow()
        q = self.db.query(DBPiece).filter(
            DBPiece.campaign_id == campaign_id,
            DBPiece.status == "approved",   # must be reviewed + approved in GUI first
            DBPiece.post_url == None,
        )
        if platform:
            q = q.filter(DBPiece.platform == platform)
        # Only post pieces scheduled for now or earlier (or no schedule = post now)
        pieces = q.all()
        return [p for p in pieces if p.scheduled_for is None or p.scheduled_for <= now]

    def _post_piece(self, piece: DBPiece) -> dict:
        platform = piece.platform
        try:
            if platform == "reddit":
                result = self._post_reddit(piece)
            elif platform == "twitter":
                result = self._post_twitter(piece)
            elif platform == "linkedin":
                result = self._post_linkedin(piece)
            elif platform == "xhs":
                result = self._post_xhs(piece)
            else:
                logger.warning("Unknown platform: %s", platform)
                return {"content_id": piece.id, "platform": platform, "success": False, "error": "unknown platform"}

            if result.get("success"):
                self._mark_posted(piece, result.get("url", ""))
            else:
                self._mark_failed(piece, result.get("error", "unknown error"))
            return result

        except Exception as exc:
            logger.error("Distribution error for %s: %s", piece.id, exc, exc_info=True)
            self._mark_failed(piece, str(exc))
            return {"content_id": piece.id, "platform": platform, "success": False, "error": str(exc)}

    def _post_reddit(self, piece: DBPiece) -> dict:
        from tools.reddit_chrome import get_me, submit_post, post_comment

        me = get_me()
        if not me:
            return {"content_id": piece.id, "platform": "reddit", "success": False, "error": "could not fetch /api/me.json — is Chrome logged in to Reddit?"}

        modhash = me.get("modhash", "")
        if not modhash:
            return {"content_id": piece.id, "platform": "reddit", "success": False, "error": "no modhash in me.json response"}

        if piece.content_type == "post":
            subreddit = piece.target_subreddit or "test"
            # Strip "r/" prefix if present
            subreddit = subreddit.lstrip("r/")
            resp = submit_post(subreddit, piece.title or "Untitled", piece.body, modhash)
            if resp:
                url = resp.get("url") or f"https://reddit.com/r/{subreddit}"
                return {"content_id": piece.id, "platform": "reddit", "success": True, "url": url}
            return {"content_id": piece.id, "platform": "reddit", "success": False, "error": "submit_post returned None"}

        elif piece.content_type == "comment":
            # For comments we need a target post ID — skip if not set
            logger.warning("Comment posting requires target post ID — piece %s skipped", piece.id)
            return {"content_id": piece.id, "platform": "reddit", "success": False, "error": "comment posting requires manual target post selection"}

        return {"content_id": piece.id, "platform": "reddit", "success": False, "error": f"unknown content_type: {piece.content_type}"}

    def _post_twitter(self, piece: DBPiece) -> dict:
        from tools.twitter_chrome import tweet

        text = piece.body
        if piece.hashtags_json:
            text += " " + " ".join(f"#{t}" for t in piece.hashtags_json[:2])

        resp = tweet(text)
        if resp and resp.get("ok"):
            return {"content_id": piece.id, "platform": "twitter", "success": True, "url": "https://twitter.com"}
        return {"content_id": piece.id, "platform": "twitter", "success": False, "error": str(resp)}

    def _post_linkedin(self, piece: DBPiece) -> dict:
        from tools.linkedin_chrome import create_post

        text = piece.body
        if piece.hashtags_json:
            text += "\n\n" + " ".join(f"#{t}" for t in piece.hashtags_json[:3])

        resp = create_post(text)
        if resp and resp.get("ok"):
            return {"content_id": piece.id, "platform": "linkedin", "success": True, "url": "https://linkedin.com"}
        return {"content_id": piece.id, "platform": "linkedin", "success": False, "error": str(resp)}

    def _post_xhs(self, piece: DBPiece) -> dict:
        from tools.xhs_sdk import generate_post_instructions
        instructions = generate_post_instructions(
            title=piece.title or "",
            body=piece.body,
            hashtags=piece.hashtags_json or [],
        )
        # XHS requires computer-use — print instructions for Claude Code context to execute
        logger.info("[%s] XHS post requires manual computer-use execution:\n%s", self.name, instructions)
        print("\n" + "="*60)
        print("XHS POST — execute via computer-use:")
        print(instructions)
        print("="*60 + "\n")
        # Mark as needs_manual since we can't auto-execute from pure Python
        return {
            "content_id": piece.id,
            "platform": "xhs",
            "success": False,
            "error": "XHS requires computer-use MCP — instructions printed above",
            "instructions": instructions,
        }

    def _mark_posted(self, piece: DBPiece, url: str):
        piece.status = "posted"
        piece.post_url = url
        piece.posted_at = datetime.utcnow()
        self.db.commit()

    def _mark_failed(self, piece: DBPiece, error: str):
        piece.status = "failed"
        piece.error_msg = error
        self.db.commit()
