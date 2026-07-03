"""The wallet console's thin API — a FastAPI server wrapping the real engine (`TwinDemo`)
plus the wallet's own additive layer (`wallet/`). Single process, in-memory consumer table +
one sqlite conn. No live LLM calls on this path (non-negotiable #7): the classifier judge and
the same-person adjudicator are the offline fakes in `wallet/adjudicators.py`.
"""
from __future__ import annotations
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from contract import ConflictSet, Refusal, Symbol
from locators import field_of, source_of, row_key_of
from resolution.capability import mint
from resolution.gate import PredicateGate
from resolution.descent import SemanticDescent
from resolution.resolver import GroupingOverlay
from demo import TwinDemo
from wallet.adjudicators import ValueJudge, CountingSame
from wallet.multi_reader import MultiSourceReader
from wallet.registry import SOURCES, ALL_SOURCES
from wallet.setup import onboard_one, CONNECT_ORDER
from wallet.wallet_projector import WalletProjector
from wallet.wallet_join import cross_source_query as wallet_cross_source_query
from wallet.fetch import resolve_value

TRANSCRIPT_DIR = Path(__file__).resolve().parent.parent / "seed" / "transcripts"

# Colin's preset row across all four sources — the identity the deal-status question walks.
COLIN_ROWS = {"crm_a": "a1", "crm_b": "b1", "whatsapp_calls": "c1", "personal_notes": "n1"}


# ============================================================================= app state
def _wallet_conn(path: str = "wallet.db") -> sqlite3.Connection:
    """A dedicated connection for the wallet server, not `db.get_conn` — the server is a
    single logical thread of control (async-def routes on one event loop; atomic by
    construction) but different ASGI servers/test harnesses may dispatch it onto different
    OS threads, so `check_same_thread=False` here (never touching the frozen engine's
    connection helper)."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF;")
    return conn


class WalletState:
    def __init__(self, db_path: str = "wallet.db") -> None:
        self.conn = _wallet_conn(db_path)
        self.gate = PredicateGate()
        self.reader = MultiSourceReader()
        self.demo = TwinDemo(self.conn, judge=ValueJudge(), adjudicator=CountingSame(same=True),
                            gate=self.gate, reader=self.reader)
        self.wproj = WalletProjector(self.demo.registry, self.gate, audit=self.demo.audit,
                                     control_plane=self.demo.control_plane)
        # a SEPARATE SemanticDescent wired to the WalletProjector (not demo.descent, which
        # carries the engine's plain Projector) so descent's internal projection step honours
        # the wallet's per-cell ctx injection (gate.py trap #3).
        self.descent = SemanticDescent(self.demo.router, self.demo.store, projector=self.wproj)

        self.connected: dict[str, bool] = {s: False for s in ALL_SOURCES}
        self.reports: dict[str, object] = {}
        self.consumers: dict[str, dict] = {
            "colin": dict(consumer_id="colin", label="Colin's agent", holder="colin",
                         owner=True, active=True, sources={s: True for s in ALL_SOURCES},
                         rows=None, cap=None),
            "acme": dict(consumer_id="acme", label="Acme org", holder="acme-assistant",
                        owner=False, active=True,
                        sources={"crm_a": True, "crm_b": True, "whatsapp_calls": True},
                        rows=None, cap=None),
            "partner": dict(consumer_id="partner", label="External partner", holder="partner",
                            owner=False, active=False, sources={"crm_a": True, "crm_b": True},
                            rows=[("crm_a", "a1"), ("crm_b", "b1")], cap=None),
        }
        for consumer_id in self.consumers:
            remint(self, consumer_id)


def remint(state: WalletState, consumer_id: str) -> None:
    """Revoke any existing cap, then (if active) mint a fresh one from current caveats.
    Master OFF = revoke without re-mint; master ON = remint. Atomic by construction
    (single-threaded FastAPI dev server)."""
    c = state.consumers[consumer_id]
    if c["cap"] is not None:
        state.gate.revoke(c["cap"].id())
        c["cap"] = None
    if not c["active"]:
        return
    if c["rows"]:
        caveats = [f"share:{s}:{r}" for s, r in c["rows"] if c["sources"].get(s, True)]
    else:
        caveats = [f"src:{s}" for s, enabled in c["sources"].items() if enabled]
    c["cap"] = mint(c["holder"], "wallet_query", caveats)


def reset_state(db_path: str = "wallet.db") -> WalletState:
    """(Re)build the module-level state against a fresh connection — used by tests to get
    an isolated wallet per test (e.g. `reset_state(':memory:')`). Route handlers look up the
    module global `state` at CALL time, so reassigning it here is picked up immediately."""
    global state
    state = WalletState(db_path)
    return state


state: Optional[WalletState] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # deferred to app STARTUP, not import time: constructing TwinDemo builds the ontology
    # classifier, which eagerly embeds every exemplar — heavy work (and, offline, a fastembed
    # model load) that must never run as a bare module-import side effect.
    if state is None:
        reset_state("wallet.db")
    yield


app = FastAPI(title="Context Wallet API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================= helpers
def _get_consumer(consumer_id: str) -> dict:
    c = state.consumers.get(consumer_id)
    if c is None:
        raise HTTPException(404, "unknown consumer")
    return c


def _cap_for(cap_id: str):
    for c in state.consumers.values():
        if c["cap"] is not None and c["cap"].id() == cap_id:
            return c["cap"]
    raise HTTPException(404, "unknown or stale cap_id")


def _consumer_row(c: dict) -> dict:
    row = {
        "consumer_id": c["consumer_id"], "label": c["label"], "owner": c["owner"],
        "active": c["active"], "cap_id": c["cap"].id() if c["cap"] else None,
        "sources": [],
    }
    for source, enabled in c["sources"].items():
        entry = {"source": source, "label": SOURCES[source]["label"], "enabled": enabled}
        if c["consumer_id"] == "acme" and source == "whatsapp_calls":
            entry["note"] = "signal — transcript locked"
        row["sources"].append(entry)
    return row


def _connector_row(source: str) -> dict:
    row = {"source": source, "label": SOURCES[source]["label"],
          "status": "connected" if state.connected[source] else "available"}
    if state.connected[source]:
        report = state.reports[source]
        row["report"] = {
            "auto": len(report.auto_fields),
            "flagged": sum(1 for b in report.bands.values() if b == "flag"),
            "deferred": len(report.deferred),
            "proposals": [{"field": f, "status": state.demo.control_plane.status_of(cp_id) or "proposed"}
                         for f, cp_id in report.proposals.items()],
        }
    return row


def _read_transcript_pointer(pointer: str) -> str:
    # wa_store://call_c1 -> seed/transcripts/call_c1.txt (the two-step: gate -> pointer -> file)
    name = pointer.split("://", 1)[-1]
    path = TRANSCRIPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _fetch_cell_value(cap, ctx: dict, cell) -> dict:
    """Gate -> (pointer -> file, if this is a transcript cell) -> value. Shared by POST
    /fetch and the open_transcript /ask question."""
    proj = state.wproj.project([cell], cap, ctx)
    pcell = proj.cells.get(cell.cell_id)
    if pcell is None:
        return {"refusal": "not available to you"}
    value = resolve_value(state.demo.ladder, pcell, cap, ctx)
    if isinstance(value, Refusal) or isinstance(value, Symbol):
        return {"refusal": "not available to you"}
    if cell.type.ontology_node == "transcript":
        return {"value": _read_transcript_pointer(value)}
    return {"value": value}


def _conflict_card(cs: ConflictSet) -> dict:
    values = [{"value": v.value, "source": v.source,
              "date": v.timestamp.date().isoformat() if v.timestamp else None} for v in cs.values]
    return {"kind": cs.status, "ontology_node": cs.ontology_node, "values": values,
           "default_selection": cs.default_selection}


def _refusal_card() -> dict:
    return {"kind": "refusal", "message": "not available to you"}


# ============================================================================= /connectors
@app.get("/connectors")
async def list_connectors():
    return [_connector_row(s) for s in ALL_SOURCES]


@app.post("/connectors/{source}/connect")
async def connect_source(source: str):
    if source not in ALL_SOURCES:
        raise HTTPException(404, "unknown source")
    if not state.connected[source]:
        state.reports[source] = onboard_one(state.demo, source)
        state.connected[source] = True
    return _connector_row(source)


# ============================================================================= /consumers
@app.get("/consumers")
async def list_consumers():
    return [_consumer_row(c) for c in state.consumers.values()]


class ActivePatch(BaseModel):
    active: bool


@app.patch("/consumers/{consumer_id}")
async def set_active(consumer_id: str, patch: ActivePatch):
    c = _get_consumer(consumer_id)
    if c["owner"]:
        raise HTTPException(400, "cannot toggle the owner")
    c["active"] = patch.active
    remint(state, consumer_id)
    return _consumer_row(c)


class SourcePatch(BaseModel):
    source: str
    enabled: bool


@app.patch("/consumers/{consumer_id}/sources")
async def set_source(consumer_id: str, patch: SourcePatch):
    c = _get_consumer(consumer_id)
    if c["owner"]:
        raise HTTPException(400, "cannot toggle the owner")
    if patch.source not in c["sources"]:
        raise HTTPException(404, "source not available to this consumer")
    c["sources"][patch.source] = patch.enabled
    remint(state, consumer_id)
    return _consumer_row(c)


# ============================================================================= /graph
@app.get("/graph")
async def graph(cap_id: str):
    cap = _cap_for(cap_id)
    ctx = {"purpose": cap.purpose}
    all_cells = state.demo.store.all_cells()
    projected = state.wproj.project(all_cells, cap, ctx)

    # durable grouping ONLY (overlay principals are per-request and never appear here).
    principal_of_cell: dict[str, str] = {}
    for pid, cell in state.demo.store.all_with_grouping():
        if pid is not None:
            principal_of_cell[cell.cell_id] = pid

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_sources: set[str] = set()
    seen_nodes: set[str] = set()
    seen_principals: set[str] = set()

    for cell_id, pcell in projected.cells.items():
        source = source_of(pcell.ref.locator)
        if source not in seen_sources:
            seen_sources.add(source)
            nodes.append({"id": f"source:{source}", "label": SOURCES[source]["label"], "kind": "source"})

        nodes.append({"id": f"cell:{cell_id}", "label": field_of(pcell.ref.locator), "kind": "cell"})
        edges.append({"source": f"cell:{cell_id}", "target": f"source:{source}", "kind": "sourced_from"})

        if pcell.type is not None:
            node_name = pcell.type.ontology_node
            if node_name not in seen_nodes:
                seen_nodes.add(node_name)
                nodes.append({"id": f"ontology:{node_name}", "label": node_name, "kind": "ontology"})
            edges.append({"source": f"cell:{cell_id}", "target": f"ontology:{node_name}", "kind": "classified_as"})

        pid = principal_of_cell.get(cell_id)
        if pid is not None:
            if pid not in seen_principals:
                seen_principals.add(pid)
                nodes.append({"id": f"principal:{pid}", "label": f"p·{pid[-4:]}", "kind": "principal"})
            edges.append({"source": f"cell:{cell_id}", "target": f"principal:{pid}", "kind": "belongs_to"})

    return {"nodes": nodes, "edges": edges}


# ============================================================================= /ask
class AskRequest(BaseModel):
    cap_id: str
    question_id: str


def _deal_status(cap, ctx: dict) -> dict:
    descent = state.descent.query("Colin", cap, ctx)
    if descent.projected is None:
        return {"answer_kind": "absent", "cards": []}

    # TRAP #2 (pre-solved): candidate_refs is NOT projection-filtered — always intersect
    # before resolving, or a viewer's resolution could read identities it cannot see.
    projected_locators = {pc.ref.locator for pc in descent.projected.cells.values() if pc.ref}
    refs = [r for r in descent.candidate_refs if r.locator in projected_locators]
    refs = [r for r in refs
           if (source_of(r.locator), row_key_of(r.locator)) in
              {(s, rk) for s, rk in COLIN_ROWS.items()}]

    cards: list[dict] = []
    overlay = GroupingOverlay()   # fresh every /ask call — nothing about resolution persists

    principal_id: Optional[str] = None
    if refs:
        rp = state.demo.resolve(refs, cap, ctx, overlay)
        if isinstance(rp, Refusal):
            return {"answer_kind": "refusal", "cards": [_refusal_card()]}
        principal_id = rp.principal_id
        role_cs = wallet_cross_source_query(state.demo.store, overlay, principal_id, "role",
                                            cap, ctx, state.demo.ladder, state.demo.registry,
                                            state.demo.reader, state.wproj)
        if isinstance(role_cs, Refusal):
            return {"answer_kind": "refusal", "cards": [_refusal_card()]}
        if isinstance(role_cs, ConflictSet) and role_cs.values:
            cards.append(_conflict_card(role_cs))

        topic_cs = wallet_cross_source_query(state.demo.store, overlay, principal_id, "topic",
                                             cap, ctx, state.demo.ladder, state.demo.registry,
                                             state.demo.reader, state.wproj)
        if isinstance(topic_cs, Refusal):
            return {"answer_kind": "refusal", "cards": [_refusal_card()]}
        if isinstance(topic_cs, ConflictSet) and topic_cs.values:
            cards.append(_conflict_card(topic_cs))

    # the call signal — assembled directly from the call row's projected cells, independent
    # of the person-node descent above (whatsapp_calls is never a `person`-concept source).
    signal = _signal_card(cap, ctx)
    if signal is not None:
        cards.append(signal)

    if not cards:
        return {"answer_kind": "absent", "cards": []}
    return {"answer_kind": cards[0]["kind"], "cards": cards}


def _signal_card(cap, ctx: dict) -> Optional[dict]:
    all_cells = state.demo.store.all_cells()
    row_cells = {field_of(c.ref.locator): c for c in all_cells
                if source_of(c.ref.locator) == "whatsapp_calls" and row_key_of(c.ref.locator) == "c1"}
    if not row_cells:
        return None
    projected = state.wproj.project(list(row_cells.values()), cap, ctx)
    if not all(f in row_cells and row_cells[f].cell_id in projected.cells
              for f in ("topic", "channel", "participant")):
        return None

    def _val(field: str):
        pcell = projected.cells[row_cells[field].cell_id]
        v = resolve_value(state.demo.ladder, pcell, cap, ctx)
        return None if isinstance(v, (Refusal, Symbol)) else v

    topic, channel, participant = _val("topic"), _val("channel"), _val("participant")
    if topic is None or channel is None or participant is None:
        return None

    transcript_cell = row_cells.get("transcript_ref")
    transcript_cell_id = (transcript_cell.cell_id
                          if transcript_cell is not None and transcript_cell.cell_id in projected.cells
                          else None)
    return {"kind": "signal", "participants": participant, "channel": channel, "topic": topic,
           "follow_up": None, "transcript_cell_id": transcript_cell_id}


def _open_transcript(cap, ctx: dict) -> dict:
    all_cells = state.demo.store.all_cells()
    cell = next((c for c in all_cells if source_of(c.ref.locator) == "whatsapp_calls"
                and row_key_of(c.ref.locator) == "c1" and field_of(c.ref.locator) == "transcript_ref"),
               None)
    if cell is None:
        return {"answer_kind": "absent", "cards": []}
    result = _fetch_cell_value(cap, ctx, cell)
    if "refusal" in result:
        return {"answer_kind": "refusal", "cards": [_refusal_card()]}
    return {"answer_kind": "agreed", "cards": [{"kind": "agreed", "value": result["value"],
                                                "source": "whatsapp_calls", "date": None}]}


def _whats_private(cap, ctx: dict) -> dict:
    all_cells = state.demo.store.all_cells()
    note_cells = [c for c in all_cells
                 if source_of(c.ref.locator) == "personal_notes" and field_of(c.ref.locator) == "body"]
    if not note_cells:
        return {"answer_kind": "absent", "cards": []}
    projected = state.wproj.project(note_cells, cap, ctx)
    visible = [c for c in note_cells if c.cell_id in projected.cells]
    if not visible:
        return {"answer_kind": "absent", "cards": []}

    cards = []
    for cell in visible:
        v = resolve_value(state.demo.ladder, projected.cells[cell.cell_id], cap, ctx)
        if isinstance(v, Refusal):
            return {"answer_kind": "refusal", "cards": [_refusal_card()]}
        if isinstance(v, Symbol):
            continue
        cards.append({"kind": "agreed", "value": v, "source": "personal_notes", "date": None})
    if not cards:
        return {"answer_kind": "absent", "cards": []}
    return {"answer_kind": cards[0]["kind"], "cards": cards}


_QUESTIONS = {"deal_status": _deal_status, "open_transcript": _open_transcript,
             "whats_private": _whats_private}


@app.post("/ask")
async def ask(body: AskRequest):
    cap = _cap_for(body.cap_id)
    if body.question_id not in _QUESTIONS:
        raise HTTPException(404, "unknown question_id")
    ctx = {"purpose": cap.purpose}
    return _QUESTIONS[body.question_id](cap, ctx)


# ============================================================================= /fetch
class FetchRequest(BaseModel):
    cap_id: str
    cell_id: str


@app.post("/fetch")
async def fetch(body: FetchRequest):
    cap = _cap_for(body.cap_id)
    ctx = {"purpose": cap.purpose}
    cell = next((c for c in state.demo.store.all_cells() if c.cell_id == body.cell_id), None)
    if cell is None:
        raise HTTPException(404, "unknown cell_id")
    return _fetch_cell_value(cap, ctx, cell)
