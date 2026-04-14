"""ProductAnalysisAgent: scrapes a product URL and extracts structured insights."""

import logging
from datetime import datetime
from sqlalchemy.orm import Session

from agents.base_agent import BaseAgent
from schemas.product import ProductAnalysis
from tools.scraper import scrape_url

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior product marketer and competitive analyst.
Given raw text scraped from a product landing page, extract structured insights.

Rules:
- Be specific, not generic. Extract actual features, not marketing fluff.
- USPs should be genuinely differentiating, not "easy to use".
- Target audience should be as specific as possible (not just "developers").
- Content themes should be concrete topics for social posts (not "product benefits").
- pricing_tier: one of free / freemium / paid / enterprise (infer from page).
- If information is missing from the page, make reasonable inferences based on context.
"""


class ProductAnalysisAgent(BaseAgent):
    name = "product_analysis"

    def run(self, url: str, campaign_id: str) -> ProductAnalysis:
        logger.info("[%s] Scraping %s", self.name, url)
        raw_text = scrape_url(url)

        if not raw_text:
            raw_text = f"Product URL: {url} (could not fetch page content)"
            logger.warning("Scrape returned nothing, proceeding with URL only")

        snippet = raw_text[:2000]

        messages = [{
            "role": "user",
            "content": (
                f"Analyze this product page and extract structured marketing insights.\n\n"
                f"Product URL: {url}\n\n"
                f"Page content:\n{raw_text}"
            ),
        }]

        analysis = self.call_llm(
            system=SYSTEM_PROMPT,
            messages=messages,
            output_model=ProductAnalysis,
            max_tokens=3000,
        )

        # Fill in fields that LLM won't set
        analysis.product_url = url
        analysis.scraped_at = datetime.utcnow()
        analysis.raw_text_snippet = snippet

        # Persist to DB
        self._save(campaign_id, analysis)
        self.log_session(campaign_id, "product_analysis", {"product_name": analysis.product_name})
        logger.info("[%s] Analysis complete: %s", self.name, analysis.product_name)
        return analysis

    def _save(self, campaign_id: str, analysis: ProductAnalysis):
        from db.models import ProductAnalysis as DBAnalysis
        row = DBAnalysis(
            campaign_id=campaign_id,
            analysis_json=analysis.model_dump(mode="json"),
            scraped_at=analysis.scraped_at,
        )
        self.db.add(row)
        self.db.commit()
