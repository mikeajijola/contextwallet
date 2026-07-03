# Context Wallet

A web console on top of the Context Twin governance engine: connect sources, decide who can
see what, and ask three questions that render differently depending on who's asking.

## Run it

First-time offline setup: `python scripts/fetch_model.py` once with network; the app then runs fully offline.

```bash
# from the repo root, once
uv venv && uv pip install -e ".[dev]"
python scripts/fetch_model.py        # one-time, needs network — vendors the embedder locally
python seed/make_seed.py

# terminal 1 — the API
uvicorn wallet.api:app --port 8000

# terminal 2 — the UI
cd wallet/ui
npm install
npm run dev
```

Open `http://localhost:5173`. Delete `wallet.db` (repo root) to reset all state — the seed
data regenerates the same way every time.

Note: the ontology classifier's shortlist step uses a real embedding model
(`fastembed`/`BAAI/bge-small-en-v1.5`) — the same embedder the engine's own Unit 2 tests
depend on, not something the wallet layer adds. `scripts/fetch_model.py` vendors it into
`models/` (repo-local, gitignored) once with network; after that, `embed.py` forces
`HF_HUB_OFFLINE=1` and loads the identical weights with zero network calls, so connecting a
source works the same whether or not `huggingface.co` is reachable.

## Click path

1. **Connect ×4** — click Connect on all four connector cards (CRM A, CRM B, Personal notes,
   WhatsApp calls). Each shows its onboarding report (auto/flagged/deferred) and an amber
   `region — proposed` chip once CRM B connects. The map grows as each source lands.
2. **VIEW AS Colin's agent → Deal status** — a role conflict (VP Engineering vs Director,
   Platform, most-recent selected), plus the WhatsApp signal card (participants, channel,
   topic). Click **Open transcript** on the signal card to reveal the call transcript inline.
3. **VIEW AS Acme org → Deal status** — the same signal card appears, but the map has zero
   `Personal notes` cells, and **Open transcript** now returns the flat refusal card
   (`not available to you`). Switch back to Colin and click the same button — transcript
   content appears.
4. **ACCESS: flip External partner ON** — switch to that viewer; the map shows only the two
   rows Partner was granted (Colin's `a1`/`b1` cells across CRM A/CRM B), nothing else.
5. **ACCESS: flip Acme org OFF** — switch to Acme; the map is empty and any Ask returns
   refusal/absent, and its VIEW AS pill shows a `revoked` tag.

## Layout

Header (identity + VIEW AS pills) → left rail (Connectors) → centre (the map, redrawn per
viewer) → right rail (Access over Ask). Desktop/projector only — no mobile layout.

## Cut ladder (if a demo run needs to shed scope)

See `AGENT_BUILD_BRIEF.md` §5 for the full ladder. In short: never cut the four beats, the
two toggle levels, or leak discipline. Map diff animation, onboarding-report chips, and
per-source toggles are the first things to simplify. If a SQLite edge misbehaves, flip that
source's `fmt` to `csv` in `wallet/registry.py` and add its rows to `seed/make_seed.py`.
