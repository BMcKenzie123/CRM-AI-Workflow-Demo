"""Claude-based triage: classify intent, extract entities, score urgency.

Uses tool-use / structured output so the response is always valid JSON
matching a known schema. Retries on transient errors with exponential backoff.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from anthropic import Anthropic, APIError, APIStatusError

log = logging.getLogger(__name__)

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": [
                "sales_lead",
                "support_request",
                "complaint",
                "vendor_outreach",
                "billing",
                "internal",
                "spam",
                "other",
            ],
        },
        "urgency": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "intent": {"type": "string", "description": "One-phrase description of what the sender wants"},
        "extracted": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "contact_name": {"type": "string"},
                "quantity": {"type": "number"},
                "product": {"type": "string"},
                "timeline": {"type": "string"},
                "budget_status": {"type": "string"},
            },
        },
        "suggested_response": {"type": "string", "description": "1-2 sentence draft reply"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["category", "urgency", "intent", "extracted", "suggested_response", "confidence"],
}

SYSTEM_PROMPT = """You are an inbox triage assistant for a B2B operations team.

Given an inbound message, you must:
1. Classify the message into one of: sales_lead, support_request, complaint,
   vendor_outreach, billing, internal, spam, other
2. Score urgency: low (informational, no action), medium (respond within 1 day),
   high (respond same business day), critical (respond within 1 hour)
3. Identify the sender's intent in one phrase
4. Extract structured fields where present (company, contact name, quantity,
   product, timeline, budget status). Omit fields not in the message.
5. Draft a 1-2 sentence suggested response that an account manager could
   personalize and send.
6. Provide a confidence score (0.0-1.0) reflecting how certain you are about
   the category and urgency.

Be conservative on urgency — only mark "critical" for clear emergencies.
Mark "spam" only when the message has no plausible legitimate intent.
"""


class TriageClient:
    """Claude-based message triage with structured output."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ):
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    def classify(self, sender: str, subject: str, body: str) -> dict[str, Any]:
        """Classify a message. Returns dict matching TRIAGE_SCHEMA."""
        user_message = (
            f"From: {sender}\n"
            f"Subject: {subject}\n\n"
            f"{body}"
        )

        # Use tool_use for guaranteed structured output
        tool = {
            "name": "record_triage",
            "description": "Record the triage result for this message",
            "input_schema": TRIAGE_SCHEMA,
        }

        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": "record_triage"},
                    messages=[{"role": "user", "content": user_message}],
                )

                # Extract the tool_use block
                for block in response.content:
                    if block.type == "tool_use" and block.name == "record_triage":
                        return dict(block.input)

                raise ValueError("No tool_use block in response")

            except APIStatusError as e:
                if e.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    delay = self.retry_base_delay * (2 ** attempt)
                    log.warning("Triage retry %d after %.1fs (status=%d)", attempt + 1, delay, e.status_code)
                    time.sleep(delay)
                    continue
                raise
            except APIError:
                if attempt < self.max_retries - 1:
                    delay = self.retry_base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Triage exhausted retries")
