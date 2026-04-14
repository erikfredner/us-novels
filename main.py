import csv
import re
import statistics
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data"
XML_FILES = sorted(DATA_DIR.glob("*.xml"))
OUTPUT_CSV = Path(__file__).parent / "corpus.csv"
PUB_YEAR_CUTOFF = 1930

# ── Regex patterns ─────────────────────────────────────────────────────────────

PERIOD_RE = re.compile(r"^\d{4}-\d{4}$")

# Matches "Last, First (YYYY-YYYY)", "Last, First (1951- )", "Last, Jr. (ca.1700-1760)",
# "Last, First (fl. 1820)", "Last, First (b. 1800)", etc.
DATED_AUTHOR_RE = re.compile(
    r"^[A-Z\u00C0-\u024F].+,\s+.+\("
    r"(?:\d{3,4}[/\d]*\??-(?:ca\.\s*)?[\d\s\?]*"
    r"|(?:ca\.|fl\.|b\.|d\.)\s*\d{3,4}(?:[/\-]\d{2,4})?)"
    r"\s*\)$"
)

# Matches "Last, First" with no parenthetical dates
UNDATED_AUTHOR_RE = re.compile(r"^[A-Z][A-Za-z'\-]+,\s+[A-Z][A-Za-z\.\s']+$")

# Matches "Some Title (1851)"
WORK_SIMPLE_RE = re.compile(r"^(.+)\s+\((\d{4})\)$")

# Matches "Some Title (1893, rev. 1896)" or "Title (1893/1896)" — use first year
WORK_COMPLEX_RE = re.compile(r"^(.+?)\s+\((\d{4})[^)]+\)$")


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_canonical_period(year: int) -> str:
    c = (year // 100) * 100
    return f"{c}-{c + 99}"


def parse_author_token(token: str) -> tuple[str, int | None, int | None]:
    """Extract (display_name, birth_year, death_year) from an author token."""
    m = re.match(r"^(.+?)\s*\((.+)\)\s*$", token)
    if not m:
        return token.strip(), None, None

    name = m.group(1).strip()
    date_str = m.group(2).strip()

    # Standard: YYYY-YYYY or YYYY- (living) or YYYY?-YYYY, including ca. on death side
    m2 = re.match(r"(\d{3,4})[/\d]*\??-(?:ca\.\s*)?(\d{4})?\s*$", date_str)
    if m2:
        birth = int(m2.group(1))
        death = int(m2.group(2)) if m2.group(2) else None
        return name, birth, death

    # ca. on birth side: "ca. 1700-1760"
    m3 = re.match(r"ca\.\s*(\d{3,4})-(\d{3,4})$", date_str)
    if m3:
        return name, int(m3.group(1)), int(m3.group(2))

    # fl./b./d./ca. single year
    m4 = re.match(r"(?:ca\.|fl\.|b\.|d\.)\s*(\d{3,4})", date_str)
    if m4:
        return name, int(m4.group(1)), None

    return name, None, None


def classify_token(tok: str, nat_lit_types: frozenset) -> str:
    """Return token type: 'nat_lit', 'period', 'author_dated', 'author_undated',
    'work', or 'other'."""
    if tok in nat_lit_types:
        return "nat_lit"
    if PERIOD_RE.match(tok):
        return "period"
    if DATED_AUTHOR_RE.match(tok):
        return "author_dated"
    # Work check: must end with (YYYY) or (YYYY...) but NOT be an author
    m_simple = WORK_SIMPLE_RE.match(tok)
    if m_simple and not DATED_AUTHOR_RE.match(tok):
        return "work"
    m_complex = WORK_COMPLEX_RE.match(tok)
    if m_complex and not DATED_AUTHOR_RE.match(tok):
        return "work"
    if UNDATED_AUTHOR_RE.match(tok):
        return "author_undated"
    return "other"


def extract_work_year(tok: str) -> tuple[str, int] | None:
    """Return (title, year) from a work token, or None."""
    m = WORK_SIMPLE_RE.match(tok)
    if m:
        return m.group(1).strip(), int(m.group(2))
    m = WORK_COMPLEX_RE.match(tok)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return None


# ── Pre-scans ─────────────────────────────────────────────────────────────────

def collect_nat_lit_types(xml_files: list[Path]) -> frozenset[str]:
    """Collect every token that appears at position 0 of a subjects string.
    These are always national literature markers (e.g. 'American literature')."""
    types: set[str] = set()
    for path in xml_files:
        tree = ET.parse(path)
        for subj_el in tree.iterfind(".//subjects"):
            text = (subj_el.text or "").strip()
            if not text:
                continue
            first_tok = text.split(" ; ")[0].strip()
            if first_tok:
                types.add(first_tok)
    return frozenset(types)


def build_author_nat_profiles(
    xml_files: list[Path], nat_lit_types: frozenset
) -> tuple[dict, dict]:
    """For each author, count co-occurrences with non-American national literature
    tokens across all records (no filter applied).

    Returns:
        author_nat:   author_name → Counter(foreign_nat_lit → record_count)
        author_total: author_name → total records the author appeared in
    """
    author_nat: dict[str, Counter] = defaultdict(Counter)
    author_total: dict[str, int] = defaultdict(int)

    for path in xml_files:
        tree = ET.parse(path)
        for record in tree.iterfind(".//record"):
            subj_el = record.find("subjects")
            if subj_el is None:
                continue
            text = (subj_el.text or "").strip()
            if not text:
                continue

            tokens = [t.strip() for t in text.split(" ; ")]

            # Collect all foreign national literature tokens present in this record
            foreign_nat_lits = {
                t for t in tokens
                if t in nat_lit_types
                and t != "American literature"
                and not t.startswith("American literature ")
            }

            # Associate every author in this record with the foreign nat lits
            for tok in tokens:
                if DATED_AUTHOR_RE.match(tok) or UNDATED_AUTHOR_RE.match(tok):
                    name, _, _ = parse_author_token(tok)
                    author_total[name] += 1
                    for nat_lit in foreign_nat_lits:
                        author_nat[name][nat_lit] += 1

    return dict(author_nat), dict(author_total)


def is_american_author(name: str, author_nat: dict, author_total: dict) -> bool:
    """Return True if the author should be treated as American.

    An author is considered non-American if their single most common
    non-American national literature appears in >= 50% of all records
    where they appear as a subject.
    """
    total = author_total.get(name, 0)
    if total == 0:
        return True  # no profile → include by default
    foreign = author_nat.get(name)
    if not foreign:
        return True  # never co-occurs with any foreign nat lit
    top_count = foreign.most_common(1)[0][1]
    return top_count / total < 0.5


# ── Core parsing ───────────────────────────────────────────────────────────────

def process_xml_files(
    xml_files: list[Path],
    nat_lit_types: frozenset,
    author_nat: dict,
    author_total: dict,
) -> dict:
    """Return novel_data dict: Key(title, pub_year, author_token) -> metadata dict."""
    novel_data: dict[tuple, dict] = {}
    cited_per_record: dict[str, set] = defaultdict(set)

    for path in xml_files:
        print(f"Parsing {path.name}...")
        tree = ET.parse(path)
        for record in tree.iterfind(".//record"):
            subj_el = record.find("subjects")
            if subj_el is None:
                continue
            subj_text = (subj_el.text or "").strip()
            if not subj_text:
                continue

            # Fast pre-filters
            if "American literature" not in subj_text:
                continue
            tokens = subj_text.split(" ; ")
            if "novel" not in tokens:
                continue

            an_el = record.find("an")
            rec_id = (an_el.text or "").strip() if an_el is not None else str(id(record))

            _run_state_machine(
                tokens, nat_lit_types, rec_id, novel_data, cited_per_record,
                author_nat, author_total,
            )

    return novel_data


def _run_state_machine(
    tokens: list[str],
    nat_lit_types: frozenset,
    rec_id: str,
    novel_data: dict,
    cited_per_record: dict,
    author_nat: dict,
    author_total: dict,
) -> None:
    in_am_lit = False
    current_period: str | None = None
    current_author: str | None = None

    for raw_tok in tokens:
        tok = raw_tok.strip()

        # Check for "American literature" — may be fused with period: "American literature 1800-1899"
        if tok == "American literature" or tok.startswith("American literature "):
            in_am_lit = True
            current_period = None
            current_author = None
            # Handle fused period token
            if tok != "American literature":
                suffix = tok[len("American literature"):].strip()
                if PERIOD_RE.match(suffix):
                    current_period = suffix
            continue

        kind = classify_token(tok, nat_lit_types)

        if kind == "nat_lit":
            in_am_lit = False
            current_period = None
            current_author = None
            continue

        if kind == "period":
            if current_period is None:
                current_period = tok
        elif kind in ("author_dated", "author_undated"):
            current_author = tok
        elif kind == "work":
            result = extract_work_year(tok)
            if result is None:
                continue
            title, year = result
            if year <= PUB_YEAR_CUTOFF and current_author:
                name, birth, death = parse_author_token(current_author)
                if birth is not None and year < birth:
                    continue
                if not is_american_author(name, author_nat, author_total):
                    continue
                key = (title, year, current_author)
                if key not in cited_per_record[rec_id]:
                    cited_per_record[rec_id].add(key)
                    if key not in novel_data:
                        novel_data[key] = {
                            "count": 0,
                            "author_name": name,
                            "author_birth": birth,
                            "author_death": death,
                            "canonical_period": get_canonical_period(year),
                        }
                    novel_data[key]["count"] += 1
        # "other" tokens: no-op (current_author context persists)


# ── Z-score computation ────────────────────────────────────────────────────────

def compute_z_scores(novel_data: dict) -> list[dict]:
    # Group counts by canonical period
    period_counts: dict[str, list[int]] = defaultdict(list)
    for data in novel_data.values():
        period_counts[data["canonical_period"]].append(data["count"])

    period_stats: dict[str, tuple[float, float]] = {}
    for period, counts in period_counts.items():
        mean = statistics.mean(counts)
        std = statistics.stdev(counts) if len(counts) >= 2 else 0.0
        period_stats[period] = (mean, std)

    rows = []
    for (title, year, _author_tok), data in novel_data.items():
        mean, std = period_stats[data["canonical_period"]]
        z = (data["count"] - mean) / std if std > 0 else 0.0
        rows.append(
            {
                "Novel Title": title,
                "Novel year of publication": year,
                "Author": data["author_name"],
                "Author Birth": data["author_birth"] if data["author_birth"] is not None else "",
                "Author Death": data["author_death"] if data["author_death"] is not None else "",
                "Period": data["canonical_period"],
                "Count": data["count"],
                "Z Score": round(z, 6),
            }
        )

    rows = [r for r in rows if r["Z Score"] >= 0]
    # Sort: Z Score descending, then Author ascending
    rows.sort(key=lambda r: (-r["Z Score"], r["Author"]))
    return rows


# ── Output ─────────────────────────────────────────────────────────────────────

FIELDNAMES = [
    "Novel Title",
    "Novel year of publication",
    "Author",
    "Author Birth",
    "Author Death",
    "Period",
    "Count",
    "Z Score",
]


def write_csv(rows: list[dict], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    print("Collecting national literature types...")
    nat_lit_types = collect_nat_lit_types(XML_FILES)
    print(f"  Found {len(nat_lit_types)} national literature type tokens.")

    print("Building author nationality profiles...")
    author_nat, author_total = build_author_nat_profiles(XML_FILES, nat_lit_types)
    print(f"  Profiled {len(author_total)} unique authors.")

    novel_data = process_xml_files(XML_FILES, nat_lit_types, author_nat, author_total)
    print(f"Found {len(novel_data)} unique novels.")

    rows = compute_z_scores(novel_data)
    write_csv(rows, OUTPUT_CSV)
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
