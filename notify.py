"""Slack / Discord webhook notifier."""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

URGENCY_EMOJI = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🟠",
    "critical": "🔴",
}


class Notifier:
    """Posts triage results to Slack or Discord via incoming webhook."""

    def __init__(self, webhook_url: str | None, platform: str = "slack"):
        self.webhook_url = webhook_url
        self.platform = platform.lower()
        if self.platform not in ("slack", "discord"):
            raise ValueError(f"Unsupported notify platform: {platform}")

    def send(
        self,
        triage: dict[str, Any],
        sender: str,
        subject: str,
        crm_url: str,
    ) -> None:
        if not self.webhook_url:
            log.debug("No NOTIFY_WEBHOOK_URL set; skipping notification")
            return

        emoji = URGENCY_EMOJI.get(triage.get("urgency", "low"), "⚪")
        category = triage.get("category", "unknown")
        urgency = triage.get("urgency", "unknown")
        extracted = triage.get("extracted", {}) or {}
        company = extracted.get("company") or "—"
        contact = extracted.get("contact_name") or sender
        suggested = triage.get("suggested_response", "")

        if self.platform == "slack":
            payload = {
                "text": f"{emoji} New {category} — {urgency} urgency",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"{emoji} *New {category}* — _{urgency} urgency_\n"
                                f"*{company}* · {contact}\n"
                                f"> {subject}\n\n"
                                f"*Suggested reply:* {suggested}"
                            ),
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": f"<{crm_url}|View in CRM>"}
                        ],
                    },
                ],
            }
        else:  # discord
            payload = {
                "embeds": [
                    {
                        "title": f"{emoji} New {category} — {urgency} urgency",
                        "description": (
                            f"**{company}** · {contact}\n"
                            f"> {subject}\n\n"
                            f"**Suggested reply:** {suggested}"
                        ),
                        "url": crm_url,
                        "color": _discord_color(urgency),
                    }
                ]
            }

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(self.webhook_url, json=payload)
            resp.raise_for_status()
        log.info("Notification sent to %s", self.platform)


def _discord_color(urgency: str) -> int:
    return {
        "low": 0x2ECC71,       # green
        "medium": 0xF1C40F,    # yellow
        "high": 0xE67E22,      # orange
        "critical": 0xE74C3C,  # red
    }.get(urgency, 0x95A5A6)
