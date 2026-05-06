#!/usr/bin/env bash
# Smoke test: send the sample payload to a locally-running app
set -euo pipefail

HOST="${HOST:-http://localhost:8000}"

echo "→ Health check"
curl -s "$HOST/health" | jq .

echo
echo "→ Posting sample webhook"
curl -s -X POST "$HOST/hook" \
  -H "Content-Type: application/json" \
  -d @"$(dirname "$0")/sample_webhook.json" | jq .

echo
echo "→ Recent interactions"
curl -s "$HOST/crm/interactions?limit=5" | jq .
