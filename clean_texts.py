"""Strip Project Gutenberg boilerplate and merge multi-volume works.

Assigns a stable human-readable 'Corpus ID' to every row in corpus.csv
(including works without Gutenberg texts) and writes cleaned plain-text
files to texts/cleaned/{corpus_id}.txt.

Multi-volume works (same title/author/year, multiple Gutenberg IDs) are
merged into a single file in CSV row order (= correct volume order).

Safe to re-run: already-cleaned files are skipped.
"""

import csv
import re
import unicodedata
from pathlib import Path

CORPUS_CSV  = Path(__file__).parent / "corpus.csv"
TEXTS_DIR   = Path(__file__).parent / "texts"
CLEANED_DIR = Path(__file__).parent / "texts" / "cleaned"

TITLE_MAX_LEN = 30

FIELDNAMES = [
    "Novel Title",
    "Novel year of publication",
    "Author",
    "Author Birth",
    "Author Death",
    "Period",
    "Count",
    "Z Score",
    "Corpus ID",
    "Gutenberg ID",
]

# Header marker: "*** START OF THE/THIS PROJECT GUTENBERG EBOOK …"
# \*{3}\s* handles "***START" (no space after asterisks, seen in older files).
START_RE = re.compile(
    r"^\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK",
    re.IGNORECASE,
)

# Footer marker: "*** END OF THE/THIS PROJECT GUTENBERG EBOOK …"
# Second branch handles old-style "End of Project Gutenberg's …" footers.
END_RE = re.compile(
    r"^\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK"
    r"|^End of (?:the )?Project Gutenberg",
    re.IGNORECASE,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalize_str(s: str) -> str:
    """NFD-normalize and strip combining characters (removes diacritics)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def make_slug(title: str, author: str, year: str) -> str:
    """Return a human-readable, filesystem-safe corpus ID slug.

    Format: {author_last}_{normalized_title}_{year}
    Examples:
      make_slug("Moby-Dick", "Melville, Herman", "1851") → "melville_moby-dick_1851"
      make_slug("The Portrait of a Lady", "James, Henry, Jr.", "1881")
          → "james_the-portrait-of-a-lady_1881"
    """
    # Author last name: text before first comma, normalized, alphanumeric only
    last = author.split(",", 1)[0].strip()
    last = _normalize_str(last).lower()
    last = re.sub(r"[^a-z0-9]", "", last)

    # Title: normalize → strip punctuation → spaces to hyphens → truncate
    t = _normalize_str(title).lower()
    t = re.sub(r"[^a-z0-9\s]", "", t).strip()
    t = re.sub(r"\s+", "-", t)
    if len(t) > TITLE_MAX_LEN:
        cut = t[:TITLE_MAX_LEN]
        pos = cut.rfind("-")
        t = cut[:pos] if pos > 0 else cut

    return f"{last}_{t}_{year}"


def assign_corpus_ids(rows: list[dict]) -> list[dict]:
    """Add a 'Corpus ID' key to every row dict (mutates in place).

    Rows sharing (title, author, year) receive the same corpus ID —
    this covers multi-volume works. After all IDs are generated, detect
    and resolve slug collisions by appending _b, _c, … in CSV order.
    """
    work_to_slug: dict[tuple, str] = {}

    for row in rows:
        key = (row["Novel Title"], row["Author"], row["Novel year of publication"])
        if key not in work_to_slug:
            work_to_slug[key] = make_slug(
                row["Novel Title"], row["Author"], row["Novel year of publication"]
            )
        row["Corpus ID"] = work_to_slug[key]

    # Collision detection: two *different* works with the same slug.
    slug_to_keys: dict[str, list[tuple]] = {}
    for key, slug in work_to_slug.items():
        slug_to_keys.setdefault(slug, []).append(key)

    suffixes = "bcdefghijklmnopqrstuvwxyz"
    for slug, keys in slug_to_keys.items():
        if len(keys) > 1:
            print(f"  WARNING: slug collision '{slug}' — {len(keys)} works share it")
            for i, key in enumerate(keys[1:]):
                new_slug = f"{slug}_{suffixes[i]}"
                work_to_slug[key] = new_slug
                print(f"    → '{key[0]}' remapped to '{new_slug}'")

    # Apply any remapped slugs back to the rows
    for row in rows:
        key = (row["Novel Title"], row["Author"], row["Novel year of publication"])
        row["Corpus ID"] = work_to_slug[key]

    return rows


def strip_gutenberg_boilerplate(text: str, filename: str = "") -> str:
    """Extract the literary content between Gutenberg's START and END markers."""
    lines = text.splitlines()

    start_idx = None
    for i, line in enumerate(lines):
        if START_RE.match(line.strip()):
            start_idx = i
            break

    end_idx = None
    for i, line in enumerate(lines):
        if END_RE.match(line.strip()):
            end_idx = i
            break

    if start_idx is None:
        label = filename or "unknown"
        print(f"\n  WARNING: no START marker found in {label}; returning full text")
        content_lines = list(lines)
    else:
        content_lines = list(lines[start_idx + 1 : end_idx])

    # Strip leading/trailing blank lines
    while content_lines and not content_lines[0].strip():
        content_lines.pop(0)
    while content_lines and not content_lines[-1].strip():
        content_lines.pop()

    return "\n".join(content_lines) + "\n"


def collect_work_groups(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group rows by Corpus ID, preserving first-occurrence (CSV) order.

    Returns list of (corpus_id, [row, ...]) tuples. Within each group,
    rows appear in CSV order, which is the correct volume order.
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        cid = row["Corpus ID"]
        groups.setdefault(cid, []).append(row)
    return list(groups.items())


def clean_work(
    corpus_id: str,
    vol_rows: list[dict],
    texts_dir: Path,
    cleaned_dir: Path,
) -> str:
    """Clean and merge one work's volume(s). Returns a status string."""
    output_path = cleaned_dir / f"{corpus_id}.txt"

    if output_path.exists():
        return "skipped (already exists)"

    gids = [r["Gutenberg ID"].strip() for r in vol_rows]
    if not any(gids):
        return "no Gutenberg ID — manual gathering needed"

    vol_texts: list[str] = []
    for row, gid in zip(vol_rows, gids):
        if not gid:
            return "incomplete: some volumes missing Gutenberg ID"
        src = texts_dir / f"pg{gid}.txt"
        if not src.exists():
            return f"MISSING source file pg{gid}.txt"
        raw = src.read_text(encoding="utf-8", errors="replace")
        cleaned = strip_gutenberg_boilerplate(raw, src.name)
        vol_texts.append(cleaned)

    merged = "\n\n".join(vol_texts)
    output_path.write_text(merged, encoding="utf-8")
    n = len(vol_texts)
    return f"written ({n} vol{'s' if n > 1 else ''}, {len(merged):,} chars)"


def load_corpus(path: Path) -> list[dict]:
    """Read corpus.csv and return all rows as a list of dicts."""
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_corpus(rows: list[dict], path: Path) -> None:
    """Rewrite corpus.csv in place with the Corpus ID column included."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    CLEANED_DIR.mkdir(parents=True, exist_ok=True)

    rows = load_corpus(CORPUS_CSV)
    rows = assign_corpus_ids(rows)
    write_corpus(rows, CORPUS_CSV)
    print(f"Corpus IDs assigned and written to {CORPUS_CSV}\n")

    groups = collect_work_groups(rows)
    total = len(groups)
    print(f"Processing {total} unique works...\n")

    cleaned = skipped = manual = errors = 0
    for i, (corpus_id, vol_rows) in enumerate(groups, 1):
        title = vol_rows[0]["Novel Title"]
        n_vols = len(vol_rows)
        vol_label = f", {n_vols} vols" if n_vols > 1 else ""
        print(f"[{i:3}/{total}] {corpus_id}{vol_label} ...", end=" ", flush=True)

        status = clean_work(corpus_id, vol_rows, TEXTS_DIR, CLEANED_DIR)
        print(status)

        if status.startswith("written"):
            cleaned += 1
        elif status.startswith("skipped"):
            skipped += 1
        elif "manual" in status:
            manual += 1
        else:
            errors += 1

    print(
        f"\nDone. {cleaned} cleaned, {skipped} skipped, "
        f"{manual} need manual gathering, {errors} errors."
    )
    print(f"Cleaned texts are in {CLEANED_DIR}/")


if __name__ == "__main__":
    main()
