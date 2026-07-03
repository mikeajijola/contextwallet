"""Unit 3 — Resolution + Gate + Projection (wired together, one unit)."""
from resolution.gate import (
    PredicateGate, PolicyRegistry, dereference_predicate, hr_dereference,
    allow, deny, OPEN, ROLE_GATED, SECRET, HR_SCOPED, DEFAULT, DENY_ALL,
)
from resolution.projection import Projector
from resolution.resolver import Resolver, GroupingOverlay
from resolution.descent import SemanticDescent, DescentResult
from resolution.capability import mint, demo_analyst_cap, demo_restricted_cap
from onboarding.ontology_router import ConceptRouter

__all__ = [
    "PredicateGate", "PolicyRegistry", "dereference_predicate", "hr_dereference",
    "allow", "deny", "OPEN", "ROLE_GATED", "SECRET", "HR_SCOPED", "DEFAULT", "DENY_ALL",
    "Projector", "Resolver", "GroupingOverlay",
    "SemanticDescent", "DescentResult", "ConceptRouter",
    "mint", "demo_analyst_cap", "demo_restricted_cap",
]
