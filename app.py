"""FastAPI application: webhook receiver → AI triage → CRM + notification."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from crm import CRM
from notify import Notifier
from triage import TriageClient

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("inbox-triage")


class WebhookPayload(BaseModel):
    sender: str = Field(..., alias="from")
    subject: str
    body: str
    received_at: str | None = None

    class Config:
        populate_by_name = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize singletons on startup
    app.state.crm = CRM(db_path=os.getenv("CRM_DB_PATH", "crm.db"))
    app.state.crm.initialize()
    app.state.triage = TriageClient(api_key=os.environ["ANTHROPIC_API_KEY"])
    app.state.notifier = Notifier(
        webhook_url=os.getenv("NOTIFY_WEBHOOK_URL"),
        platform=os.getenv("NOTIFY_PLATFORM", "slack"),
    )
    log.info("App initialized")
    yield
    app.state.crm.close()


app = FastAPI(title="Inbox Triage Demo", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/hook")
async def receive_webhook(payload: WebhookPayload) -> dict[str, Any]:
    """Receive an inbound message, triage it, write to CRM, notify."""
    log.info("Received webhook from=%s subject=%r", payload.sender, payload.subject)

    # 1. Classify and extract via Claude
    try:
        triage_result = app.state.triage.classify(
            sender=payload.sender,
            subject=payload.subject,
            body=payload.body,
        )
    except Exception as e:
        log.exception("Triage failed")
        raise HTTPException(status_code=502, detail=f"Triage failed: {e}")

    log.info(
        "Triage: category=%s urgency=%s confidence=%.2f",
        triage_result["category"],
        triage_result["urgency"],
        triage_result.get("confidence", 0),
    )

    # 2. Write to CRM (idempotent on message hash)
    contact_id, interaction_id = app.state.crm.record_interaction(
        sender=payload.sender,
        subject=payload.subject,
        body=payload.body,
        triage=triage_result,
    )

    # 3. Notify (best-effort, doesn't fail the request)
    try:
        app.state.notifier.send(
            triage=triage_result,
            sender=payload.sender,
            subject=payload.subject,
            crm_url=f"/crm/contact/{contact_id}",
        )
    except Exception as e:
        log.warning("Notification failed (non-fatal): %s", e)

    return {
        "ok": True,
        "contact_id": contact_id,
        "interaction_id": interaction_id,
        "triage": triage_result,
    }


@app.get("/crm/contact/{contact_id}")
def get_contact(contact_id: int) -> dict[str, Any]:
    contact = app.state.crm.get_contact(contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    interactions = app.state.crm.get_interactions(contact_id)
    return {"contact": contact, "interactions": interactions}


@app.get("/crm/interactions")
def list_interactions(limit: int = 50) -> dict[str, Any]:
    return {"interactions": app.state.crm.recent_interactions(limit=limit)}
