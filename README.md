# Inbox → AI Triage → CRM Automation Demo

**A working example of an AI-assisted automation workflow: receives an inbound message via webhook, uses Claude to classify and extract structured fields, writes to a CRM-style data store, and posts a notification to Slack or Discord.**

This demo is a self-contained reference implementation of the pattern an internal "AI-assisted workflow" team would build for any inbound channel — support emails, contact-form submissions, sales inquiries, vendor messages — wherever a human currently has to read, classify, and route.

---

## What it demonstrates

- **Webhook integration** — FastAPI endpoint that receives JSON payloads (drop-in target for any forwarding rule)
- **AI workflow** — structured-output prompting with Claude to classify intent, extract entities, score urgency, and draft a response
- **CRM write-back** — SQLite-backed contact + interaction store with idempotent inserts (swap for HubSpot/Pipedrive/Salesforce by replacing one module)
- **Notification fan-out** — Slack or Discord webhook, formatted with the AI-extracted summary
- **Operational primitives** — health endpoint, structured logging, retry-with-backoff on Claude calls, env-driven config

---

## Architecture

```
                 ┌─────────────────┐
   Inbound       │   POST /hook    │
   webhook ────► │   FastAPI       │
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │  triage.py      │
                 │  (Claude API)   │  ──► classify, extract, score
                 └────────┬────────┘
                          │
              ┌───────────┼───────────┐
              ▼                       ▼
      ┌──────────────┐        ┌──────────────┐
      │   crm.py     │        │  notify.py   │
      │   (SQLite)   │        │  (Slack/     │
      │              │        │   Discord)   │
      └──────────────┘        └──────────────┘
```

---

## Quickstart

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and optionally NOTIFY_WEBHOOK_URL

# 3. Run
uvicorn app:app --reload --port 8000

# 4. Send a test message (in another terminal)
bash examples/run_demo.sh
```

You should see:
- Console log of the triage result
- A new row in `crm.db` (inspect with `sqlite3 crm.db 'SELECT * FROM interactions'`)
- A formatted message in your Slack/Discord channel (if `NOTIFY_WEBHOOK_URL` set)

---

## Example payload

```json
{
  "from": "alex.chen@acme-industries.com",
  "subject": "Quote request — 50 units, urgent",
  "body": "Hi, we're looking to place an order for 50 units of your industrial sensor for a project starting next week. Can you send pricing and lead time? We have budget approved and need to move fast. Thanks, Alex",
  "received_at": "2026-05-05T14:23:00Z"
}
```

### What Claude returns (structured)

```json
{
  "category": "sales_lead",
  "urgency": "high",
  "intent": "quote_request",
  "extracted": {
    "company": "ACME Industries",
    "contact_name": "Alex Chen",
    "quantity": 50,
    "product": "industrial sensor",
    "timeline": "next week",
    "budget_status": "approved"
  },
  "suggested_response": "Thanks Alex — quoting 50 units, lead time and pricing attached. Available for a call this week to confirm.",
  "confidence": 0.92
}
```

### What gets written to the CRM

A normalized row in `interactions` with foreign key into `contacts`. Re-sending the same payload won't double-insert (idempotency on message hash).

### What gets posted to Slack/Discord

```
🟠 New sales_lead — high urgency
ACME Industries · Alex Chen
"Quote request — 50 units, urgent"
Suggested reply: Thanks Alex — quoting 50 units...
View in CRM: http://localhost:8000/crm/contact/{id}
```

---

## File map

| File | Purpose |
|---|---|
| `app.py` | FastAPI app with `/hook`, `/health`, `/crm/...` endpoints |
| `triage.py` | Claude-based classification + extraction with structured output |
| `crm.py` | SQLite CRM client (contacts + interactions tables, idempotent writes) |
| `notify.py` | Slack / Discord webhook poster with format selection |
| `schema.sql` | CRM schema |
| `.env.example` | Required env vars |
| `requirements.txt` | Python deps |
| `examples/run_demo.sh` | curl-based smoke test |
| `examples/sample_webhook.json` | Example input |

---

## Swapping the CRM backend

`crm.py` is intentionally a thin abstraction. To swap for a real CRM:

- **HubSpot:** replace `insert_contact` and `insert_interaction` with calls to the HubSpot v3 API (`https://api.hubapi.com/crm/v3/objects/contacts`)
- **Pipedrive:** swap to the Pipedrive Persons + Activities endpoints
- **Salesforce:** Salesforce REST API + a Composite Tree call to insert both records atomically
- **Airtable / Notion / Google Sheets:** trivial — same shape, different SDK

The triage logic, webhook receiver, and notification fan-out don't change.

---

## Deployment notes

For production, swap:
- Uvicorn dev server → systemd unit + nginx reverse proxy (Ansible roles for both in [`linux-ops-stack`](../linux-ops-stack-clean))
- Local SQLite → managed PostgreSQL (`postgresql` role in same repo)
- `.env` → secrets vault (Ansible Vault, AWS SSM, or your platform's secrets manager)
- Stdout logs → Loki via Promtail (`loki` role in same repo)

The `linux-ops-stack` repo provides the production substrate this demo would deploy onto.

---

## Why this pattern matters

Most "AI in operations" projects fail because they're either too magic (a chatbot bolted onto everything) or too narrow (one specific extractor with no integration story). This demo shows the durable pattern:

1. **Channel** — webhook in
2. **AI as a structured-output function** — not a chat, a classifier with typed return values
3. **Side-effects to system-of-record** — CRM, ticketing, calendar, whatever
4. **Human-visible notification** — so the team trusts what's happening and can intervene

Replace any of the four boxes and the pattern still works. Replace the channel with a Gmail watch, the CRM with HubSpot, the notification with PagerDuty, and you have a different product without rewriting the AI logic.

---

## License

MIT — see `LICENSE`.

*Brogan McKenzie · [broganmcke@gmail.com](mailto:broganmcke@gmail.com)*
