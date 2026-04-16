"""Look up VIAF IDs and gender for each unique author in corpus.csv.

Uses two VIAF endpoints:
  - AutoSuggest  → VIAF ID
  - SRU search   → gender (from the fixed-field <ns2:gender> element)

Writes results to viaf_ids.csv with columns:
  Author, Author Birth, Author Death, VIAF ID, Gender
"""

import csv
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CORPUS_CSV = Path(__file__).parent / "corpus.csv"
OUTPUT_CSV = Path(__file__).parent / "viaf_ids.csv"
VIAF_AUTOSUGGEST = "https://viaf.org/viaf/AutoSuggest"
VIAF_SRU = "https://viaf.org/viaf/search"
# Cloudflare on viaf.org blocks Python's default User-Agent; a browser UA is required.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
SEARCH_DELAY = 1.0
TIMEOUT = 30
MAX_RETRIES = 3

# MARC 21 gender codes used in VIAF fixed fields
GENDER_CODES: dict[str, str] = {
    "a": "female",
    "b": "male",
    "c": "transgender",
    "n": "not applicable",
    "u": "unknown",
}

OUTPUT_FIELDNAMES = ["Author", "Author Birth", "Author Death", "VIAF ID", "Gender"]


# ── Helpers ────────────────────────────────────────────────────────────────────


def name_tokens(name: str) -> set[str]:
    """Return lowercase ASCII tokens for name similarity — skip digits and noise words."""
    nfd = unicodedata.normalize("NFD", name)
    ascii_only = nfd.encode("ascii", "ignore").decode()
    tokens = set(re.findall(r"\w+", ascii_only.lower()))
    # Drop single characters (initials), digit strings (years), and honorific suffixes
    return {t for t in tokens if len(t) > 1 and not t.isdigit() and t not in ("jr", "sr", "ii", "iii", "iv")}


def name_similarity(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two name strings."""
    ta = name_tokens(a)
    tb = name_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def extract_years(text: str) -> list[int]:
    """Extract plausible 4-digit year numbers from a string."""
    return [int(y) for y in re.findall(r"\b(1[5-9]\d\d|20\d\d)\b", text)]


def viaf_autosuggest(query: str) -> list[dict]:
    """Query VIAF AutoSuggest and return the result list, retrying on transient errors."""
    url = f"{VIAF_AUTOSUGGEST}?{urllib.parse.urlencode({'query': query})}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.load(resp)
                return data.get("result") or []
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"\n  Attempt {attempt} failed ({exc}); retrying in {wait}s …")
                time.sleep(wait)
            else:
                print(f"\n  Warning: giving up after {MAX_RETRIES} attempts ({exc})")
    return []


def score_candidate(candidate: dict, author: str, birth: str, death: str) -> float:
    """Score a VIAF AutoSuggest candidate.

    Returns -1 for non-personal names; otherwise a float where higher is better.
    Date matches contribute most (1 point each); name similarity fills the gap
    when dates are absent or ambiguous.
    """
    if candidate.get("nametype") != "personal":
        return -1.0

    term = candidate.get("term", "")
    years = extract_years(term)

    date_score = 0.0
    if birth and int(birth) in years:
        date_score += 1.0
    if death and int(death) in years:
        date_score += 1.0

    sim = name_similarity(author, term)
    return date_score * 2 + sim


def find_viaf_id(author: str, birth: str, death: str) -> str:
    """Return the best-matching VIAF ID for an author, or '' if not found."""
    results = viaf_autosuggest(author)

    personal = [r for r in results if r.get("nametype") == "personal"]
    if not personal:
        return ""

    scored = sorted(
        ((score_candidate(r, author, birth, death), r) for r in personal),
        key=lambda x: x[0],
        reverse=True,
    )
    best_score, best = scored[0]

    # Require at least minimal name overlap to avoid false positives
    if name_similarity(author, best.get("term", "")) < 0.3:
        return ""

    return str(best.get("viafid", ""))


def fetch_gender(viaf_id: str) -> str:
    """Return the decoded gender value for a VIAF personal record, or '' if absent."""
    url = f"{VIAF_SRU}?{urllib.parse.urlencode({
        'query': f'local.viafID exact \"{viaf_id}\"',
        'maximumRecords': '1',
    })}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "text/xml"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read().decode()
            m = re.search(r"<ns2:gender>([^<]*)</ns2:gender>", body)
            if m:
                code = m.group(1).strip()
                return GENDER_CODES.get(code, code)
            return ""
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"\n  Attempt {attempt} failed ({exc}); retrying in {wait}s …")
                time.sleep(wait)
            else:
                print(f"\n  Warning: giving up after {MAX_RETRIES} attempts ({exc})")
    return ""


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    with CORPUS_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Deduplicate authors preserving first-appearance order (= descending z-score)
    seen: set[str] = set()
    authors: list[dict] = []
    for row in rows:
        name = row["Author"]
        if name not in seen:
            seen.add(name)
            authors.append({
                "Author": name,
                "Author Birth": row["Author Birth"],
                "Author Death": row["Author Death"],
            })

    total = len(authors)
    output_rows: list[dict] = []
    matched = 0

    for i, info in enumerate(authors, 1):
        name = info["Author"]
        birth = info["Author Birth"]
        death = info["Author Death"]

        print(f"[{i:3}/{total}] {name!r} ...", end=" ", flush=True)

        viaf_id = find_viaf_id(name, birth, death)

        if viaf_id:
            matched += 1
            time.sleep(SEARCH_DELAY)
            gender = fetch_gender(viaf_id)
            print(f"→ {viaf_id}  gender={gender!r}")
        else:
            gender = ""
            print("no match")

        output_rows.append({
            "Author": name,
            "Author Birth": birth,
            "Author Death": death,
            "VIAF ID": viaf_id,
            "Gender": gender,
        })

        time.sleep(SEARCH_DELAY)

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nDone. {matched}/{total} authors matched.")
    print(f"Wrote {total} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
