"""Tests that corpus.csv counts are consistent with raw subject-field searches.

For each novel whose title is unique in corpus.csv, we verify that a simple
substring search of that title across all <subjects> fields in data/*.xml gives
a count that is:

  1. >= the corpus Count  (raw search is an upper bound; the state machine only
     adds filters, never counts things the raw search would miss)
  2. >= corpus Count / 2  (corpus shouldn't be less than half the raw count;
     a larger gap suggests the title is ambiguous or the parser is over-filtering)

The ratio test (2) is applied only to titles with corpus_count >= MIN_COUNT.
Short or common-word titles ("Nature", "Work", "Summer", …) have low citation
counts AND appear as substrings in many unrelated subject strings; they are
skipped for the ratio check because a raw substring search is not a reliable
proxy for them.
"""

# Minimum corpus Count for the ratio test.  Titles below this threshold tend to
# be short common-word titles whose raw substring counts are inflated by
# unrelated records.
MIN_COUNT_FOR_RATIO = 50

import csv
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).parent / "data"
CORPUS_CSV = Path(__file__).parent / "corpus.csv"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _load_corpus() -> list[dict]:
    with CORPUS_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _unique_title_rows(rows: list[dict]) -> list[tuple[str, int]]:
    """Return (title, corpus_count) for titles appearing exactly once in corpus."""
    freq = Counter(r["Novel Title"] for r in rows)
    return [
        (r["Novel Title"], int(r["Count"]))
        for r in rows
        if freq[r["Novel Title"]] == 1
    ]


def _raw_subject_counts(titles: set[str]) -> dict[str, int]:
    """Count XML records where each title appears as a substring in <subjects>."""
    counts: dict[str, int] = {t: 0 for t in titles}
    for xml_path in sorted(DATA_DIR.glob("*.xml")):
        tree = ET.parse(xml_path)
        for record in tree.iterfind(".//record"):
            subj_el = record.find("subjects")
            if subj_el is None:
                continue
            subj_text = subj_el.text or ""
            for title in titles:
                if title in subj_text:
                    counts[title] += 1
    return counts


# ── Fixtures ───────────────────────────────────────────────────────────────────

# Computed once at collection time so we don't re-parse XMLs per test.
_CORPUS_ROWS = _load_corpus()
_UNIQUE_ROWS = _unique_title_rows(_CORPUS_ROWS)
_RAW_COUNTS = _raw_subject_counts({title for title, _ in _UNIQUE_ROWS})


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title,corpus_count",
    _UNIQUE_ROWS,
    ids=[title for title, _ in _UNIQUE_ROWS],
)
def test_raw_subject_count_is_upper_bound(title: str, corpus_count: int) -> None:
    """Raw substring count must be >= corpus Count (state machine only filters)."""
    raw = _RAW_COUNTS[title]
    assert raw >= corpus_count, (
        f"{title!r}: raw subject-field count ({raw}) < corpus count ({corpus_count}). "
        "The state machine should only reduce counts, not inflate them."
    )


@pytest.mark.parametrize(
    "title,corpus_count",
    [(t, c) for t, c in _UNIQUE_ROWS if c >= MIN_COUNT_FOR_RATIO],
    ids=[title for title, count in _UNIQUE_ROWS if count >= MIN_COUNT_FOR_RATIO],
)
def test_raw_subject_count_is_not_too_large(title: str, corpus_count: int) -> None:
    """For high-citation titles, corpus Count should be >= half the raw count.

    Short or common-word titles are excluded (corpus_count < MIN_COUNT_FOR_RATIO)
    because their substrings appear in many unrelated subject strings.
    """
    raw = _RAW_COUNTS[title]
    assert corpus_count >= raw / 2, (
        f"{title!r}: corpus count ({corpus_count}) is less than half of raw ({raw}). "
        "This title may be ambiguous, or the state machine may be over-filtering."
    )
