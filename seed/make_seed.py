"""Generate the seed data the whole demo lives on.

Fully deterministic: rows are hand-authored literals (no RNG), so the demo never
varies between runs. Writes `seed/crm_a.csv` and `seed/crm_b.csv` (the frozen engine
suite's format), PLUS `seed/crm_a.db` / `seed/crm_b.db` (the wallet runtime's format,
written from the SAME literal row lists so the two formats cannot drift), plus the two
wallet-only sqlite sources (`whatsapp_calls.db`, `personal_notes.db`) and the transcript
file the `transcript_ref` pointer resolves to.

Planted structure (exercised by the acceptance tests across all five units):

  * Colin Marsh  — shared email across A/B  -> AUTO-band resolution.
                   title ("VP Engineering") vs job_role ("Director, Platform"),
                   both dated -> conflict_ordered.
  * Dana Osei    — NO shared email (B email blank) -> middle-band (LLM) resolution
                   on name + org. One title undated (B last_touch blank)
                   -> conflict_unordered.
  * "Colin Marsh-Jones" — near-miss distractor at a DIFFERENT org; must NOT resolve
                   to Colin Marsh.
  * `region` populated only in crm_b -> no ontology node -> quarantine + propose (Unit 2).

Field-to-node truth (divergent names, same meaning):
  full_name/name -> person, email/primary_email -> email,
  title/job_role -> role, company/org_name -> organisation.
  updated_at/last_touch order conflicts (not served as attributes).

DUAL-FORMAT RULE: never delete the CSVs, never edit rows in only one format. The engine's
frozen test suite reads the CSVs via `CsvSourceReader`; the wallet runtime reads the .db
files via `SqliteSourceReader`. Both are written from ONE literal list here.
"""
from __future__ import annotations
import csv
import sqlite3
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent
TRANSCRIPT_DIR = SEED_DIR / "transcripts"

# crm_a.csv: id, full_name, email, title, company, updated_at
CRM_A_HEADER = ["id", "full_name", "email", "title", "company", "updated_at"]
CRM_A_ROWS = [
    # --- planted identities ---
    ["a1", "Colin Marsh", "colin.marsh@stripe.com", "VP Engineering", "Stripe", "2025-11-02"],
    ["a2", "Dana Osei", "dana.osei@acme.io", "Head of Ops", "Acme", "2025-10-01"],
    # --- distractors ---
    ["a3", "Priya Nair", "priya.nair@stripe.com", "Staff Engineer", "Stripe", "2025-08-15"],
    ["a4", "Marcus Webb", "marcus.webb@globex.com", "CFO", "Globex", "2025-07-22"],
    ["a5", "Elena Rossi", "elena.rossi@acme.io", "Account Executive", "Acme", "2025-09-03"],
    ["a6", "Tomas Vega", "tomas.vega@initech.com", "Product Manager", "Initech", "2025-06-30"],
    ["a7", "Sarah Chen", "sarah.chen@umbrella.co", "Head of Design", "Umbrella", "2025-10-18"],
    ["a8", "Colin Marsh-Jones", "colin.mjones@hooli.com", "VP Sales", "Hooli", "2025-05-11"],
    ["a9", "Ravi Patel", "ravi.patel@globex.com", "Data Scientist", "Globex", "2025-09-27"],
]

# crm_b.csv: contact_id, name, primary_email, job_role, org_name, last_touch, region
CRM_B_HEADER = ["contact_id", "name", "primary_email", "job_role", "org_name", "last_touch", "region"]
CRM_B_ROWS = [
    # --- planted identities ---
    ["b1", "C. Marsh", "colin.marsh@stripe.com", "Director, Platform", "Stripe", "2025-09-10", "EMEA"],
    ["b2", "D. Osei", "", "Operations Lead", "Acme", "", "NA"],
    # --- distractors ---
    ["b3", "Priya Nair", "priya.nair@stripe.com", "Staff Software Engineer", "Stripe", "2025-08-20", "APAC"],
    ["b4", "M. Webb", "marcus.webb@globex.com", "Chief Financial Officer", "Globex", "2025-07-25", "NA"],
    ["b5", "Elena Rossi", "elena.r@acme.io", "Senior AE", "Acme", "2025-09-05", "EMEA"],
    ["b6", "Nina Kowalski", "nina.kowalski@initech.com", "Engineering Manager", "Initech", "2025-08-01", "EMEA"],
    ["b7", "Sarah Chen", "sarah.chen@umbrella.co", "Design Director", "Umbrella", "2025-10-20", "NA"],
    ["b8", "Colin Marsh-Jones", "colin.mjones@hooli.com", "VP of Sales", "Hooli", "2025-05-14", "NA"],
    ["b9", "James O'Brien", "james.obrien@initech.com", "Solutions Architect", "Initech", "2025-09-30", "EMEA"],
]

# seed/whatsapp_calls.db: table `calls`, key `call_id`.
# TRAP #1 (pre-solved): a single `participant` (the resolvable identity) + display-only
# `counterpart`, plus an `org` column so blocking passes on the org token and the match
# lands in the AUTO band (cosine of "Colin Marsh | Stripe" vs itself ~= 1.0 >= HIGH).
CALLS_HEADER = ["call_id", "participant", "counterpart", "org", "channel", "topic", "transcript_ref"]
CALLS_ROWS = [
    ["c1", "Colin Marsh", "Femi Adeyemi", "Stripe", "whatsapp",
     "Stripe Q3 pricing — needs finance sign-off", "wa_store://call_c1"],
]

# seed/personal_notes.db: table `notes`, key `note_id`.
# n2 is the clearly-private note that makes org-invisibility mean something.
NOTES_HEADER = ["note_id", "person", "org", "topic", "body"]
NOTES_ROWS = [
    ["n1", "Colin Marsh", "Stripe", "Stripe Q3 pricing",
     "Colin hinted they'd sign at 12% if we move before quarter end"],
    ["n2", "Colin Marsh", "", "mortgage renewal",
     "Remortgage meeting Tuesday 4pm — bring payslips"],
]

TRANSCRIPT_C1 = """\
[whatsapp call — Colin Marsh & Femi Adeyemi — Stripe Q3 pricing]
Femi: Thanks for jumping on, Colin. Wanted to close out Q3 pricing before the quarter ends.
Colin: Sure — where are we landing on the renewal number?
Femi: We can do 12% if you can get finance sign-off this side of quarter end.
Colin: That works for us. I'll get finance to sign off this week.
Femi: Great, I'll send the updated order form today.
Colin: Perfect, talk soon.
Femi: Speak soon, thanks Colin.
"""


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _write_db(path: Path, table: str, key_col: str, header: list[str], rows: list[list[str]]) -> None:
    """Write one literal row list into a fresh sqlite table (same literals as the CSV twin)."""
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        cols_sql = ", ".join(f'"{c}" TEXT' for c in header)
        conn.execute(f'CREATE TABLE {table} ({cols_sql}, PRIMARY KEY ("{key_col}"))')
        placeholders = ", ".join("?" for _ in header)
        conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    # the two CRM CSVs — UNCHANGED, read by the frozen engine test suite.
    _write_csv(SEED_DIR / "crm_a.csv", CRM_A_HEADER, CRM_A_ROWS)
    _write_csv(SEED_DIR / "crm_b.csv", CRM_B_HEADER, CRM_B_ROWS)
    print(f"wrote {SEED_DIR / 'crm_a.csv'} ({len(CRM_A_ROWS)} rows)")
    print(f"wrote {SEED_DIR / 'crm_b.csv'} ({len(CRM_B_ROWS)} rows)")

    # the SAME literal rows, ADDITIONALLY written as sqlite — the wallet runtime's format.
    _write_db(SEED_DIR / "crm_a.db", "contacts", "id", CRM_A_HEADER, CRM_A_ROWS)
    _write_db(SEED_DIR / "crm_b.db", "contacts", "contact_id", CRM_B_HEADER, CRM_B_ROWS)
    print(f"wrote {SEED_DIR / 'crm_a.db'} ({len(CRM_A_ROWS)} rows)")
    print(f"wrote {SEED_DIR / 'crm_b.db'} ({len(CRM_B_ROWS)} rows)")

    # the two wallet-only sqlite sources — no CSV twins.
    _write_db(SEED_DIR / "whatsapp_calls.db", "calls", "call_id", CALLS_HEADER, CALLS_ROWS)
    _write_db(SEED_DIR / "personal_notes.db", "notes", "note_id", NOTES_HEADER, NOTES_ROWS)
    print(f"wrote {SEED_DIR / 'whatsapp_calls.db'} ({len(CALLS_ROWS)} rows)")
    print(f"wrote {SEED_DIR / 'personal_notes.db'} ({len(NOTES_ROWS)} rows)")

    # the edge-resident transcript content — a raw file, not a table.
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    (TRANSCRIPT_DIR / "call_c1.txt").write_text(TRANSCRIPT_C1, encoding="utf-8")
    print(f"wrote {TRANSCRIPT_DIR / 'call_c1.txt'}")


if __name__ == "__main__":
    main()
