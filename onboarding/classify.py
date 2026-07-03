"""Unit 2 — field-to-node classification by SHORTLIST + judge (source-shape-agnostic).

The mechanism changed from "pick the node" to "nominate a shortlist, then judge over the
sample VALUES". This is the generality mechanism, not a tweak:

  1. structural pre-filter — keys (`id`, `*_id`) and timestamp columns are quarantined
     BEFORE embedding. They are not attributes, and their NAMES otherwise embed spuriously
     near real concepts (an id column can out-score a real attribute). Load-bearing: it is
     the only thing that stops those from ever reaching the judge as attributes.
  2. shortlist (embedding, RECALL not precision) — the field embeds against every node's
     exemplars (max-pool cosine); we keep the top-k plus anything within a margin. The bar
     is only "get the right node INTO the shortlist", which bge-small clears easily, so the
     cosine is a loose recall floor, NOT a tuned decision boundary. The old top-1 threshold
     agony (0.66 vs 0.70) is retired: spurious matches like a free-text `notes` column that
     out-cosines `phone` are a non-problem here, because...
  3. judge (LLM, PRECISION) — an LLM decides among the shortlist WITH the sample values in
     front of it. It reads prose and sees `notes` is not a phone number; the embedding never
     could, the judge reading real values always can. This is what absorbs vocabulary we did
     not anticipate, so a source we never tuned against classifies by the same path.

Fail-closed (hole 2): only a CONFIDENT judged node mints a live cell (`auto`). A judged-but-
unconfident node is `flag`; a judged "none" is `propose_new`/`quarantine`. Everything not
`auto` is a proposed control-plane row — never a silent live cell.

Lazy classification (see onboard.py): retrieval-seed fields (person/email — the fields a
query finds people BY) are classified eagerly at ingest; the ambiguous long-tail is deferred
to point-of-use under this same shortlist+judge path. The embedding cosine is used as a
deferral hint (eager vs defer) — but that hint is NOT load-bearing for correctness: if it
defers something classifiable, the judge still classifies it correctly later; it only
affects WHEN, not WHETHER.

v2 framing (not code): human confirmations are labelled data, so the judge improves per
deployment from its own review history — "gets cheaper with use", not "gets automated".
There is no universal classifier to converge to; calibration is per-deployment.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml

from contract import AuditEntry, ClassificationProposal
from embed import Embedder, cosine, default_embedder

# shortlist (recall): keep the top-k candidates plus anything within MARGIN of the top.
SHORTLIST_K = 5
SHORTLIST_MARGIN = 0.15
# judge: a node judged with confidence >= JUDGE_CONF mints a live cell (auto); else flag.
JUDGE_CONF = 0.70
# deferral hint (NOT a decision boundary): a non-seed field whose best cosine is below
# EAGER_FLOOR is treated as ambiguous long-tail and deferred to point-of-use.
EAGER_FLOOR = 0.66
# fields a query finds people BY must be eager even if ambiguous.
RETRIEVAL_SEED_NODES = frozenset({"person", "email"})

_ONTOLOGY_PATH = Path(__file__).resolve().parent.parent / "ontology.yaml"
_DATE_RE = re.compile(r"^\d{4}[-/]\d{2}[-/]\d{2}")


def _looks_key(field_name: str) -> bool:
    f = field_name.strip().lower()
    return f == "id" or f.endswith("_id")


def _looks_date(sample: list[str]) -> bool:
    return bool(sample) and all(_DATE_RE.match(v.strip()) for v in sample)


# ---- the judge -------------------------------------------------------------------
# (field_name, sample, candidates) -> {"node": str|None, "confidence": float, "reason": str}
# candidates is the shortlist: list of (node_name, description). Injectable so tests are offline.
Judge = Callable[[str, list[str], list[tuple[str, str]]], dict]
Adjudicator = Judge  # backwards-compatible alias


def anthropic_judge(field_name: str, sample: list[str],
                    candidates: list[tuple[str, str]]) -> dict:
    """Default judge: one strict-JSON Anthropic call that decides over the shortlist by
    reading the SAMPLE VALUES (not just the column name)."""
    import os
    import anthropic

    model = os.environ.get("RESOLVER_MODEL", "claude-sonnet-4-6")
    cand_lines = "\n".join(f"- {n}: {d}" for n, d in candidates)
    prompt = (
        "Decide which ontology node a data-source field maps to, judging by the SAMPLE "
        "VALUES more than the column name.\n"
        f"Field name: {field_name}\n"
        f"Sample values: {sample}\n"
        f"Candidate nodes (shortlist):\n{cand_lines}\n\n"
        'Answer STRICT JSON only: {"node": <one candidate name or null>, '
        '"confidence": <0..1>, "reason": <short string>}. '
        'Use null if the values fit none of the candidates.'
    )
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(model=model, max_tokens=200,
                                 messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text
    text = text[text.find("{"): text.rfind("}") + 1]
    v = json.loads(text)
    return {"node": v.get("node"), "confidence": float(v.get("confidence", 0.0)),
            "reason": str(v.get("reason", ""))}


class VerdictCache:
    """SQLite memo of judge verdicts so re-runs make ZERO new LLM calls (determinism)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.execute("CREATE TABLE IF NOT EXISTS classify_cache "
                     "(key TEXT PRIMARY KEY, verdict_json TEXT)")
        conn.commit()

    @staticmethod
    def key(field_name: str, sample: list[str]) -> str:
        raw = field_name + "::" + "|".join(sample)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(self, key: str) -> Optional[dict]:
        row = self.conn.execute("SELECT verdict_json FROM classify_cache WHERE key = ?",
                                (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, verdict: dict) -> None:
        self.conn.execute("INSERT OR REPLACE INTO classify_cache (key, verdict_json) VALUES (?, ?)",
                          (key, json.dumps(verdict, sort_keys=True)))
        self.conn.commit()


def load_ontology(path: Path = _ONTOLOGY_PATH) -> list[dict]:
    """Nodes as raw dicts: {name, description, exemplars}. `region` is intentionally absent."""
    data = yaml.safe_load(path.read_text())
    return [{"name": n["name"], "description": n["description"],
             "exemplars": list(n.get("exemplars", []))} for n in data["nodes"]]


class OntologyClassifier:
    """Embeds node exemplars once; nominates a shortlist and lets the judge decide."""

    def __init__(self, embedder: Optional[Embedder] = None,
                 judge: Optional[Judge] = None,
                 adjudicator: Optional[Judge] = None,   # back-compat alias
                 cache: Optional[VerdictCache] = None,
                 audit=None,
                 ontology_path: Path = _ONTOLOGY_PATH,
                 k: int = SHORTLIST_K, margin: float = SHORTLIST_MARGIN,
                 judge_conf: float = JUDGE_CONF, eager_floor: float = EAGER_FLOOR) -> None:
        self.embedder = embedder or default_embedder()
        self.judge = judge or adjudicator or anthropic_judge
        self.cache = cache
        self.audit = audit
        self.k = k
        self.margin = margin
        self.judge_conf = judge_conf
        self.eager_floor = eager_floor
        self.nodes = load_ontology(ontology_path)
        self._desc = {n["name"]: n["description"] for n in self.nodes}

        self._flat: list[str] = []
        self._owner: list[str] = []
        for node in self.nodes:
            for text in [node["description"], *node["exemplars"]]:
                self._flat.append(text)
                self._owner.append(node["name"])
        self._ex_vecs = self.embedder.embed(self._flat)

    # ---- embedding (recall) ----
    def _node_scores(self, field_name: str, sample: list[str]) -> list[tuple[str, float]]:
        text = field_name + " | " + " | ".join(sample)
        fv = self.embedder.embed([text])[0]
        best: dict[str, float] = {n["name"]: -1.0 for n in self.nodes}
        for i, _ in enumerate(self._flat):
            s = cosine(fv, self._ex_vecs[i])
            if s > best[self._owner[i]]:
                best[self._owner[i]] = s
        return sorted(best.items(), key=lambda kv: -kv[1])

    def shortlist(self, field_name: str, sample: list[str]) -> list[tuple[str, float]]:
        """Recall, not decision: top-k candidates plus anything within `margin` of the top."""
        ranked = self._node_scores(field_name, sample)
        top = ranked[0][1]
        picked = list(ranked[: self.k])
        for node, score in ranked[self.k:]:
            if top - score <= self.margin:
                picked.append((node, score))
        return picked

    def closest_nodes(self, concept: str, k: int = 3) -> list[tuple[str, float]]:
        """Sprawl guard: the k existing nodes most similar to a proposed concept."""
        return self._node_scores(concept, [])[:k]

    # ---- deferral hint (NOT load-bearing for correctness) ----
    def is_structural(self, field_name: str, sample: list[str]) -> bool:
        return _looks_key(field_name) or _looks_date(sample)

    def should_defer(self, field_name: str, sample: list[str],
                     eager_nodes: frozenset = RETRIEVAL_SEED_NODES) -> bool:
        """Eager if a retrieval-seed candidate or an embedding-confident match; else defer.
        Wrong guesses only change WHEN a field is classified, never the eventual result."""
        if self.is_structural(field_name, sample):
            return False   # structural is handled eagerly (quarantine, no judge cost)
        sl = self.shortlist(field_name, sample)
        top_node, top_score = sl[0]
        if top_node in eager_nodes:
            return False
        return top_score < self.eager_floor

    # ---- classification (shortlist -> judge) ----
    def classify(self, field_name: str, sample: list[str], source: str = "",
                 autonomous: bool = False) -> ClassificationProposal:
        proposal = self._classify(field_name, sample, source, autonomous)
        if self.audit is not None:
            self.audit.append(AuditEntry(
                event="classify", ts=datetime.now(timezone.utc),
                principal=source or "onboarding", capability_id="onboarding",
                cell_id=None, policy_version=0, decision="allow"))
        return proposal

    def _classify(self, field_name, sample, source, autonomous) -> ClassificationProposal:
        # 1. structural pre-filter — never reaches the judge as an attribute
        if _looks_key(field_name):
            return self._quarantine(source, field_name,
                                    f"'{field_name}' is an identifier/key column, not an attribute")
        if _looks_date(sample):
            return self._quarantine(source, field_name,
                                    f"'{field_name}' holds timestamps {sample}, not an attribute")

        # 2. shortlist (recall) + 3. judge over the sample VALUES (precision)
        shortlist = self.shortlist(field_name, sample)
        verdict = self._judge(field_name, sample, shortlist)
        node = verdict.get("node")
        conf = float(verdict.get("confidence", 0.0))
        reason = verdict.get("reason", "")
        cands = ", ".join(f"{n}:{s:.2f}" for n, s in shortlist)

        if node and conf >= self.judge_conf:
            return ClassificationProposal(
                source=source, field_name=field_name, proposed_node=node,
                confidence=round(conf, 4), band="auto",
                evidence=f"shortlist[{cands}] -> judge '{node}' conf {conf:.2f}: {reason}")
        if node:
            return ClassificationProposal(
                source=source, field_name=field_name, proposed_node=node,
                confidence=round(conf, 4), band="flag",
                evidence=f"shortlist[{cands}] -> judge '{node}' conf {conf:.2f} (< {self.judge_conf}): {reason}")
        # judge says none of the shortlist fits
        return self._propose_or_quarantine(source, field_name, sample, cands, reason)

    def _judge(self, field_name: str, sample: list[str],
               shortlist: list[tuple[str, float]]) -> dict:
        candidates = [(n, self._desc.get(n, "")) for n, _ in shortlist]
        if self.cache is None:
            return self.judge(field_name, sample, candidates)
        key = VerdictCache.key(field_name, sample)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        verdict = self.judge(field_name, sample, candidates)
        self.cache.put(key, verdict)
        return verdict

    def _propose_or_quarantine(self, source, field_name, sample, cands, reason) -> ClassificationProposal:
        meaningful = bool(sample) and any(c.isalpha() for c in field_name)
        if meaningful:
            return ClassificationProposal(
                source=source, field_name=field_name, proposed_node=None,
                confidence=0.0, band="propose_new",
                evidence=f"'{field_name}' {sample}: judge found no fitting node in [{cands}] ({reason})")
        return self._quarantine(source, field_name,
                                f"no coherent concept for '{field_name}' in [{cands}] ({reason})")

    def _quarantine(self, source, field_name, evidence) -> ClassificationProposal:
        return ClassificationProposal(source=source, field_name=field_name, proposed_node=None,
                                      confidence=0.0, band="quarantine", evidence=evidence)
