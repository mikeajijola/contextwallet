"""Acceptance tests for the shared locator seam.

A locator is opaque (source:row_key:field) and must round-trip losslessly, including
components that themselves contain ':'. It carries NO identity by construction.
"""
import inspect

import pytest

import locators
from locators import make_locator, parse_locator, row_key_of, source_of, field_of


def test_1_roundtrip_basic():
    assert parse_locator(make_locator("crm_b", "dana_osei", "job_role")) == (
        "crm_b",
        "dana_osei",
        "job_role",
    )


def test_2_colon_safety():
    # the exact bug this seam prevents: a ':' inside a component must not be read as the separator
    loc = make_locator("crm_b", "acct:42", "field:x")
    assert parse_locator(loc) == ("crm_b", "acct:42", "field:x")
    assert row_key_of(loc) == "acct:42"      # NOT "42" or a truncated value
    assert source_of(loc) == "crm_b"
    assert field_of(loc) == "field:x"
    # unicode / percent round-trip too
    loc2 = make_locator("src", "Zoë%X", "naïve:role")
    assert parse_locator(loc2) == ("src", "Zoë%X", "naïve:role")


def test_3_no_identity_derivable_from_locator():
    # deriving identity from a locator is impossible by construction — there is no such helper
    assert not hasattr(locators, "principal_id_of")
    # and no public function in the module returns/derives a principal
    names = [n for n, _ in inspect.getmembers(locators, inspect.isfunction)]
    assert not any("principal" in n for n in names)


def test_4_malformed_raises():
    with pytest.raises(ValueError):
        parse_locator("a:b")          # two components
    with pytest.raises(ValueError):
        parse_locator("a:b:c:d")      # four components
    with pytest.raises(ValueError):
        parse_locator("::a")          # empty components
    with pytest.raises(ValueError):
        make_locator("", "row", "f")  # empty component at mint time
    with pytest.raises(ValueError):
        make_locator("src", "", "f")


def test_5_deterministic():
    assert make_locator("crm_a", "a1", "title") == make_locator("crm_a", "a1", "title")
