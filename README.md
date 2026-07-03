# Context Twin

A governed digital twin. The core idea: **facts carry references and types, never values.**
The map holds typed reference-cells; a value only ever materialises when it is fetched under a
capability that the cell's policy allows, and the fetch is audited. This lets a single twin serve
many viewers, each seeing only what their capability permits — fail-closed by default.

Built by five units against a **frozen** contract (`contract.py`) over a shared SQLite substrate
(`twin.db`). Each unit implements the seam interface(s) it owns and imports only interfaces
(never another unit's concrete class) for what it consumes, so all five build in parallel.

| Unit | Dir | Owns |
|------|-----|------|
| 1 — Twin Core | `twin_core/` | `MasterTableStore`, cell construction, reference inversion |
| 2 — Onboarding | `onboarding/` | `Onboarder`, `ControlPlane`, classifier, concept router (ontology) |
| 3 — Resolution | `resolution/` | `Gate`, `Projector`, `Resolver`, `PolicyRegistry`, `GroupingOverlay`, semantic descent |
| 4 — Fetch | `fetch/` | `SourceReader`, `Adapter`, `FetchLadder`, conflict + cross-source join |
| 5 — Audit | `audit/` | `AuditSink` |

## Layout

```
contract.py     FROZEN types + seam interfaces. Changed only by deliberate, owner-flagged amendment.
locators.py     FROZEN behaviour. The ONE place a cell locator is minted/parsed.
db.py           sqlite connection helper + init_all()
embed.py         the ONE shared embedder (fastembed bge-small) — single MODEL_NAME pin, one space
ontology.yaml   hand-authored nodes (deliberately omits `region` — an intentional gap) + concept map
seed/           make_seed.py -> crm_a.csv, crm_b.csv (two divergent CRMs, planted identities)
twin_core/      Unit 1 — store, build_cells, reference inversion, seed_grouping, MasterTable
onboarding/     Unit 2 — onboarding orchestrator, shortlist+judge classifier, control plane, concept router
resolution/     Unit 3 — gate, projection, banded resolver, GroupingOverlay, capabilities, semantic descent
fetch/          Unit 4 — CSV source reader, fetch ladder (Gate 2), conflict object, cross-source join
audit/          Unit 5 — write-only, tamper-evident audit sink
demo.py         Money-shot wiring harness (the demo entrypoint) — composes all five units
tests/          acceptance tests (per-unit) + the end-to-end money-shot test
```

## Locators & identity grouping (shared foundation — read before minting)

A **locator is opaque**: `source:row_key:field`, each component percent-encoded so a `:`
inside a component can never be mistaken for the separator. It says *where* a value lives,
never *who* it belongs to. **Always** mint/parse via `locators.make_locator` / `parse_locator`
— never hand-format or `split(":")` a locator anywhere. There is deliberately no
`principal_id_of(locator)`: deriving identity from a locator is impossible by construction.

**Identity grouping is a separate, overridable column** on `cells`, set at write time — not
encoded in the locator:

- `store.put_cell(cell)` — frozen contract path; writes the cell **ungrouped** (`principal_id`
  NULL).
- `store.put_cell_for(principal_id, cell)` — write with a known grouping. Re-put with `None`
  never clobbers an existing grouping (`COALESCE`).
- `store.set_grouping(cell_id, principal_id)` — re-group live. Unit 3 calls this **only** under
  a capability authorising durable re-grouping; by default query-time resolution keeps its merge
  in an in-memory `GroupingOverlay` and persists nothing.
- `twin_core.seed_grouping(records)` — cheap deterministic mint-time seed (exact normalised-email
  match; no embeddings, no LLM). Groups Colin (shared email) at ingest; deliberately leaves Dana
  (blank second email) for the live resolver to merge on stage.

Each unit's contract with this seam: Unit 4 `read_value` parses `ref.locator` via `parse_locator`
to get `(source, row_key, field)` — for the week `row_key` is the CSV row id. Unit 3 decides
identity from values under the gate and applies groupings (overlay by default, `set_grouping`
only when the capability permits). No unit reads identity from a locator.

## The money shot (live resolution across two un-merged CRMs)

The demo resolves one person **live** across two un-merged CRMs, joins across both under a
capability, and persists nothing. `demo.py` (`TwinDemo`) is the thin wiring harness that composes
all five **real** units on one sqlite connection and one `AuditSink`, threading **one**
`GroupingOverlay` through `resolve → join`:

1. **Descend** — `SemanticDescent.query("Colin", cap, ctx)` resolves the query to a concept
   (`person`), routes concept → sources via the ontology map, and surfaces candidate refs under
   the caller's projection (Gate 1). Concept-first routing prunes by *source*, not a post-filter.
2. **Resolve** — `Resolver.resolve(refs, cap, ctx, overlay)` merges Dana's two records (no shared
   email → banded name+org match, LLM adjudicator for the middle band) into the **overlay**.
   Persists nothing; a wrong match is scoped to one query under one capability.
3. **Join** — `cross_source_query(store, overlay, pid, node, cap, ctx, ladder, registry, reader)`
   reads **through `overlay.cells_for`** (the Dana rule), fetches each value at Gate 2, and returns
   a `ConflictSet` (`agreed` / `conflict_ordered` / `conflict_unordered`). The same query under a
   less-privileged capability returns a flat `Refusal` — the governance climax.

`tests/test_e2e_moneyshot.py` asserts this whole path end to end, plus the system-wide invariants
unit tests structurally cannot: persists-nothing (+ reproducible from a fresh overlay), no naked
cached values (every materialised value carries `fetched_under`), no PII on the audit log, and a
tamper-evident chain (`verify_chain()`). It is offline and deterministic — the two LLM
adjudicators use the units' shipped fakes.

## Setup

```bash
uv venv && uv pip install -e ".[dev]"     # or: pip install -e ".[dev]"
cp .env.example .env                        # ANTHROPIC_API_KEY only for LIVE adjudicators (tests are offline)
python seed/make_seed.py                     # generate the seed CSVs
uv run pytest                                # full suite (all units + descent + e2e + integration fixes)
```

## The seed (what the demo lives on)

Two CRMs with divergent schemas and planted structure:

- **Colin Marsh** — shared email across A/B → AUTO-band resolution; `title` vs `job_role`, both
  dated → `conflict_ordered`.
- **Dana Osei** — no shared email (B email blank) → middle-band (LLM) resolution on name + org;
  one title undated → `conflict_unordered`.
- **Colin Marsh-Jones** — near-miss at a different org; must NOT resolve to Colin Marsh.
- `region` (crm_b only) — has no ontology node → defers/proposes via the control plane (Unit 2),
  never silently minted.

## Contract amendments (deliberate, owner-sanctioned)

`contract.py` is frozen; changes to it are visible, deliberate events. Two amendments landed after
the initial freeze to reconcile the seam with the overlay design that arrived later:

1. **`Resolver.resolve` carries an explicit, mandatory `overlay: GroupingOverlay` parameter.** The
   overlay is threaded through `resolve → join` as a named parameter (no `ctx` side-channel), so a
   forgotten overlay is a loud error, never a silent no-overlay run.
2. **`GroupingOverlay` is a contract `Protocol`.** It is a cross-unit seam (Unit 3 owns the
   concrete impl, Unit 4 consumes it), so its type lives in `contract.py` alongside `SourceReader`
   / `Gate` / `MasterTableStore` / `AuditSink`; the layering points down only (units → contract).

**Fail-closed policy lookup:** `PolicyRegistry.get` returns a deny-all policy for an unknown
`policy_id` (counted + logged), never `KeyError` — an unregistered policy is a governance gap and
fails closed. A registered `"default"` policy (map-visible, dereference denied) keeps the mint
path intentional.

**One shared embedder:** `embed.py` (fastembed `bge-small-en-v1.5`) is the single canonical
embedder for the whole system. Classification (Unit 2) and entity-resolution (Unit 3) previously
carried their own copies pinning the same model; they were consolidated so there is exactly **one**
`MODEL_NAME` pin and one embedding space — the two can never silently drift apart on a model bump.
A determinism test asserts the same string embeds bit-for-bit identically through both units' paths.

## Status

All five units built and merged against the frozen contract; the money-shot end-to-end test is
green. **Full suite: 64 tests pass** (per-unit acceptance + semantic descent + e2e money shot +
integration fixes + shared-embedder determinism), offline and deterministic.

- **Unit 0 (shared foundation)** — contract, `locators.py`, db, `embed.py`, ontology, seed, packaging.
- **Unit 1 (twin core)** — `SqliteMasterTableStore` (overridable grouping), `build_cells`,
  `seed_grouping`, `MasterTable`.
- **Unit 2 (onboarding)** — lazy onboarding, shortlist+judge classifier, control plane, concept
  router.
- **Unit 3 (resolution)** — gate, projection, banded query-time resolver, `GroupingOverlay`,
  capabilities, semantic descent.
- **Unit 4 (fetch)** — `CsvSourceReader`, fetch ladder (Gate 2), conflict object, cross-source join.
- **Unit 5 (audit)** — write-only, tamper-evident audit sink.
- **Integration** — `demo.py` money-shot harness + `tests/test_e2e_moneyshot.py`; fail-closed
  policy lookup, the two contract amendments above, and the shared `embed.py` embedder.
