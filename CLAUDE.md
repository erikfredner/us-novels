# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the full corpus-building pipeline (parses XML → corpus.csv)
uv run python main.py

# Look up Gutenberg IDs for all corpus entries (enriches corpus.csv in place)
uv run python gutenberg_ids.py

# Download plain-text files for all Gutenberg IDs in corpus.csv
uv run python download_texts.py

# Strip boilerplate, merge volumes, assign corpus IDs, write texts/cleaned/
uv run python clean_texts.py

# Run all tests
uv run pytest

# Run a single test by name
uv run pytest test_corpus.py::test_raw_subject_count_is_upper_bound -k "Moby-Dick"
```

## Architecture

The pipeline runs in four sequential stages, each a standalone script:

**Stage 1 — `main.py` → `corpus.csv`**
Parses MLAIB XML exports in `data/*.xml`. Each XML file is a century-scoped export (e.g. `1800-1899.xml`); the 1900s required three files due to MLAIB export limits. The core logic is a token-by-token state machine (`_run_state_machine`) that walks semicolon-delimited subject fields to extract novel titles and their citation counts. After counting, z-scores are computed per century and only novels with z ≥ 0 are written to `corpus.csv`. The public-domain cutoff is `PUB_YEAR_CUTOFF = 1930`.

**Stage 2 — `gutenberg_ids.py` → appends "Gutenberg ID" column to `corpus.csv`**
Queries the Gutendex API (`gutendex.com/books`) for each corpus entry. Audiobook-only entries (no real `.txt` file) are filtered out. Multi-volume works produce one row per volume; duplicate editions collapse to the most popular. Rewrites `corpus.csv` in place.

**Stage 3 — `download_texts.py` → `texts/pg{id}.txt`**
Downloads plain-text Gutenberg files for each unique Gutenberg ID. Safe to re-run (skips existing files). Respects Gutenberg's 2-second delay guideline.

**Stage 4 — `clean_texts.py` → `texts/cleaned/{corpus_id}.txt`**
Assigns a stable human-readable `Corpus ID` column to every row in `corpus.csv` (inserted before `Gutenberg ID`), then strips Project Gutenberg headers/footers and writes cleaned texts to `texts/cleaned/`. Multi-volume works (same title/author/year, multiple Gutenberg IDs) are merged into a single file in CSV row order (= correct volume order). Works without Gutenberg IDs receive a Corpus ID but no cleaned file; the script reports those at completion for manual gathering. Safe to re-run: already-cleaned files are skipped.

Corpus ID format: `{author_last}_{normalized_title}_{year}` (e.g., `melville_moby-dick_1851`, `james_the-portrait-of-a-lady_1881`). Author last name is extracted from `"Last, First"` format; diacritics are stripped; non-alphanumeric characters are removed.

**Tests — `test_corpus.py`**
Parametrized pytest suite. For each uniquely-titled novel in `corpus.csv`, verifies that the raw substring count from XML subject fields is ≥ the corpus count (state machine only filters, never inflates), and ≥ 2× the corpus count for high-citation titles. XML data is parsed once at collection time to avoid re-parsing per test.

## Key design notes

- Author nationality disambiguation: `build_author_nat_profiles` scans all records first to build a co-occurrence profile; an author is treated as non-American only if their top foreign national literature appears in ≥ 50% of their records.
- The state machine resets `in_am_lit` context on encountering any non-American national literature token, preventing foreign-literature subjects from being counted.
- `data/queries.md` documents the MLAIB query strings and hit counts used to produce the XML exports.
- Downloaded texts land in `texts/` (gitignored).
