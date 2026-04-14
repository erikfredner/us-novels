"""Download plain-text Project Gutenberg files for novels in corpus.csv.

Reads the "Gutenberg ID" column, downloads each unique non-empty ID's
.txt file to texts/pg{id}.txt, and waits 2 seconds between requests
per Gutenberg's robot guidelines (equivalent to wget -w 2).

Safe to re-run: already-downloaded files are skipped.
"""

import csv
import time
import urllib.error
import urllib.request
from pathlib import Path

CORPUS_CSV = Path(__file__).parent / "corpus.csv"
TEXTS_DIR = Path(__file__).parent / "texts"
DOWNLOAD_DELAY = 2.0  # seconds — matches Gutenberg's wget -w 2 guideline
TIMEOUT = 60          # seconds per HTTP request
MAX_RETRIES = 3

# Tried in order for each ID.  cache/epub is the modern robot-friendly path;
# the files/ fallbacks handle older catalog entries.
URL_PATTERNS = [
    "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt",
    "https://www.gutenberg.org/files/{id}/{id}-0.txt",
    "https://www.gutenberg.org/files/{id}/{id}.txt",
]


def download_text(gid: int) -> bytes | None:
    """Try each URL pattern in order; return content bytes or None on failure."""
    for pattern in URL_PATTERNS:
        url = pattern.format(id=gid)
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    break  # this pattern has no file — try next pattern
                if attempt < MAX_RETRIES:
                    wait = 2 ** attempt
                    print(f"\n  Attempt {attempt} failed (HTTP {exc.code}); retrying in {wait}s …")
                    time.sleep(wait)
                else:
                    print(f"\n  Warning: HTTP {exc.code} after {MAX_RETRIES} attempts")
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < MAX_RETRIES:
                    wait = 2 ** attempt
                    print(f"\n  Attempt {attempt} failed ({exc}); retrying in {wait}s …")
                    time.sleep(wait)
                else:
                    print(f"\n  Warning: {exc} after {MAX_RETRIES} attempts")
    return None


def main() -> None:
    TEXTS_DIR.mkdir(exist_ok=True)

    with CORPUS_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Collect unique non-empty IDs, preserving first-occurrence order.
    seen: set[int] = set()
    gids: list[int] = []
    for row in rows:
        raw = row.get("Gutenberg ID", "").strip()
        if raw:
            gid = int(raw)
            if gid not in seen:
                seen.add(gid)
                gids.append(gid)

    total = len(gids)
    print(f"Found {total} unique Gutenberg IDs to download.")

    downloaded = skipped = failed = 0
    for i, gid in enumerate(gids, 1):
        dest = TEXTS_DIR / f"pg{gid}.txt"
        print(f"[{i:3}/{total}] {gid} ...", end=" ", flush=True)

        if dest.exists():
            print("already exists, skipping")
            skipped += 1
            continue

        content = download_text(gid)
        if content is None:
            print("FAILED (no URL pattern matched)")
            failed += 1
        else:
            dest.write_bytes(content)
            kb = len(content) // 1024
            print(f"saved ({kb} KB)")
            downloaded += 1

        time.sleep(DOWNLOAD_DELAY)

    print(f"\nDone. {downloaded} downloaded, {skipped} skipped, {failed} failed.")
    print(f"Text files are in {TEXTS_DIR}/")


if __name__ == "__main__":
    main()
