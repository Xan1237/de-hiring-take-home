# CLAUDE.md

## Project

Wikipedia ETL pipeline — BFS crawl of the Toronto Wikipedia page 2 levels deep, transform and validate the data, load into staging and production SQLite tables.

## Commands

- Install deps: `uv sync`
- Run pipeline: `uv run python -m src.pipeline`
- Run tests: `uv run pytest -v`
- Fresh run: `rm data/pipeline.db && uv run python -m src.pipeline`

**Always run `uv run pytest -v` after making any code change and confirm all 15 tests pass before moving on.**

## Architecture

```
src/
├── config.py        # All tuning knobs (depth, rate limit, batch size, workers)
├── models.py        # RawPage (nullable extract/summary) and CleanPage (non-nullable)
├── extract.py       # WikipediaClient + BFS crawl()
├── transform.py     # clean_pages() — filter, dedup, sort, parse dates
├── load.py          # load_staging() and load_production() via SQLAlchemy
├── pipeline.py      # Orchestrates extract → load staging → transform → load production
└── logging_config.py
```

## Key decisions

- **Batched API requests** — 20 pages per request (hard `exlimit=max` cap from MediaWiki)
- **ThreadPoolExecutor** over asyncio — simpler with a shared rate limiter; GIL is not a bottleneck on network-bound work
- **Two-phase dedup** — `visited` (by title, before queuing) + `seen_pageids` (by pageid, after fetch) to catch redirects
- **Staging → production** — all pages land in `staging_pages` regardless of quality; only clean pages promote to `pages`
- **SQLite chunking** — bind param limit is 32,766; chunks are sized as `32_766 // cols_per_row`
- **`INSERT OR REPLACE`** for pages, **`INSERT OR IGNORE`** for links — links have no updatable payload

## Database

Output: `data/pipeline.db` (SQLite)

Tables: `staging_pages`, `pages`, `page_links`

## Known limitations

- **Categories silently truncated** — `cllimit=max` for unauthenticated requests resolves to 50, not 500 (MediaWiki API spec). There is no `clcontinue` pagination loop. Any page with more than 50 categories is stored truncated with no warning logged.

- **`plcontinue` loop has no cap** — the `while "plcontinue"` loop in `fetch_pages_batch` has no iteration limit. A hub page with thousands of links will hold its worker thread in the loop until the API is exhausted, potentially stalling the crawl.

- **`JSONDecodeError` crashes the crawl loop** — the `except requests.RequestException` guard in `fetch_pages_batch` only catches HTTP/network failures. A malformed JSON response raises `json.JSONDecodeError`, propagates uncaught through `future.result()`, and aborts the `as_completed` loop, silently discarding results from all already-completed futures.

- **Empty first paragraph silently drops a page** — `summary` is extracted as `extract.split("\n\n", 1)[0]`. If the extract starts with `\n\n`, summary is `""`. The page is written to staging then silently dropped by `clean_pages()` as if it had missing content.

- **`requests.Session` shared across threads** — one `WikipediaClient` instance shares its `Session` across all worker threads. Safe in practice with no auth state, but not guaranteed thread-safe if Wikipedia sets a session cookie (rate-limit redirect, GeoIP response).

- **`RateLimiter` holds the lock during `sleep()`** — all threads serialize through the rate limiter because the lock is held for the full duration of the sleep. This correctly enforces the global rate limit but means no other thread can even compute its wait time until the sleeping thread finishes.

- **`setup_logging()` leaks a file handle on re-call** — the `FileHandler` is constructed unconditionally before `basicConfig` (which is a no-op if handlers already exist). A second call to `setup_logging()` opens a second handle to `pipeline.log` and leaks it.
