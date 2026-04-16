"""Look up Project Gutenberg IDs for each novel in corpus.csv via the Gutendex API.

Writes results back to corpus.csv, appending a "Gutenberg ID" column.
Multi-volume works (e.g., The Wings of the Dove) produce one row per volume;
duplicate editions of the same work collapse to the most popular one.
"""

import csv
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CORPUS_CSV = Path(__file__).parent / "corpus.csv"
API_BASE = "https://gutendex.com/books"
SEARCH_DELAY = 1.0       # seconds between API requests
TITLE_THRESHOLD = 0.85   # minimum title match score
TIMEOUT = 30             # seconds per HTTP request
MAX_RETRIES = 3

FIELDNAMES = [
    "Novel Title",
    "Novel year of publication",
    "Author",
    "Author Birth",
    "Author Death",
    "Period",
    "Count",
    "Z Score",
    "Gutenberg ID",
]

# Matches explicit volume/part indicators in Gutenberg titles.
# Handles both arabic digits ("Volume 1") and roman numerals ("Volume II").
# Roman numeral alternation: [IVXivx]+ followed by a word boundary avoids
# false matches on plain words (e.g. "Volume in" — 'n' prevents \b after 'i').
_VOLUME_RE = re.compile(
    r"\bvol(?:ume)?\.?\s*(?:\d+|[ivxlcdm]+)\b"   # "Vol. 1", "Volume II"
    r"|\bpart\s+(?:\d+|[ivxlcdm]+)\b"              # "Part 1", "Part II"
    r"|\bbook\s+(?:\d+|[ivxlcdm]+)\b",             # "Book 1", "Book II"
    re.IGNORECASE,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def author_last_name(author_field: str) -> str:
    """Return the surname from 'Last, First[, Suffix]' format."""
    return author_field.split(",", 1)[0].strip()


def title_match_score(corpus_title: str, gutenberg_title: str) -> float:
    """Score how well a Gutenberg title matches the corpus title (0–1)."""
    c = corpus_title.lower().strip()
    g = gutenberg_title.lower().strip()

    if c == g:
        return 1.0

    # Strip leading articles before comparison
    for article in ("the ", "a ", "an "):
        if c.startswith(article):
            c = c[len(article):]
        if g.startswith(article):
            g = g[len(article):]

    if c == g:
        return 0.99

    # Prefix match: "Moby-Dick" vs "Moby-Dick; Or The Whale"
    # When Gutenberg's title is longer, only accept if the extra text begins
    # with a subtitle separator ("; or", ": a", "—", etc.) not more content
    # words (which would indicate a completely different book like
    # "The American Occupation of the Philippines" for "The American").
    if g.startswith(c):
        remainder = g[len(c):]
        if not remainder or re.match(r"^[\s]*[;:.,\u2014\u2013(\[/]", remainder):
            return 0.95
    elif c.startswith(g):
        return 0.95

    # Substring match — only if the shorter string is at least half
    # the length of the longer (prevents short words matching inside
    # long, unrelated titles, e.g. "ambassadors" in a 100-word title)
    short, long = (c, g) if len(c) <= len(g) else (g, c)
    if short in long and len(short) / len(long) >= 0.5:
        return 0.90

    # Word-level Jaccard similarity as final fallback.
    # Character-level SequenceMatcher conflates near-homophones like
    # "Little Men" / "Little Women" because "men" is a suffix of "women".
    c_words = set(re.findall(r"\w+", c))
    g_words = set(re.findall(r"\w+", g))
    if not c_words or not g_words:
        return 0.0
    return len(c_words & g_words) / len(c_words | g_words)


def gutendex_search(params: dict) -> dict:
    """Call Gutendex and return parsed JSON, retrying on transient errors."""
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"\n  Attempt {attempt} failed ({exc}); retrying in {wait}s …")
                time.sleep(wait)
            else:
                print(f"\n  Warning: giving up after {MAX_RETRIES} attempts ({exc})")
    return {"results": []}


def find_gutenberg_matches(title: str, author: str) -> list[tuple[int, str]]:
    """Return (gutenberg_id, gutenberg_title) pairs for this corpus entry.

    - One pair  → single edition found
    - Zero pairs → no match
    - Multiple  → confirmed multi-volume work (each title has a volume marker)
      Duplicate editions (same work, no volume marker) are collapsed to the
      most popular one (Gutendex default sort order).
    """
    last = author_last_name(author)
    data = gutendex_search({"search": f"{title} {last}", "languages": "en"})

    seen: set[int] = set()
    matches: list[tuple[int, str]] = []

    for book in data.get("results", []):
        score = title_match_score(title, book["title"])
        if score < TITLE_THRESHOLD:
            continue
        if not any(
            last.lower() in person["name"].lower()
            for person in book.get("authors", [])
        ):
            continue
        # Skip audiobook-only entries (no real plain-text file).
        # Gutendex marks these with a text/plain key pointing to a readme.
        formats = book.get("formats", {})
        has_text = any(
            "text/plain" in mime and not url.endswith("readme.txt")
            for mime, url in formats.items()
        )
        if not has_text:
            continue
        # Skip audiobook reading scripts whose title or subtitle contains
        # "Reading by <name>" (e.g. "…: Reading by Steve Andersen").
        reading_fields = book.get("title", "") + " " + book.get("subtitle", "")
        if re.search(r"\breading by\b", reading_fields, re.IGNORECASE):
            continue
        gid = book["id"]
        if gid not in seen:
            seen.add(gid)
            matches.append((gid, book["title"]))

    if not matches:
        return []

    # Partition into volumed vs. non-volumed titles
    volumed     = [(gid, t) for gid, t in matches if _VOLUME_RE.search(t)]
    non_volumed = [(gid, t) for gid, t in matches if not _VOLUME_RE.search(t)]

    if non_volumed:
        # A complete (non-split) edition exists — return just the most popular
        return non_volumed[:1]

    # Only volumed editions found — return all (genuine multi-volume work)
    return volumed


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    with CORPUS_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    output_rows: list[dict] = []
    total = len(rows)

    for i, row in enumerate(rows, 1):
        title = row["Novel Title"]
        author = row["Author"]
        print(f"[{i:3}/{total}] {title!r} ...", end=" ", flush=True)

        matches = find_gutenberg_matches(title, author)

        if not matches:
            print("no match")
            output_rows.append({**row, "Gutenberg ID": ""})
        elif len(matches) == 1:
            gid, gtitle = matches[0]
            print(f"→ {gid}  ({gtitle!r})")
            output_rows.append({**row, "Gutenberg ID": gid})
        else:
            print(f"→ {len(matches)} volumes")
            for gid, gtitle in matches:
                print(f"       {gid}: {gtitle!r}")
                output_rows.append({**row, "Gutenberg ID": gid})

        time.sleep(SEARCH_DELAY)

    with CORPUS_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)

    matched = sum(1 for r in output_rows if r["Gutenberg ID"])
    print(f"\nDone. {matched}/{len(output_rows)} rows have a Gutenberg ID.")
    print(f"Wrote {len(output_rows)} rows to {CORPUS_CSV}")


if __name__ == "__main__":
    main()
