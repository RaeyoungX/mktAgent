"""BaseAgent: shared LLM call pattern using tool_use for structured output."""

import json
import logging
import os
from typing import Any
from anthropic import Anthropic
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _pydantic_to_input_schema(model_class: type[BaseModel]) -> dict:
    """Convert a Pydantic model to an Anthropic tool input_schema."""
    schema = model_class.model_json_schema()
    # Remove title at top level — Anthropic doesn't need it
    schema.pop("title", None)
    return schema


class BaseAgent:
    """
    Base class for all mktAgent sub-agents.

    Pattern: call Claude with tool_use + tool_choice forced to "structured_output"
    to guarantee Pydantic-parseable JSON responses. No markdown wrapping,
    no schema drift.
    """

    name: str = "base"

    def __init__(self, db: Session):
        self.db = db
        self.client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def call_llm(
        self,
        system: str,
        messages: list[dict],
        output_model: type[BaseModel],
        max_tokens: int = 4096,
    ) -> BaseModel:
        """
        Call Claude with forced tool_use output matching output_model schema.
        Returns a parsed Pydantic instance.
        """
        schema = _pydantic_to_input_schema(output_model)

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            tools=[{
                "name": "structured_output",
                "description": "Return the structured result in the exact schema provided.",
                "input_schema": schema,
            }],
            tool_choice={"type": "tool", "name": "structured_output"},
            messages=messages,
        )

        raw = response.content[0].input
        # Claude sometimes returns list/dict fields as JSON strings — parse them
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, str) and (v.startswith("[") or v.startswith("{")):
                    try:
                        raw[k] = json.loads(v)
                    except Exception:
                        pass
        return output_model.model_validate(raw)

    def call_llm_text(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 2048,
    ) -> str:
        """Call Claude for free-text output (no structured schema)."""
        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return response.content[0].text

    def log_session(self, campaign_id: str, action: str, result: Any = None, error: str = None):
        from db.models import SessionLog
        from datetime import datetime
        log = SessionLog(
            campaign_id=campaign_id,
            agent_name=self.name,
            action=action,
            result_json=result if isinstance(result, dict) else None,
            error=error,
            completed_at=datetime.utcnow(),
        )
        self.db.add(log)
        self.db.commit()
