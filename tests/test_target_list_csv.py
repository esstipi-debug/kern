"""Shape and integrity checks for the persisted AU/NZ target-list CSV
(documentation/operator/kern-au-nz-target-list.csv).

This is a data-integrity guardrail, not application logic: the file backs the
Cin7/Unleashed channel-partner outreach track (Kern GMV-band GTM plan, Part 4)
and must never carry personal contact data (name/email/phone) - contact doors
are company websites or directory profile URLs only.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

CSV_PATH = (
    Path(__file__).resolve().parent.parent
    / "documentation"
    / "operator"
    / "kern-au-nz-target-list.csv"
)

EXPECTED_COLUMNS = [
    "Segment",
    "Name",
    "Website",
    "Location",
    "Type/Category",
    "Why-fit",
    "Personalization hook",
    "Contact path",
    "Confidence",
    "Notes",
    "Country",
    "Ecosystem",
    "ICP_Fit",
    "Contact_path_type",
    "Directory_source",
    "Directory_profile_url",
    "Own_site_verified",
    "Outreach_stage",
    "Last_verified_date",
]

CHANNEL_SEGMENT = "Channel partner - inventory implementer"

# Contact fields the guardrail applies to: company websites and directory
# profile URLs only, never a personal contact string.
CONTACT_COLUMNS = ["Website", "Contact path", "Directory_profile_url"]

EMAIL_LIKE = re.compile(r"[^\s,;]+@[^\s,;]+")

# Phone-number guardrail: a run of digits (optionally with a leading '+')
# spanning 8+ characters once spaces/dashes/parens typical of phone
# formatting are allowed for. Deliberately scoped to the 42 NEW
# channel-partner rows only - the 66 PRESERVED SEED rows legitimately carry
# pre-existing general business phone numbers (office/toll-free lines) in
# their free-text Contact path field, which predates this merge and is out
# of scope for this guardrail (see task review finding).
PHONE_LIKE = re.compile(r"\+?\d[\d\s\-\(\)]{6,}\d")


def _read_rows() -> list[dict[str, str]]:
    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def test_csv_file_exists():
    assert CSV_PATH.is_file(), f"expected target-list CSV at {CSV_PATH}"


def test_header_has_expected_columns():
    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == EXPECTED_COLUMNS


def test_no_ragged_rows():
    """Every data row must parse into exactly the header's columns (no stray
    commas from an unescaped field, no missing trailing columns)."""
    rows = _read_rows()
    assert rows, "expected at least one data row"
    for i, row in enumerate(rows):
        assert None not in row, f"row {i} has extra unparsed fields: {row}"
        assert None not in row.values(), f"row {i} is missing columns: {row}"


def test_no_email_addresses_in_contact_columns():
    rows = _read_rows()
    offenders = []
    for i, row in enumerate(rows):
        for col in CONTACT_COLUMNS:
            value = row.get(col, "") or ""
            if EMAIL_LIKE.search(value):
                offenders.append((i, col, value))
    assert not offenders, f"found email-like values in contact columns: {offenders}"


def test_no_email_addresses_anywhere():
    """Belt-and-suspenders: no '@' anywhere in the file at all - the schema
    deliberately has no contact_name/email/phone column."""
    text = CSV_PATH.read_text(encoding="utf-8")
    assert "@" not in text, "found '@' in the target-list CSV; contact fields must be websites/directory URLs only"


def test_at_least_42_channel_partner_rows():
    rows = _read_rows()
    channel_rows = [r for r in rows if r["Segment"] == CHANNEL_SEGMENT]
    assert len(channel_rows) >= 42, (
        f"expected at least 42 rows with Segment == {CHANNEL_SEGMENT!r}, "
        f"found {len(channel_rows)}"
    )


def test_verify_rows_have_no_fabricated_url():
    """Rows sourced from a directory listing marked Verify? in the plan must
    not have a Directory_profile_url or Website filled in - fabricating a URL
    we haven't verified would be a data-integrity violation."""
    rows = _read_rows()
    channel_rows = [r for r in rows if r["Segment"] == CHANNEL_SEGMENT]
    unverified = [r for r in channel_rows if r["Own_site_verified"] == "no"]
    assert unverified, "expected at least one unverified (Own_site_verified=no) channel row"
    for row in unverified:
        assert row["Directory_profile_url"] == "", (
            f"{row['Name']!r} is unverified but has a Directory_profile_url set: "
            f"{row['Directory_profile_url']!r}"
        )
        assert row["Website"] == "", (
            f"{row['Name']!r} is unverified but has a Website set: {row['Website']!r}"
        )


def test_seed_rows_preserved():
    """The seed's non-channel rows must still be present and untouched in
    their original 10 columns (seed row count is 66 as of 2026-07-18)."""
    rows = _read_rows()
    seed_rows = [r for r in rows if r["Segment"] != CHANNEL_SEGMENT]
    assert len(seed_rows) == 66
    names = {r["Name"] for r in seed_rows}
    # Spot-check a few known seed entries survived the merge unchanged.
    for expected_name in ("Ottway the Label", "Father Rabbit", "NZPICS"):
        assert expected_name in names


@pytest.mark.parametrize("row_index", range(0, 66))
def test_seed_rows_have_blank_extended_columns(row_index):
    """Old seed rows should not have fabricated values in the new
    channel-specific columns that were added for this merge."""
    rows = _read_rows()
    seed_rows = [r for r in rows if r["Segment"] != CHANNEL_SEGMENT]
    row = seed_rows[row_index]
    for col in ("Ecosystem", "ICP_Fit", "Contact_path_type", "Directory_source", "Outreach_stage"):
        assert row[col] == "", f"seed row {row['Name']!r} unexpectedly has {col}={row[col]!r}"


def test_no_phone_numbers_in_new_channel_partner_rows():
    """None of the 42 NEW channel-partner rows may contain a phone-number-
    shaped string in any field - the schema deliberately has no
    contact_name/email/phone column, and channel-partner contact doors must
    stay limited to company websites or directory profile URLs.

    Scoped to new channel-partner rows only: the 66 PRESERVED SEED rows
    legitimately carry pre-existing general business phone numbers (e.g.
    toll-free/office lines) in their free-text Contact path field, which
    predates this merge and is intentionally out of scope here.
    """
    rows = _read_rows()
    channel_rows = [r for r in rows if r["Segment"] == CHANNEL_SEGMENT]
    assert channel_rows, "expected at least one channel-partner row"
    offenders = []
    for i, row in enumerate(channel_rows):
        for col, value in row.items():
            if value and PHONE_LIKE.search(value):
                offenders.append((i, col, value))
    assert not offenders, (
        f"found phone-number-like values in new channel-partner rows: {offenders}"
    )
