"""locators.py — the ONE place a cell locator is minted or parsed.
Shared foundation. FROZEN behaviour: never hand-format, f-string, or split a locator
anywhere else. Always go through make_locator / parse_locator.
A locator is opaque and identifies WHERE a value lives, never WHO it belongs to."""
from urllib.parse import quote, unquote

_SEP = ":"

def make_locator(source: str, row_key: str, field: str) -> str:
    """Mint an opaque, round-trippable locator: source:row_key:field.
    row_key identifies the row within the source (e.g. the CSV row id / natural key),
    NOT the resolved principal. Each component is percent-encoded so a ':' inside a
    component cannot be mistaken for the separator."""
    for label, part in (("source", source), ("row_key", row_key), ("field", field)):
        if not part:
            raise ValueError(f"locator component {label!r} must be non-empty")
    return _SEP.join(quote(p, safe="") for p in (source, row_key, field))

def parse_locator(locator: str) -> tuple[str, str, str]:
    """Inverse of make_locator -> (source, row_key, field). Raises on malformed input."""
    parts = locator.split(_SEP)
    if len(parts) != 3:
        raise ValueError(f"malformed locator (expected 3 components): {locator!r}")
    source, row_key, field = (unquote(p) for p in parts)
    if not (source and row_key and field):
        raise ValueError(f"locator has empty component: {locator!r}")
    return source, row_key, field

def source_of(locator: str) -> str:  return parse_locator(locator)[0]
def row_key_of(locator: str) -> str: return parse_locator(locator)[1]
def field_of(locator: str) -> str:   return parse_locator(locator)[2]
