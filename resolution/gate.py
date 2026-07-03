"""Unit 3 — the single gate and the policy registry.

ONE gate interface, TWO physical call sites: adapter fetch (Unit 4) and projection (here).
This week it evaluates a plain callable predicate; the signature is kept identical so a
biscuit/authorisation engine drops in later without touching callers.

The registry owns the policy CALLABLES. Cells store only `policy_id` (Unit 1); the callables
never touch the db. A handful of demo policies are registered here.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from contract import Capability, CellPolicy, Context, Predicate
from locators import parse_locator

_log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PredicateGate:
    """Evaluates revocation, then capability expiry, then the predicate. Fail-closed on all three."""

    def __init__(self) -> None:
        self.revoked: set[str] = set()

    def revoke(self, cap_id: str) -> None:
        self.revoked.add(cap_id)

    def check(self, cap: Capability, predicate: Predicate, ctx: Context) -> bool:
        # getattr, not self.revoked: existing test subclasses (SpyGate in test_unit3.py /
        # test_e2e_moneyshot.py) override __init__ without calling super(), so the attribute
        # may not exist on them — treat that as "nothing revoked" rather than crashing.
        if cap.id() in getattr(self, "revoked", ()):
            return False
        expiry = cap.expiry
        if expiry.tzinfo is None:                 # tolerate naive expiries defensively
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < _now():
            return False
        return bool(predicate(cap, ctx))


# ---- demo predicates -------------------------------------------------------------
def allow(cap: Capability, ctx: Context) -> bool:
    return True


def deny(cap: Capability, ctx: Context) -> bool:
    return False


def hr_dereference(cap: Capability, ctx: Context) -> bool:
    """Dereference only for cross-source / DSAR purposes AND with HR clearance."""
    return cap.purpose in {"cross_source_query", "dsar_response"} and "clearance:hr" in cap.caveats


# ---- wallet predicates (journey demo) — ADDITIVE; existing predicates untouched ----
def is_owner(cap: Capability, ctx: Context) -> bool:
    return cap.holder == "colin"


def _src_caveat_ok(cap: Capability, source: str) -> bool:
    return f"src:{source}" in cap.caveats


def _row_share_ok(cap: Capability, source: str, row_key: str) -> bool:
    return f"share:{source}:{row_key}" in cap.caveats


def wallet_visible(cap: Capability, ctx: Context) -> bool:
    """Existence/type/state for wallet cells. ctx carries the cell's source/row_key
    (the wallet server injects them per-cell at check time — see WalletProjector)."""
    src, row = ctx.get("_cell_source", ""), ctx.get("_cell_row", "")
    return is_owner(cap, ctx) or _src_caveat_ok(cap, src) or _row_share_ok(cap, src, row)


def wallet_deref(cap: Capability, ctx: Context) -> bool:
    return wallet_visible(cap, ctx)


def owner_only_deref(cap: Capability, ctx: Context) -> bool:
    return is_owner(cap, ctx)


# resolution's identifying reads (name/email) are dereferences — gated by the same rule.
# Backwards-compatible OR: old analyst/hr caps still pass via hr_dereference; wallet caps
# (purpose "wallet_query") pass via the new clause. resolver.py itself is untouched.
def _resolver_deref(cap: Capability, ctx: Context) -> bool:
    return hr_dereference(cap, ctx) or cap.purpose == "wallet_query"


dereference_predicate: Predicate = _resolver_deref


# ---- demo policies (callables live here, never in the db) ------------------------
OPEN = CellPolicy(policy_id="open", see_existence=allow, see_type=allow,
                  see_state=allow, dereference=allow)

ROLE_GATED = CellPolicy(policy_id="role_gated", see_existence=allow, see_type=allow,
                        see_state=allow, dereference=hr_dereference)

# deny-by-dissimulation: existence itself is hidden, so the cell is ABSENT, not masked.
SECRET = CellPolicy(policy_id="secret", see_existence=deny, see_type=deny,
                    see_state=deny, dereference=deny)

# existence itself is capability-scoped: visible ONLY to an HR-cleared viewer. Two different
# capabilities therefore see two different MAPS (not just two different fetch outcomes).
HR_SCOPED = CellPolicy(policy_id="hr_scoped", see_existence=hr_dereference,
                       see_type=hr_dereference, see_state=allow, dereference=hr_dereference)

# The registered DEFAULT a cell points at when no explicit policy is chosen at mint time. The
# honest "you can see it exists, you cannot read it" posture: visible in the map (existence +
# type + state) but NOT dereferenceable without a specific grant. It is a REGISTERED policy so
# the common mint path is intentional, never a lookup for an id the registry doesn't know.
DEFAULT = CellPolicy(policy_id="default", see_existence=allow, see_type=allow,
                     see_state=allow, dereference=deny)

# The fail-closed fallback `get` returns for an UNKNOWN policy_id: every facet denies, so the
# cell is invisible to projection (Gate 1) and unreadable at fetch (Gate 2) — a governance gap
# fails closed (deny), never open, and never crashes a live query.
DENY_ALL = CellPolicy(policy_id="__deny_all__", see_existence=deny, see_type=deny,
                      see_state=deny, dereference=deny)

# ---- wallet policies (journey demo) — ADDITIVE ------------------------------------
# `owner_private` and `org_work` share the SAME predicate logic on purpose: the difference
# is entirely in which caveats each consumer's capability carries (notes are private because
# nobody but the owner is granted `src:personal_notes`, not because the policy names them).
OWNER_PRIVATE = CellPolicy(policy_id="owner_private", see_existence=wallet_visible,
                           see_type=wallet_visible, see_state=wallet_visible,
                           dereference=wallet_deref)
ORG_WORK = CellPolicy(policy_id="org_work", see_existence=wallet_visible,
                      see_type=wallet_visible, see_state=wallet_visible,
                      dereference=wallet_deref)
CALL_CONTENT = CellPolicy(policy_id="call_content", see_existence=wallet_visible,
                          see_type=wallet_visible, see_state=wallet_visible,
                          dereference=owner_only_deref)


class PolicyRegistry:
    """policy_id -> CellPolicy (with live callables). Unknown ids fail CLOSED (deny-all)."""

    def __init__(self, policies: list[CellPolicy] | None = None) -> None:
        self._by_id: dict[str, CellPolicy] = {}
        for p in (policies if policies is not None else
                 [OPEN, ROLE_GATED, SECRET, HR_SCOPED, DEFAULT,
                  OWNER_PRIVATE, ORG_WORK, CALL_CONTENT]):
            self._by_id[p.policy_id] = p
        self.unknown_lookups = 0   # observable counter: an unknown id is a gap you want to see

    def get(self, policy_id: str) -> CellPolicy:
        """Fail CLOSED. An unregistered policy_id is a governance gap (typo, unmigrated source,
        renamed policy): return a deny-all policy so the cell is invisible and unreadable, rather
        than raising `KeyError` and crashing a live query. The lookup is counted + logged so the
        gap is observable, not silent."""
        policy = self._by_id.get(policy_id)
        if policy is None:
            self.unknown_lookups += 1
            _log.warning("PolicyRegistry.get: unknown policy_id %r -> deny-all (fail-closed)", policy_id)
            return DENY_ALL
        return policy

    def register(self, policy: CellPolicy) -> None:
        self._by_id[policy.policy_id] = policy
