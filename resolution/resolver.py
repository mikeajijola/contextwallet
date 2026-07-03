"""Unit 3 — query-time resolution. Runs under the caller's capability. PERSISTS NOTHING.

The architect's settled ruling (do not revert): groupings resolve LIVE and persist nothing.
There is NO durable `set_grouping` write path anywhere in this unit. A resolution's grouping
lives ONLY in a query-scoped `GroupingOverlay`, discarded when the query ends, so a wrong
match is scoped to one query under one capability and cannot poison any other caller.

Contract note: the overlay is threaded through `resolve` as an EXPLICIT, MANDATORY parameter.
A named parameter you must pass is forgettable-proof — the call site visibly shows the overlay
is threaded through resolve -> join. There is deliberately NO ctx-keyed overlay side-channel:
an invisible channel silently defaults to "no overlay" when forgotten, which is the un-catchable
form of the bug the overlay design exists to prevent (Dana intermittently vanishing from the
join). `contract.py` was amended post-freeze so the frozen `Resolver` protocol now carries this
parameter — the contract matches reality, not routes around it.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Callable, Optional

from contract import (
    AuditEntry,
    AuditSink,
    Capability,
    Cell,
    Context,
    Reference,
    Refusal,
    ResolvedPrincipal,
    SourceReader,
)
from locators import make_locator, parse_locator, row_key_of, source_of
from twin_core.inversion import normalise_email
from embed import Embedder, cosine, default_embedder
from resolution.gate import PredicateGate, dereference_predicate

HIGH = 0.88   # name|org cosine auto-match (Dana sits at 0.809 -> middle band, by design)
LOW = 0.60    # below -> reject

# (text_a, text_b) -> {"same": bool, "reason": str}. Injectable; default is one Anthropic call.
SameAdjudicator = Callable[[str, str], dict]


def anthropic_same_adjudicator(text_a: str, text_b: str) -> dict:
    import os, json
    import anthropic
    model = os.environ.get("RESOLVER_MODEL", "claude-sonnet-4-6")
    prompt = (
        "Are these two records the same real person? Consider name variants and shared "
        "employer, but DIFFERENT organisations are strong evidence of different people.\n"
        f"A: {text_a}\nB: {text_b}\n\n"
        'Answer STRICT JSON only: {"same": <true|false>, "reason": <short string>}.'
    )
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(model=model, max_tokens=150,
                                 messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text
    text = text[text.find("{"): text.rfind("}") + 1]
    v = json.loads(text)
    return {"same": bool(v.get("same")), "reason": str(v.get("reason", ""))}


class GroupingOverlay:
    """In-memory, query-scoped grouping. Created once per query, passed through the whole
    resolve -> project -> fetch/join chain, discarded when the query ends. NEVER written to
    the db. NEVER read back across queries.

    This is the CONCRETE implementation of the `contract.GroupingOverlay` seam Protocol (Unit 3
    owns the impl; Unit 4 consumes it). Conformance is structural — the contract owns the type,
    this module owns the implementation, and the layering points DOWN (unit -> contract) only."""

    def __init__(self) -> None:
        self._of: dict[str, str] = {}   # row_key -> overlay principal_id
        self._n = 0

    def merge(self, row_keys: list[str]) -> str:
        existing = [self._of[rk] for rk in row_keys if rk in self._of]
        pid = existing[0] if existing else self._new_id()
        for rk in row_keys:
            self._of[rk] = pid
        # unify any rows that had a different prior id into the chosen pid
        if existing:
            stale = set(existing[1:])
            if stale:
                for rk, p in list(self._of.items()):
                    if p in stale:
                        self._of[rk] = pid
        return pid

    def principal_of(self, row_key: str) -> Optional[str]:
        return self._of.get(row_key)

    def _new_id(self) -> str:
        self._n += 1
        return f"ovl_{self._n}"

    def cells_for(self, store, principal_id: str, node: Optional[str] = None) -> list[Cell]:
        """Union the store's durable seed grouping with THIS overlay's live merges.

        This is what lets a live-resolved principal (e.g. Dana, absent from the store's
        durable grouping) be seen across both CRMs IN THE SAME QUERY. Any step that may be
        reading a live-resolved principal MUST call this, not a cold `store.cells_for`.
        """
        out: dict[str, Cell] = {}
        # durable path (durably-seeded principals like Colin)
        for c in store.cells_for(principal_id):
            out[c.cell_id] = c
        # overlay path (rows merged live this query)
        overlay_rows = {rk for rk, pid in self._of.items() if pid == principal_id}
        if overlay_rows:
            for c in store.all_cells():
                if row_key_of(c.ref.locator) in overlay_rows:
                    out[c.cell_id] = c
        cells = list(out.values())
        if node is not None:
            cells = [c for c in cells if c.type.ontology_node == node]
        return cells


def _org_tokens(org: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (org or "").lower()))


def _surname_initial(name: str) -> str:
    parts = (name or "").replace(",", " ").split()
    return parts[-1][0].lower() if parts else ""


class Resolver:
    """Banded, query-time, capability-scoped resolver. Persists nothing."""

    def __init__(self, gate: PredicateGate, reader: SourceReader, store,
                 embedder: Optional[Embedder] = None,
                 adjudicator: Optional[SameAdjudicator] = None,
                 audit: Optional[AuditSink] = None,
                 high: float = HIGH, low: float = LOW) -> None:
        self.gate = gate
        self.reader = reader
        self.store = store
        self.embedder = embedder or default_embedder()
        self.adjudicator = adjudicator or anthropic_same_adjudicator
        self.audit = audit
        self.high = high
        self.low = low
        self._memo: dict[tuple[str, str], dict] = {}  # IN-MEMORY only; never persisted

    def resolve(self, candidate_refs: list[Reference], caller: Capability,
                ctx: Context, overlay: GroupingOverlay):
        # overlay is MANDATORY and explicit — no ctx side-channel, no silent default. Passing
        # None is a loud error, never a quiet no-overlay run that would make Dana vanish.
        if overlay is None:
            raise ValueError("resolve() requires an explicit GroupingOverlay (no ctx side-channel)")

        # gate the identifying reads (they are dereferences). Deny -> flat Refusal.
        if not self.gate.check(caller, dereference_predicate, ctx):
            self._audit_resolve(caller, "deny")
            return Refusal()

        # build an identifying record per candidate (gated reads via SourceReader)
        records: dict[str, tuple[Reference, dict]] = {}
        for ref in candidate_refs:
            src, row_key, _ = parse_locator(ref.locator)
            records[row_key] = (ref, self._identify(src, row_key))

        keys = list(records)
        # union-find over matched pairs (blocked, banded)
        parent = {k: k for k in keys}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            parent[find(a)] = find(b)

        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                ra, rb = records[a][1], records[b][1]
                if not self._same_block(ra, rb):     # sub-quadratic: skip un-blocked pairs
                    continue
                same, _band, _reason = self._match(ra, rb)
                if same:
                    union(a, b)

        # the component containing the first candidate is the resolved principal
        root = find(keys[0])
        component = [k for k in keys if find(k) == root]
        pid = overlay.merge(component)
        member_refs = [records[k][0] for k in component]

        self._audit_resolve(caller, "allow")
        return ResolvedPrincipal(principal_id=pid, member_refs=member_refs)

    def prewarm(self, candidate_groups: list[list[Reference]], caller: Capability,
                ctx: Context) -> GroupingOverlay:
        """Populate a FRESH overlay at demo start so live resolution is instant. In-memory
        only; never persisted; never shared across callers."""
        overlay = GroupingOverlay()
        for refs in candidate_groups:
            self.resolve(refs, caller, ctx, overlay)
        return overlay

    # ---- internals ----
    def _identify(self, source: str, row_key: str) -> dict:
        rec = {"name": "", "email": "", "org": ""}
        for c in self.store.all_cells():
            if source_of(c.ref.locator) != source or row_key_of(c.ref.locator) != row_key:
                continue
            node = c.type.ontology_node
            if node not in ("person", "email", "organisation"):
                continue
            val = self.reader.read_value(c.ref)      # gated already (check ran up front)
            if node == "person":
                rec["name"] = val
            elif node == "email":
                rec["email"] = val
            elif node == "organisation":
                rec["org"] = val
        return rec

    def _same_block(self, a: dict, b: dict) -> bool:
        if _org_tokens(a["org"]) & _org_tokens(b["org"]):
            return True
        if a["name"] and b["name"] and _surname_initial(a["name"]) == _surname_initial(b["name"]):
            return True
        return False

    def _match(self, a: dict, b: dict) -> tuple[bool, str, str]:
        ea, eb = normalise_email(a["email"]), normalise_email(b["email"])
        if ea and eb and ea == eb:
            return True, "auto", "shared email"
        ta, tb = f'{a["name"]} | {a["org"]}', f'{b["name"]} | {b["org"]}'
        va, vb = self.embedder.embed([ta, tb])
        s = cosine(va, vb)
        if s >= self.high:
            return True, "auto", f"cosine {s:.3f}"
        if s < self.low:
            return False, "reject", f"cosine {s:.3f}"
        # middle band -> LLM (memoised IN MEMORY; errors are not cached)
        key = tuple(sorted([ta, tb]))
        if key in self._memo:
            v = self._memo[key]
        else:
            try:
                v = self.adjudicator(ta, tb)
                self._memo[key] = v
            except Exception:
                v = {"same": False, "reason": "adjudicator error"}
        return bool(v.get("same")), "flag", v.get("reason", "")

    def _audit_resolve(self, caller: Capability, decision: str) -> None:
        if self.audit is None:
            return
        self.audit.append(AuditEntry(
            event="resolve", ts=datetime.now(timezone.utc), principal=caller.holder,
            capability_id=caller.id(), cell_id=None, policy_version=0, decision=decision,
        ))
