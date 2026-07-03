"""The two deterministic offline fakes the e2e test uses, copied here so server code never
imports from `tests/` — the critical demo path must stay deterministic and offline, with no
live LLM calls (build brief non-negotiable #7).

`ValueJudge` mirrors `tests.test_unit2.ValueJudge`; `CountingSame` mirrors
`tests.test_unit3.CountingSame`. With the wallet's seed (§2.1), no middle-band pair
involving Colin exists, so `CountingSame(same=True)` cannot over-merge — Dana remains the
only middle-band pair, exactly as in the engine's own e2e test.
"""
from __future__ import annotations

_ROLE_WORDS = {"director", "lead", "manager", "engineer", "officer", "head", "vp", "cfo",
              "ceo", "cto", "president", "analyst", "architect", "executive", "designer",
              "scientist", "specialist", "consultant", "chief", "operations"}


def _looks_person(vals):
    import re
    return all(re.fullmatch(r"[A-Z][a-zA-Z.'-]*\.?\s+[A-Z][a-zA-Z.'-]+", v.strip()) for v in vals)


def _looks_org(vals):
    def titleish(v):
        return v[:1].isupper() and any(c.islower() for c in v) and len(v.split()) <= 4
    return all(titleish(v.strip()) for v in vals)


class ValueJudge:
    """Deterministic judge that decides over the SAMPLE VALUES (not the column name)."""

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


class CountingSame:
    def __init__(self, same=True):
        self.calls = 0
        self.same = same

    def __call__(self, a, b):
        self.calls += 1
        return {"same": self.same, "reason": "fake"}
