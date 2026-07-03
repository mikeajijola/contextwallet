#!/usr/bin/env bash
# Wallet Phase 2 smoke test (build brief §3 "Phase 2 acceptance"). Requires the server
# running: `uvicorn wallet.api:app --port 8000` from the repo root, with the seeds already
# generated (`python seed/make_seed.py`).
set -euo pipefail

HOST="${WALLET_HOST:-http://127.0.0.1:8000}"

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; exit 1; }

echo "== connecting all four sources =="
for src in crm_a crm_b whatsapp_calls personal_notes; do
  status=$(curl -sS -X POST "$HOST/connectors/$src/connect" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  [ "$status" = "connected" ] || fail "connect $src -> $status"
done
pass "connect x4"

consumers=$(curl -sS "$HOST/consumers")
colin_cap=$(echo "$consumers" | python3 -c "import sys,json; print(next(c['cap_id'] for c in json.load(sys.stdin) if c['consumer_id']=='colin'))")
acme_cap=$(echo "$consumers" | python3 -c "import sys,json; print(next(c['cap_id'] for c in json.load(sys.stdin) if c['consumer_id']=='acme'))")

echo "== owner deal_status: signal + conflict_ordered (VP vs Director, most-recent default) =="
owner_deal=$(curl -sS -X POST "$HOST/ask" -H 'content-type: application/json' \
  -d "{\"cap_id\":\"$colin_cap\",\"question_id\":\"deal_status\"}")
echo "$owner_deal" | python3 -c "
import sys, json
body = json.load(sys.stdin)
kinds = {c['kind'] for c in body['cards']}
assert 'signal' in kinds, body
role = next((c for c in body['cards'] if c['kind']=='conflict_ordered' and c.get('ontology_node')=='role'), None)
assert role is not None, body
assert role['values'][role['default_selection']]['value'] == 'VP Engineering', role
"
pass "owner deal_status"

echo "== acme deal_status: no personal_notes content; open_transcript refused =="
acme_deal=$(curl -sS -X POST "$HOST/ask" -H 'content-type: application/json' \
  -d "{\"cap_id\":\"$acme_cap\",\"question_id\":\"deal_status\"}")
echo "$acme_deal" | python3 -c "
import sys, json
dump = sys.stdin.read()
assert 'personal_notes' not in dump
assert 'mortgage' not in dump.lower()
"
acme_transcript=$(curl -sS -X POST "$HOST/ask" -H 'content-type: application/json' \
  -d "{\"cap_id\":\"$acme_cap\",\"question_id\":\"open_transcript\"}")
echo "$acme_transcript" | python3 -c "
import sys, json
body = json.load(sys.stdin)
assert body['answer_kind'] == 'refusal', body
assert body['cards'][0]['message'] == 'not available to you', body
"
pass "acme deal_status has no personal_notes; transcript refused"

echo "== owner open_transcript: file content =="
owner_transcript=$(curl -sS -X POST "$HOST/ask" -H 'content-type: application/json' \
  -d "{\"cap_id\":\"$colin_cap\",\"question_id\":\"open_transcript\"}")
echo "$owner_transcript" | python3 -c "
import sys, json
body = json.load(sys.stdin)
assert body['answer_kind'] == 'agreed', body
assert 'Femi' in body['cards'][0]['value'], body
"
pass "owner open_transcript returns transcript content"

echo "== partner: activate, sees only the two shared rows =="
partner_cap=$(curl -sS -X PATCH "$HOST/consumers/partner" -H 'content-type: application/json' \
  -d '{"active": true}' | python3 -c "import sys,json; print(json.load(sys.stdin)['cap_id'])")
curl -sS "$HOST/graph?cap_id=$partner_cap" | python3 -c "
import sys, json
g = json.load(sys.stdin)
sources = {n['label'] for n in g['nodes'] if n['kind']=='source'}
assert sources == {'CRM A', 'CRM B'}, sources
"
pass "partner sees only crm_a/crm_b"

echo "== acme deactivate: empty graph, refusal/absent on ask =="
curl -sS -X PATCH "$HOST/consumers/acme" -H 'content-type: application/json' -d '{"active": false}' > /dev/null
status=$(curl -sS -o /dev/null -w "%{http_code}" "$HOST/graph?cap_id=$acme_cap")
[ "$status" = "404" ] || fail "stale acme cap_id should 404 on /graph, got $status"
pass "acme deactivate revokes the stale cap_id"

echo "ALL SMOKE CHECKS PASSED"
