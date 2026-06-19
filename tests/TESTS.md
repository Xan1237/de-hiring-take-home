# Tests

The suite has 15 tests across three files, one per pipeline stage. All tests run in-process with no network access and no database file on disk.

```
tests/
├── test_extract.py    # Crawling and HTTP client
├── test_transform.py  # Data cleaning and validation
└── test_load.py       # Database writes
```

---

## test_extract.py

Tests `WikipediaClient.fetch_pages_batch` and the `crawl` BFS loop.

HTTP calls are short-circuited by patching `client._session.get` with a `FakeResponse` that returns hardcoded JSON. Crawl tests go further — they patch `WikipediaClient.fetch_pages_batch` itself with `_fake_fetch`, a lookup table that returns pre-built `RawPage` objects by title, so no HTTP layer is involved at all.

| Test | What it checks |
|---|---|
| `test_fetch_page_success` | A valid API response is parsed into a `RawPage` with the correct title, pageid, links, categories, summary, and depth. |
| `test_fetch_page_missing` | A response with `"missing": true` returns an empty list. |
| `test_fetch_page_request_exception` | A `ConnectionError` from the session returns an empty list instead of raising. |
| `test_crawl_respects_max_depth_and_circular_links` | BFS stops at `max_depth=1` — a page only reachable at depth 2 is never fetched. Circular links (Seed → Seed) do not cause an infinite loop. |
| `test_crawl_caps_links_per_page` | `MAX_LINKS_PER_DEPTH` is patched to `{0: 2}`, so only 2 of the 4 links from the seed page are followed. |
| `test_crawl_dedupes_redirects_by_pageid` | Two titles that resolve to the same `pageid` (a redirect) produce only one result page at depth 1. |

---

## test_transform.py

Tests `clean_pages` and the internal `_parse_last_modified` helper.

All inputs are built with a `_raw()` helper that constructs a `RawPage` with sensible defaults, overriding only the fields relevant to each test.

| Test | What it checks |
|---|---|
| `test_clean_pages_normalizes_categories_links_and_dates` | Duplicate categories and links are removed and the lists are sorted alphabetically. `last_modified` is parsed from the raw `Z`-suffix ISO string into a timezone-aware `datetime`. |
| `test_clean_pages_skips_missing_extract_or_summary` | Pages with `None` extract or a blank/whitespace-only summary are dropped — `clean_pages` returns an empty list. |
| `test_clean_pages_dedupes_by_pageid` | When two `RawPage` objects share the same `pageid`, only the first one is kept. |
| `test_parse_last_modified_valid` | A valid Wikipedia `touched` timestamp (`2026-06-12T10:22:21Z`) is parsed correctly to a UTC `datetime`. |
| `test_parse_last_modified_invalid_or_missing` | A malformed string and `None` both return `None` without raising. |

---

## test_load.py

Tests `load_staging` and `load_production` against a real SQLite database.

The `engine` fixture creates an **in-memory SQLite database** (`sqlite://`) and runs `init_db` to create tables before each test. Nothing is written to disk. `_raw()` and `_clean()` helpers build minimal `RawPage` and `CleanPage` objects.

| Test | What it checks |
|---|---|
| `test_load_staging_upserts_existing_rows` | Calling `load_staging` twice with the same `pageid` results in one row, with the second call's data winning (`INSERT OR REPLACE`). |
| `test_load_production_inserts_pages_and_links` | Two clean pages with a total of 3 links produce 2 rows in `pages` and 3 rows in `page_links`. |
| `test_load_production_upserts_pages_and_dedupes_links` | Re-running `load_production` updates the page row and adds new links without duplicating existing ones (`INSERT OR IGNORE` on links). |
| `test_load_production_rolls_back_on_error` | If the link insert step raises, the entire transaction rolls back — the page row reverts to its previous state and the link count is unchanged. |
