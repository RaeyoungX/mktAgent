"""
CMOAgent: top-level orchestrator.
Drives the 4-phase campaign cycle and delegates to all 6 sub-agents.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from agents.base_agent import BaseAgent
from agents.product_analysis_agent import ProductAnalysisAgent
from agents.channel_agent import ChannelAgent
from agents.content_agent import ContentAgent
from agents.account_cultivation_agent import AccountCultivationAgent
from agents.distribution_agent import DistributionAgent
from agents.feedback_agent import FeedbackAgent
from schemas.product import ProductAnalysis
from schemas.campaign import ChannelStrategy
from schemas.content import ContentBatch
from schemas.metrics import FeedbackReport, AccountHealth

logger = logging.getLogger(__name__)

load_dotenv(override=True)


class CMOAgent(BaseAgent):
    name = "cmo"

    def __init__(self, db: Session):
        super().__init__(db)
        self.product_agent = ProductAnalysisAgent(db)
        self.channel_agent = ChannelAgent(db)
        self.content_agent = ContentAgent(db)
        self.cultivation_agent = AccountCultivationAgent(db)
        self.distribution_agent = DistributionAgent(db)
        self.feedback_agent = FeedbackAgent(db)

    # ─── Campaign config loading ─────────────────────────────────────────────

    def load_campaign_config(self, campaign_id: str) -> dict:
        """Load config from DB Product (primary) or YAML fallback (CLI use)."""
        from db.models import Product
        product = self.db.query(Product).filter(Product.id == campaign_id).first()
        if product:
            return self._config_from_product(product)

        # YAML fallback for CLI usage
        config_path = Path("config/products") / f"{campaign_id}.yaml"
        if not config_path.exists():
            for f in Path("config/products").glob("*.yaml"):
                cfg = yaml.safe_load(f.read_text())
                if cfg.get("campaign_id") == campaign_id:
                    return cfg
            raise FileNotFoundError(f"No config found for campaign: {campaign_id}")
        return yaml.safe_load(config_path.read_text())

    def _config_from_product(self, product) -> dict:
        """Build a campaign config dict from a DB Product row."""
        return {
            "campaign_id": product.id,
            "product": {
                "name": product.name,
                "url": product.url,
                "scrape_on_start": True,
            },
            "platforms": product.enabled_platforms_json or {},
        }

    def _ensure_campaign_in_db(self, config: dict):
        from db.models import Campaign
        campaign_id = config["campaign_id"]
        existing = self.db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not existing:
            row = Campaign(
                id=campaign_id,
                product_url=config["product"]["url"],
                product_name=config["product"]["name"],
                config_yaml=yaml.dump(config),
            )
            self.db.add(row)
            self.db.commit()
        else:
            # Keep product_name in sync
            existing.product_name = config["product"]["name"]
            self.db.commit()

    # ─── Phase runners ───────────────────────────────────────────────────────

    def run_analyze(self, campaign_id: str, config: dict) -> tuple[ProductAnalysis, ChannelStrategy, ContentBatch]:
        """Phase 1: analyze product, build strategy, generate content."""
        logger.info("[CMO] Phase 1 — ANALYZE for %s", campaign_id)

        # Reuse existing product analysis if available (skip re-scraping)
        product = self._get_latest_product(campaign_id)
        if product:
            logger.info("[CMO] Reusing existing product analysis for %s", campaign_id)
        else:
            product = self.product_agent.run(config["product"]["url"], campaign_id)

        prev_version = self._get_latest_strategy_version(campaign_id)
        strategy = self.channel_agent.run(product, campaign_id, config, previous_version=prev_version)

        accounts = self._extract_accounts(config)
        account_phases = self._get_account_phases(accounts)
        batch = self.content_agent.run(strategy, product, campaign_id, account_phases=account_phases)

        return product, strategy, batch

    def run_operate(self, campaign_id: str, config: dict) -> dict:
        """Phase 2: cultivation + distribution."""
        logger.info("[CMO] Phase 2 — OPERATE for %s", campaign_id)
        accounts = self._extract_accounts(config)

        strategy = self._get_latest_strategy(campaign_id)
        if not strategy:
            logger.warning("[CMO] No strategy found — run analyze first")
            return {"error": "no strategy"}

        health = self.cultivation_agent.run(campaign_id, strategy, accounts)
        results = self.distribution_agent.run(campaign_id)
        return {"cultivation": [h.model_dump() for h in health], "distribution": results}

    def run_measure(self, campaign_id: str) -> FeedbackReport:
        """Phase 3: fetch metrics and build feedback report."""
        logger.info("[CMO] Phase 3 — MEASURE for %s", campaign_id)
        return self.feedback_agent.run(campaign_id)

    def run_adjust(self, campaign_id: str, config: dict, report: FeedbackReport) -> ChannelStrategy:
        """Phase 4: CMO adjusts strategy based on feedback."""
        logger.info("[CMO] Phase 4 — ADJUST for %s", campaign_id)
        product = self._get_latest_product(campaign_id)
        if not product:
            logger.warning("[CMO] No product analysis found — skipping adjust")
            return None

        prev_version = self._get_latest_strategy_version(campaign_id)
        new_strategy = self.channel_agent.run(
            product, campaign_id, config, feedback=report, previous_version=prev_version
        )
        return new_strategy

    # ─── Full cycle ──────────────────────────────────────────────────────────

    def orchestrate(self, campaign_id: str, agents: list[str] = None):
        """
        Run the full 4-phase campaign cycle (or a subset of agents).
        agents: list of agent names to run, e.g. ["product_analysis", "channel", "content"]
        """
        config = self.load_campaign_config(campaign_id)
        self._ensure_campaign_in_db(config)

        run_all = not agents
        agent_set = set(agents or [])

        logger.info("[CMO] Starting campaign: %s", campaign_id)

        if run_all or agent_set & {"product_analysis", "channel", "content"}:
            self.run_analyze(campaign_id, config)

        if run_all or agent_set & {"account_cultivation", "distribution"}:
            self.run_operate(campaign_id, config)

        if run_all or "feedback" in agent_set:
            report = self.run_measure(campaign_id)
            if run_all:
                self.run_adjust(campaign_id, config, report)

        logger.info("[CMO] Campaign cycle complete: %s", campaign_id)

    # ─── DB helpers ──────────────────────────────────────────────────────────

    def _get_latest_strategy(self, campaign_id: str) -> Optional[ChannelStrategy]:
        from db.models import ChannelStrategy as DBStrategy
        row = self.db.query(DBStrategy).filter(
            DBStrategy.campaign_id == campaign_id
        ).order_by(DBStrategy.version.desc()).first()
        if row:
            return ChannelStrategy.model_validate(row.strategy_json)
        return None

    def _get_latest_strategy_version(self, campaign_id: str) -> int:
        from db.models import ChannelStrategy as DBStrategy
        row = self.db.query(DBStrategy).filter(
            DBStrategy.campaign_id == campaign_id
        ).order_by(DBStrategy.version.desc()).first()
        return row.version if row else 0

    def _get_latest_product(self, campaign_id: str) -> Optional[ProductAnalysis]:
        from db.models import ProductAnalysis as DBAnalysis
        row = self.db.query(DBAnalysis).filter(
            DBAnalysis.campaign_id == campaign_id
        ).order_by(DBAnalysis.id.desc()).first()
        if row:
            return ProductAnalysis.model_validate(row.analysis_json)
        return None

    def _extract_accounts(self, config: dict) -> dict[str, list[str]]:
        """Return {platform: [username, ...]} from campaign config."""
        accounts = {}
        for platform, cfg in config.get("platforms", {}).items():
            if cfg.get("enabled") and cfg.get("accounts"):
                accounts[platform] = cfg["accounts"]
        return accounts

    def _get_account_phases(self, accounts: dict[str, list[str]]) -> dict[str, str]:
        """Return {platform: warmup_phase} for the first account per platform."""
        from db.models import AccountHealth as DBHealth
        phases = {}
        for platform, usernames in accounts.items():
            for username in usernames:
                row = self.db.query(DBHealth).filter(
                    DBHealth.platform == platform,
                    DBHealth.username == username,
                ).first()
                phases[platform] = row.warmup_phase if row else "warmup"
                break
        return phases
