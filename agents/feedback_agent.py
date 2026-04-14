"""
FeedbackAgent: reads post metrics, synthesizes performance data,
and generates a FeedbackReport for the CMO to act on.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from agents.base_agent import BaseAgent
from schemas.metrics import FeedbackReport, PostMetrics
from db.models import ContentPiece as DBPiece, PostMetrics as DBMetrics

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a data-driven marketing analyst.
Given a list of recent posts and their engagement metrics, provide:
1. What types of content performed best and why
2. What didn't work and why
3. Specific, actionable recommendations for the CMO
4. Suggested strategy adjustments (platform prioritization, tone shifts, cadence changes)

Be specific. Reference actual post titles/types. Don't give generic advice.
Focus on what the data actually shows, not what "usually works".
"""


class FeedbackAgent(BaseAgent):
    name = "feedback"

    def run(self, campaign_id: str, days: int = 7) -> FeedbackReport:
        period_end = datetime.utcnow()
        period_start = period_end - timedelta(days=days)

        logger.info("[%s] Gathering metrics for %s (last %d days)", self.name, campaign_id, days)

        # Refresh metrics from platforms
        posted_pieces = self.db.query(DBPiece).filter(
            DBPiece.campaign_id == campaign_id,
            DBPiece.status == "posted",
            DBPiece.posted_at >= period_start,
        ).all()

        metrics_data = []
        for piece in posted_pieces:
            m = self._fetch_metrics(piece)
            if m:
                metrics_data.append(m)
                self._save_metrics(m)

        if not metrics_data:
            logger.info("[%s] No metrics to analyze", self.name)
            return FeedbackReport(
                campaign_id=campaign_id,
                period_start=period_start,
                period_end=period_end,
                recommendations=["No posted content found in the period. Run distribution first."],
            )

        report = self._synthesize(campaign_id, metrics_data, period_start, period_end)
        self._save_report(campaign_id, report)
        self.log_session(campaign_id, "feedback", {"posts_analyzed": len(metrics_data)})
        return report

    def _fetch_metrics(self, piece: DBPiece) -> Optional[PostMetrics]:
        if piece.platform == "reddit" and piece.post_url:
            return self._fetch_reddit_metrics(piece)
        # For Twitter/LinkedIn, return what we have stored
        existing = self.db.query(DBMetrics).filter(
            DBMetrics.content_id == piece.id
        ).order_by(DBMetrics.checked_at.desc()).first()
        if existing:
            return PostMetrics(
                content_id=piece.id,
                platform=piece.platform,
                post_url=piece.post_url or "",
                posted_at=piece.posted_at or datetime.utcnow(),
                likes=existing.likes,
                comments=existing.comments,
                upvotes=existing.upvotes,
            )
        return PostMetrics(
            content_id=piece.id,
            platform=piece.platform,
            post_url=piece.post_url or "",
            posted_at=piece.posted_at or datetime.utcnow(),
        )

    def _fetch_reddit_metrics(self, piece: DBPiece) -> Optional[PostMetrics]:
        try:
            from tools.reddit_chrome import get_post_metrics
            data = get_post_metrics(piece.post_url)
            if not data:
                return None
            return PostMetrics(
                content_id=piece.id,
                platform="reddit",
                post_url=piece.post_url,
                posted_at=piece.posted_at or datetime.utcnow(),
                upvotes=data.get("score", 0),
                comments=data.get("num_comments", 0),
                engagement_rate=data.get("upvote_ratio", 0.0),
            )
        except Exception as exc:
            logger.warning("Failed to fetch Reddit metrics for %s: %s", piece.id, exc)
            return None

    def _synthesize(
        self, campaign_id: str, metrics: list[PostMetrics], period_start: datetime, period_end: datetime
    ) -> FeedbackReport:
        # Build a summary for Claude
        metrics_summary = []
        platform_stats: dict[str, dict] = {}
        top_pieces = []

        for m in metrics:
            total_engagement = m.likes + m.upvotes + m.comments + m.shares
            metrics_summary.append(
                f"- [{m.platform}] {m.post_url or m.content_id}: "
                f"upvotes={m.upvotes}, comments={m.comments}, likes={m.likes}, engagement={total_engagement}"
            )

            # Platform rollup
            if m.platform not in platform_stats:
                platform_stats[m.platform] = {"total_engagement": 0, "post_count": 0}
            platform_stats[m.platform]["total_engagement"] += total_engagement
            platform_stats[m.platform]["post_count"] += 1

            if total_engagement > 5:
                top_pieces.append(m.content_id)

        for platform, stats in platform_stats.items():
            if stats["post_count"] > 0:
                stats["avg_engagement"] = stats["total_engagement"] / stats["post_count"]

        user_msg = (
            f"Campaign: {campaign_id}\n"
            f"Period: {period_start.date()} to {period_end.date()}\n"
            f"Posts analyzed: {len(metrics)}\n\n"
            f"Metrics:\n" + "\n".join(metrics_summary)
        )

        report = self.call_llm(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            output_model=FeedbackReport,
            max_tokens=2000,
        )

        report.campaign_id = campaign_id
        report.period_start = period_start
        report.period_end = period_end
        report.top_performing_content = top_pieces
        report.platform_performance = platform_stats
        return report

    def _save_metrics(self, m: PostMetrics):
        row = DBMetrics(
            content_id=m.content_id,
            platform=m.platform,
            post_url=m.post_url,
            likes=m.likes,
            comments=m.comments,
            shares=m.shares,
            views=m.views,
            upvotes=m.upvotes,
            engagement_rate=m.engagement_rate,
            checked_at=m.checked_at,
        )
        self.db.add(row)
        self.db.commit()

    def _save_report(self, campaign_id: str, report: FeedbackReport):
        from db.models import SessionLog
        row = SessionLog(
            campaign_id=campaign_id,
            agent_name=self.name,
            action="feedback_report",
            result_json=report.model_dump(mode="json"),
            completed_at=datetime.utcnow(),
        )
        self.db.add(row)
        self.db.commit()
