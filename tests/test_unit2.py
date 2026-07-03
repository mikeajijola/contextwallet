"""Unit 2 acceptance tests — shortlist + judge classification, lazy onboarding, generality.

The embedder is real (shortlist recall). The judge is always a deterministic offline fake
that decides by READING SAMPLE VALUES — mirroring what the real LLM does — so tests are
offline and deterministic while exercising the real shortlist->judge path.
"""
from __future__ import annotations
import csv
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from contract import Capability, Reference, ControlPlaneRow
from db import get_conn
from locators import field_of
from twin_core.store import SqliteMasterTableStore
from onboarding.classify import OntologyClassifier, VerdictCache
from onboarding.control_plane import SqliteControlPlane
from onboarding.onboard import onboard_source, classify_deferred

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed"

_ROLE_WORDS = {"director", "lead", "manager", "engineer", "officer", "head", "vp", "cfo",
               "ceo", "cto", "president", "analyst", "architect", "executive", "designer",
               "scientist", "specialist", "consultant", "chief", "operations"}


def _looks_person(vals):
    return all(re.fullmatch(r"[A-Z][a-zA-Z.'-]*\.?\s+[A-Z][a-zA-Z.'-]+", v.strip()) for v in vals)


def _looks_org(vals):
    def titleish(v):
        return v[:1].isupper() and any(c.islower() for c in v) and len(v.split()) <= 4
    return all(titleish(v.strip()) for v in vals)


class ValueJudge:
    """Deterministic judge that decides over the SAMPLE VALUES (not the column name).
    This is what kills notes->phone and makes classification source-shape-agnostic."""

    def __init__(self):
        self.calls = 0

    def __call__(self, field_name, sample, candidates):
        self.calls += 1
        cand = {c[0] for c in candidates}
        vals = [v for v in sample if v and v.strip()]
        if not vals:
            return {"node": None, "confidence": 0.0, "reason": "no values"}
        if "email" in cand and any("@" in v for v in vals):
            return {"node": "email", "confidence": 0.96, "reason": "values are email addresses"}
        if "role" in cand and any(w in " ".join(vals).lower() for w in _ROLE_WORDS):
            return {"node": "role", "confidence": 0.9, "reason": "values are job titles"}
        if "person" in cand and _looks_person(vals):
            return {"node": "person", "confidence": 0.9, "reason": "values are human names"}
        if "organisation" in cand and _looks_org(vals):
            return {"node": "organisation", "confidence": 0.85, "reason": "values are company names"}
        return {"node": None, "confidence": 0.0, "reason": "no candidate fits the values"}


class FakeReader:
    def __init__(self, csv_path):
        self.rows = list(csv.DictReader(open(csv_path)))

    def list_fields(self, source):
        return list(self.rows[0].keys())

    def sample_field(self, source, field, n=3):
        return [r[field] for r in self.rows if r[field].strip()][:n]

    def read_value(self, ref):
        raise AssertionError("onboarding must not read_value")


class FakeGate:
    def check(self, cap, predicate, ctx):
        return predicate(cap, ctx)


class FakeAudit:
    def __init__(self):
        self.entries = []

    def append(self, e):
        self.entries.append(e)


def _cap():
    return Capability(holder="onboarder", purpose="onboarding",
                      expiry=datetime.now(timezone.utc) + timedelta(hours=1))


_CLASSIFIER = None
def classifier(conn=None):
    """Shared real-embedder classifier (model loads once) unless a cache conn is given."""
    global _CLASSIFIER
    if conn is None:
        if _CLASSIFIER is None:
            _CLASSIFIER = OntologyClassifier(judge=ValueJudge())
        return _CLASSIFIER
    return OntologyClassifier(judge=ValueJudge(), cache=VerdictCache(conn))


def _onboard(clf, csv_name="crm_b.csv", **kw):
    conn = get_conn(":memory:")
    store = SqliteMasterTableStore(conn)
    cp = SqliteControlPlane(conn, closest_nodes=clf.closest_nodes)
    reader = FakeReader(SEED / csv_name)
    report = onboard_source(csv_name.split(".")[0], reader, FakeGate(), _cap(), FakeAudit(),
                            store, cp, clf, rows=reader.rows,
                            principal_of=lambda r: r["contact_id"], key_field="contact_id", **kw)
    return report, store, cp, reader


# --------------------------------------------------------------------------- tests
def test_1_crm_b_eager_auto_and_region_deferred():
    report, store, cp, reader = _onboard(classifier(conn=get_conn(":memory:")))

    assert report.bands["name"] == "auto"
    assert report.bands["primary_email"] == "auto"
    assert report.bands["job_role"] == "auto"
    assert report.bands["org_name"] == "auto"           # embedding top-1 was 'email'; judge fixed it

    assert report.bands["region"] == "deferred"          # ambiguous long-tail
    assert "region" in report.deferred
    served = {field_of(c.ref.locator) for c in store.all_cells()}
    assert "region" not in served
    assert served == {"name", "primary_email", "job_role", "org_name"}

    assert report.bands["contact_id"] == "quarantine"    # structural, not judged as an attribute
    assert report.bands["last_touch"] == "quarantine"


def test_2_deferred_region_resolves_at_point_of_use():
    clf = classifier(conn=get_conn(":memory:"))
    report, store, cp, reader = _onboard(clf)
    assert "region" in report.deferred

    proposal = classify_deferred("region", ["EMEA", "NA", "APAC"], "crm_b", clf, store, cp,
                                 rows=reader.rows, principal_of=lambda r: r["contact_id"],
                                 key_field="contact_id")
    assert proposal.band == "propose_new"                # the ontology gap, surfaced lazily
    assert proposal.proposed_node is None
    assert "region" not in {field_of(c.ref.locator) for c in store.all_cells()}


def test_3_obscure_field_needs_sample_values():
    clf = classifier()
    role_sample = ["Director, Platform", "Operations Lead", "Staff Software Engineer"]
    assert clf.classify("rm", role_sample).proposed_node == "role"   # judge reads values -> role
    assert clf.classify("rm", []).proposed_node != "role"            # no values -> judge can't


def test_4_sample_and_drop_no_values_in_cells():
    clf = classifier(conn=get_conn(":memory:"))
    _, store, _, reader = _onboard(clf)
    sampled = set()
    for fld in reader.list_fields("crm_b"):
        if fld == "contact_id":
            continue
        sampled.update(v for v in reader.sample_field("crm_b", fld, 3) if v.strip())
    dump = "\n".join(str(tuple(r)) for r in store.conn.execute("SELECT * FROM cells"))
    for v in sampled:
        assert v not in dump, f"sampled value leaked into cells: {v!r}"


def test_5_control_plane_propose_approve_sprawl_guard():
    conn = get_conn(":memory:")
    clf = classifier()
    cp = SqliteControlPlane(conn, closest_nodes=clf.closest_nodes)
    row_id = cp.propose(ControlPlaneRow(id="node:region", kind="ontology_node",
                                        payload={"name": "region", "description": "a sales region"}))
    assert cp.status_of(row_id) == "proposed" and cp.current_version() == 0
    assert cp.approve(row_id, "mike") == 1 and cp.current_version() == 1
    closest = cp.closest_nodes("region", k=3)
    assert len(closest) == 3
    assert all(n in {"person", "email", "role", "organisation", "phone"} for n, _ in closest)


def test_6_determinism_cached_judge_zero_repeat_calls():
    conn = get_conn(":memory:")
    judge = ValueJudge()
    clf = OntologyClassifier(judge=judge, cache=VerdictCache(conn))

    def run():
        c = get_conn(":memory:")
        store = SqliteMasterTableStore(c)
        cp = SqliteControlPlane(c, closest_nodes=clf.closest_nodes)
        reader = FakeReader(SEED / "crm_b.csv")
        return onboard_source("crm_b", reader, FakeGate(), _cap(), FakeAudit(), store, cp, clf,
                              rows=reader.rows, principal_of=lambda r: r["contact_id"],
                              key_field="contact_id").bands

    first = run()
    calls = judge.calls                                  # one per eager non-structural field
    second = run()
    assert first == second
    assert judge.calls == calls                          # re-run: cache hit, ZERO new judge calls


def test_7_rejected_top_candidate_is_surfaced_not_discarded():
    # `notes` embeds near phone (the old top-1 crisis). The judge reads prose values and
    # refuses to auto-mint it. GAP-CHECK: it must be SURFACED for human review, never
    # silently dropped (a dropped real attribute is fail-OPEN wearing fail-closed's clothes).
    clf = classifier()
    notes = ["called the customer back", "left a voicemail", "no answer"]
    p = clf.classify("notes", notes)
    assert p.band != "auto"
    assert p.proposed_node != "phone"
    assert p.band == "propose_new"          # a reviewable band, not a silent discard

    # ...and it actually lands as a human-visible control-plane proposal (fail-closed).
    conn = get_conn(":memory:")
    store = SqliteMasterTableStore(conn)
    cp = SqliteControlPlane(conn, closest_nodes=clf.closest_nodes)
    classify_deferred("notes", notes, "src", clf, store, cp,
                      rows=[{"k": "r1", "notes": "x"}], principal_of=lambda r: r["k"], key_field="k")
    assert cp.status_of("src:notes") == "proposed"   # surfaced somewhere a human sees it
    assert store.all_cells() == []                    # and NOT minted as a live cell


def test_10_deep_shortlist_node_still_judged():
    # GAP-CHECK: the judge can only recover a node that MADE the shortlist. A field whose
    # correct node sits DEEP (rank 3, not rank 2) must still reach the judge and classify.
    # 'inbox_phone_line' over human names: email/phone out-rank person; person is rank 3.
    clf = classifier()
    field, sample, correct = "inbox_phone_line", ["Alex Johnson", "Priya Rao", "M. Chen"], "person"

    ranked = clf._node_scores(field, sample)
    rank = [n for n, _ in ranked].index(correct) + 1
    assert rank >= 3, f"expected the correct node buried at rank>=3, got {rank}"   # genuinely deep
    assert clf.classify(field, sample).proposed_node == correct                    # judge recovers it

    # width matters: even a deliberately NARROW top-k keeps the rank-3 node via the margin
    # floor, so recall does not collapse on mismatched vocabulary.
    narrow = OntologyClassifier(judge=ValueJudge(), k=2)
    assert correct in [n for n, _ in narrow.shortlist(field, sample)]
    assert narrow.classify(field, sample).proposed_node == correct


def test_8_generality_invented_source_no_hardcoding():
    # a source we never tuned against: invented column names, classified purely by VALUES.
    clf = classifier()
    assert clf.classify("moniker", ["Alex Johnson", "Priya Rao", "M. Chen"]).proposed_node == "person"
    assert clf.classify("electronic_mail", ["a@x.com", "b@y.org"]).proposed_node == "email"
    assert clf.classify("gizmo_kind", ["Head of Marketing", "Software Engineer"]).proposed_node == "role"

    # an ambiguous invented field goes shortlist -> judge (judge invoked), not a hardcoded map
    judge = ValueJudge()
    clf2 = OntologyClassifier(judge=judge)
    before = judge.calls
    amb = clf2.classify("doohickey", ["blorp", "fizzbuzz", "widget"])
    assert judge.calls == before + 1                     # the judge decided (not a lookup table)
    assert amb.band in ("propose_new", "quarantine")     # fail-closed on the unknown

    # structural guarantee: NO seed column name appears as a literal in the classification path
    src = (ROOT / "onboarding" / "classify.py").read_text() + (ROOT / "onboarding" / "onboard.py").read_text()
    for col in ["full_name", "primary_email", "job_role", "org_name", "contact_id",
                "last_touch", "updated_at"]:
        assert col not in src, f"seed column name {col!r} hardcoded in the classification path"


def test_9_bulk_autonomous_path_judge_decides_alone():
    # nobody is watching: the judge decides over the shortlist; confident fields still mint.
    clf = classifier(conn=get_conn(":memory:"))
    report, store, _, _ = _onboard(clf, autonomous=True, lazy=False)
    assert report.bands["job_role"] == "auto"            # judge decided without a human
    assert report.minted_cells > 0
