"""The wallet console's thin API — a FastAPI server wrapping the real engine (`TwinDemo`)
plus the wallet's own additive layer (`wallet/`). Single process, in-memory consumer table +
one sqlite conn. No live LLM calls on this path (non-negotiable #7): the classifier judge and
the same-person adjudicator are the offline fakes in `wallet/adjudicators.py`.
"""
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
            "colin": dict(consumer_id="colin", label="Colin", holder="colin",
                         owner=True, active=True, sources={s: True for s in ALL_SOURCES},
                         rows=None, cap=None),
            "acme": dict(consumer_id="acme", label="Acme org", holder="acme-assistant",
                        owner=False, active=True,
                        sources={"crm_a": True, "crm_b": True, "whatsapp_calls": True, "personal_notes": False},
                        rows=None, cap=None),
            "partner": dict(consumer_id="partner", label="External partner", holder="partner",
                            owner=False, active=False,
                            sources={"crm_a": True, "crm_b": True, "whatsapp_calls": False, "personal_notes": False},
                            # row-share, not source-share: crm_a/crm_b rows are dereferenceable
                            # (org_work); whatsapp_calls/personal_notes rows are existence-only
                            # even when toggled on (org_signal/owner_private are owner-gated for
                            # dereference) — flipping these switches lets Partner see that a
                            # signal/note exists, never its content.
                            rows=[("crm_a", "a1"), ("crm_b", "b1"),
                                 ("whatsapp_calls", "c1"), ("personal_notes", "n1")], cap=None),
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
    allow_origins=["*"],
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


# sources whose toggle only ever grants ontology/existence — the actual value stays
# owner-gated (org_signal for whatsapp_calls' non-transcript fields, owner_private for
# personal_notes/chat_history), regardless of which non-owner consumer flips it on.
_EXISTENCE_ONLY_SOURCES = {"whatsapp_calls", "personal_notes", "chat_history"}


def _consumer_row(c: dict) -> dict:
    row = {
        "consumer_id": c["consumer_id"], "label": c["label"], "owner": c["owner"],
        "active": c["active"], "cap_id": c["cap"].id() if c["cap"] else None,
        "sources": [],
    }
    is_row_scoped = bool(c["rows"])
    for source, enabled in c["sources"].items():
        entry = {"source": source, "label": SOURCES[source]["label"], "enabled": enabled}
        if not c["owner"] and source in _EXISTENCE_ONLY_SOURCES:
            if is_row_scoped:
                entry["note"] = "existence only — only Colin reads the data"
            elif source == "whatsapp_calls":
                entry["note"] = "signal — transcript locked"
            else:
                entry["note"] = "existence only — only Colin reads the data"
        row["sources"].append(entry)
    return row


def _connector_row(source: str) -> dict:
    row = {"source": source, "label": SOURCES[source]["label"],
          "status": "connected" if state.connected[source] else "available"}
    if state.connected[source]:
        report = state.reports[source]
        
        schema_fields = []
        for f, band in report.bands.items():
            entry = {"name": f, "band": band}
            node = report.proposed_nodes.get(f)
            if node:
                entry["node"] = node
            if f in report.proposals:
                cp_id = report.proposals[f]
                entry["status"] = state.demo.control_plane.status_of(cp_id) or "proposed"
            elif band == "auto":
                entry["status"] = "auto"
            else:
                entry["status"] = "deferred"
            schema_fields.append(entry)
            
        row["report"] = {
            "auto": len(report.auto_fields),
            "flagged": sum(1 for b in report.bands.values() if b == "flag"),
            "deferred": len(report.deferred),
            "proposals": [{"field": f, "status": state.demo.control_plane.status_of(cp_id) or "proposed"}
                         for f, cp_id in report.proposals.items()],
            "schema": schema_fields,
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
    if type(value).__name__ in ("Refusal", "Symbol"):
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


@app.post("/connectors/{source}/disconnect")
async def disconnect_source(source: str):
    if source not in ALL_SOURCES:
        raise HTTPException(404, "unknown source")
    if state.connected[source]:
        # Clear out the cells for this source
        state.demo.store.conn.execute("DELETE FROM cells WHERE source = ?", (source,))
        state.demo.store.conn.commit()
        state.connected[source] = False
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
        if type(rp).__name__ == "Refusal":
            return {"answer_kind": "refusal", "cards": [_refusal_card()]}
        principal_id = rp.principal_id
        role_cs = wallet_cross_source_query(state.demo.store, overlay, principal_id, "role",
                                            cap, ctx, state.demo.ladder, state.demo.registry,
                                            state.demo.reader, state.wproj)
        if type(role_cs).__name__ == "Refusal":
            return {"answer_kind": "refusal", "cards": [_refusal_card()]}
        if isinstance(role_cs, ConflictSet) and role_cs.values:
            cards.append(_conflict_card(role_cs))

        topic_cs = wallet_cross_source_query(state.demo.store, overlay, principal_id, "topic",
                                             cap, ctx, state.demo.ladder, state.demo.registry,
                                             state.demo.reader, state.wproj)
        if type(topic_cs).__name__ == "Refusal":
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
        return None if type(v).__name__ in ("Refusal", "Symbol") else v

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
        if type(v).__name__ == "Refusal":
            return {"answer_kind": "refusal", "cards": [_refusal_card()]}
        if type(v).__name__ == "Symbol":
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

# ============================================================================= /chat
from wallet.chat import get_session_history, save_message, create_session, list_sessions, client as genai_client
from google.genai import types

class ChatRequest(BaseModel):
    cap_id: str
    message: str

@app.get("/chat/sessions")
async def get_chat_sessions():
    return list_sessions(str(TRANSCRIPT_DIR.parent / "chat_history.db"))

@app.post("/chat/sessions")
async def new_chat_session():
    session_id = create_session(str(TRANSCRIPT_DIR.parent / "chat_history.db"))
    return {"session_id": session_id}

@app.get("/chat/sessions/{session_id}")
async def get_chat_history(session_id: str):
    return get_session_history(str(TRANSCRIPT_DIR.parent / "chat_history.db"), session_id, limit=50)

@app.post("/chat/sessions/{session_id}/message")
async def send_chat_message(session_id: str, body: ChatRequest):
    cap = _cap_for(body.cap_id)
    ctx = {"purpose": cap.purpose}
    db_path = str(TRANSCRIPT_DIR.parent / "chat_history.db")
    
    # Save user message
    save_message(db_path, session_id, "user", body.message)
    
    # Get last 7 messages
    history = get_session_history(db_path, session_id, limit=7)
    
    # Define tool
    # NODE excluded from the general dump: `transcript` cells hold a raw edge pointer
    # (e.g. "wa_store://call_c1"), not text worth surfacing — the UI's dedicated "Open
    # transcript" button does the pointer -> file read; a bare pointer string isn't a fact.
    _EXCLUDED_NODES = {"transcript"}

    def query_wallet(person_name: str) -> str:
        """Look up every fact the wallet holds about ONE specific person, by name (e.g.
        "Colin" or "Colin Marsh") — across every source you're connected to and every
        attribute (role, organisation, topic, notes, etc.), not just identity fields.
        Call this once per person mentioned in the question, with just their name."""
        try:
            name_hint = person_name.strip().lower()
            if not name_hint:
                return "No person name given."

            # 1. find every visible person-node cell whose dereferenced value mentions
            #    this name — across ALL sources (not just descent's concept-routed subset,
            #    since whatsapp_calls/personal_notes classify their identifying field to
            #    `person` too but live under a separate, non-`person`-rooted concept).
            person_cells = [c for c in state.demo.store.all_cells() if c.type.ontology_node == "person"]
            projected = state.wproj.project(person_cells, cap, ctx)
            matched_keys: set[tuple[str, str]] = set()
            matched_refs_by_key: dict[tuple[str, str], object] = {}
            for c in person_cells:
                pcell = projected.cells.get(c.cell_id)
                if pcell is None:
                    continue
                val = resolve_value(state.demo.ladder, pcell, cap, ctx)
                if isinstance(val, (Refusal, Symbol)) or name_hint not in val.lower():
                    continue
                key = (source_of(c.ref.locator), row_key_of(c.ref.locator))
                matched_keys.add(key)
                matched_refs_by_key[key] = c.ref

            if not matched_keys:
                return f"No person matching {person_name!r} found in the wallet."

            # 2. resolve identity: durable grouping first; a fresh, per-call overlay lives
            #    the same rule (persists nothing) for any matched row not durably grouped.
            principal_of_key: dict[tuple[str, str], str] = {}
            for pid, cell in state.demo.store.all_with_grouping():
                key = (source_of(cell.ref.locator), row_key_of(cell.ref.locator))
                if key in matched_keys and pid is not None:
                    principal_of_key[key] = pid

            # resolve() only returns the component containing the FIRST ref passed in — it
            # does not merge everyone given to it. Loop so multiple distinct matched-but-
            # ungrouped people (e.g. an ambiguous name hint) each resolve to their own
            # principal, instead of every leftover ref being mislabelled with the first
            # person's id.
            overlay = GroupingOverlay()
            remaining = [ref for key, ref in matched_refs_by_key.items() if key not in principal_of_key]
            while remaining:
                rp = state.demo.resolve(remaining, cap, ctx, overlay)
                if isinstance(rp, Refusal):
                    break
                for ref in rp.member_refs:
                    key = (source_of(ref.locator), row_key_of(ref.locator))
                    principal_of_key[key] = rp.principal_id
                consumed = {(source_of(ref.locator), row_key_of(ref.locator)) for ref in rp.member_refs}
                remaining = [r for r in remaining
                            if (source_of(r.locator), row_key_of(r.locator)) not in consumed]

            principal_ids = set(principal_of_key.values())
            if not principal_ids:
                return f"Found {person_name!r}, but nothing about them is accessible to you."

            # 3. pull every accessible ontology-node value for each resolved principal
            #    through the SAME leak-safe join the deal-status card uses (existence-
            #    filtered before the dereference check, so an invisible cell is omitted,
            #    never a reason to refuse the whole answer) — kept PER PRINCIPAL. A name
            #    hint like "Colin" also substring-matches the seed's deliberate near-miss
            #    distractor "Colin Marsh-Jones" (a different person, different org, already
            #    durably grouped separately at onboarding — no live resolution even runs for
            #    either of them). Flattening both principals' facts into one list is exactly
            #    the bug that made the model describe them as one person with "aliases" —
            #    never merge across principal_ids, however many matched.
            per_principal: dict[str, list[str]] = {}
            per_principal_label: dict[str, str] = {}
            for pid in principal_ids:
                cells_for_pid = overlay.cells_for(state.demo.store, pid)
                nodes = sorted({c.type.ontology_node for c in cells_for_pid} - _EXCLUDED_NODES)
                facts: list[str] = []
                labels: list[str] = []
                for node in nodes:
                    cs = wallet_cross_source_query(state.demo.store, overlay, pid, node, cap, ctx,
                                                   state.demo.ladder, state.demo.registry,
                                                   state.demo.reader, state.wproj)
                    if isinstance(cs, Refusal) or not cs.values:
                        continue
                    for v in cs.values:
                        facts.append(f"{node} ({v.source}): {v.value}")
                        if node == "person":
                            labels.append(v.value)
                if facts:
                    per_principal[pid] = sorted(set(facts))
                    per_principal_label[pid] = labels[0] if labels else pid

            if not per_principal:
                return f"Found {person_name!r}, but nothing about them is accessible to you."

            if len(per_principal) == 1:
                return "\n".join(next(iter(per_principal.values())))

            blocks = [
                f"=== {per_principal_label[pid]} (a DIFFERENT person — do not merge with the others) ===\n"
                + "\n".join(per_principal[pid])
                for pid in per_principal
            ]
            return (
                f"{person_name!r} matches more than one DISTINCT person in the wallet. "
                "These are different people, not aliases of one person — keep their facts "
                "separate in your answer, and ask the user which one they mean if unclear:\n\n"
                + "\n\n".join(blocks)
            )
        except Exception as e:
            return f"Error executing query_wallet: {e}"

    # Convert history for Gemini
    contents = []
    for msg in history[:-1]:  # Exclude current message which is added at the end
        contents.append(types.Content(role=msg.role, parts=[types.Part.from_text(text=msg.content)]))

    sys_prompt = (
        "You are a helpful AI assistant with access to a governed digital twin wallet via "
        "the query_wallet tool. This wallet belongs to one person, Colin — if the user's "
        "question doesn't name anyone (e.g. \"what did the note say about the mortgage?\"), "
        "assume they mean Colin, or whoever the conversation was already about, rather than "
        "declining to look. Call query_wallet with just the person's name (e.g. \"Colin\"), "
        "not the whole question. Answer using only what the tool returns — if it says "
        "nothing is accessible, say so plainly rather than guessing. If the tool's result "
        "says a name matches more than one DISTINCT person, they are different people, NOT "
        "aliases of one person — never combine their facts into a single description (never "
        "say things like \"also known as\" across the '===' blocks). Tell the user there are "
        "multiple matches, summarise each separately, and ask which one they meant."
    )

    try:
        response = genai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents + [body.message],
            config=types.GenerateContentConfig(
                system_instruction=sys_prompt,
                tools=[query_wallet],
                temperature=0.2,
            )
        )
        reply = response.text
    except Exception as e:
        reply = f"Error calling LLM: {e}"
        
    # Save AI response
    save_message(db_path, session_id, "model", reply)
    return {"reply": reply}
