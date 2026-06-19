# ETL Pipeline — Wikipedia Crawl

## Configuration

All tuning parameters are in [src/config.py](src/config.py):

| Parameter | Default | Description |
|---|---|---|
| `SEED_TITLE` | `"Toronto"` | Starting Wikipedia page |
| `MAX_DEPTH` | `2` | BFS depth levels to crawl |
| `MAX_LINKS_PER_DEPTH` | `{0: 100, 1: 50}` | Max links to queue per page at each depth |
| `BATCH_SIZE` | `20` | Pages per API request (hard cap — see Wiki API section) |
| `MAX_WORKERS` | `8` | Concurrent threads |
| `REQUESTS_PER_SECOND` | `15` | Self-imposed rate limit |
| `MAX_RETRIES` | `3` | Retry attempts on transient HTTP errors |
| `BACKOFF_FACTOR` | `1.0` | Exponential backoff multiplier between retries |
| `REQUEST_TIMEOUT` | `30` | Per-request timeout in seconds |

---

## Models

Defined in [src/models.py](src/models.py) using Pydantic for validation and type enforcement.

### `RawPage`

Produced by the extraction stage. Fields directly mirror what the MediaWiki API returns — `extract` and `summary` are nullable because pages that exist but have no intro text still need to land in staging.

| Field | Type | Notes |
|---|---|---|
| `title` | `str` | Page title |
| `pageid` | `int` | Wikipedia's stable numeric page ID |
| `url` | `str` | Full canonical URL |
| `extract` | `str \| None` | Intro paragraph text (nullable) |
| `summary` | `str \| None` | First paragraph of extract (nullable) |
| `categories` | `list[str]` | Raw category strings from API |
| `links` | `list[str]` | Linked page titles (used for BFS) |
| `last_modified` | `str \| None` | Raw ISO 8601 string from `touched` field |
| `depth` | `int` | BFS level this page was fetched at |
| `fetched_at` | `datetime` | UTC timestamp set at fetch time |

### `CleanPage`

Produced by the transform stage. `extract` and `summary` are non-nullable — a page that fails this requirement is dropped. `last_modified` is parsed from a raw string into a proper `datetime`. `links` is still present here for populating `page_links` before being dropped from the production `pages` table.

| Field | Type | Notes |
|---|---|---|
| `title` | `str` | |
| `pageid` | `int` | |
| `url` | `str` | |
| `extract` | `str` | Non-nullable; pages missing this are dropped |
| `summary` | `str` | Non-nullable; pages missing this are dropped |
| `categories` | `list[str]` | Deduplicated and sorted |
| `links` | `list[str]` | Used to write `page_links`, not stored in `pages` |
| `last_modified` | `datetime \| None` | Parsed UTC datetime |
| `depth` | `int` | |
| `fetched_at` | `datetime` | |

---

## Data Schema

### `staging_pages`

Raw data exactly as fetched. All pages land here regardless of quality, including those with null extract or summary.

| Column | Type | Description |
|---|---|---|
| `pageid` | INTEGER (PK) | Wikipedia page ID |
| `title` | TEXT | Page title |
| `url` | TEXT | Full Wikipedia URL |
| `extract` | TEXT | Intro paragraph (nullable) |
| `summary` | TEXT | First sentence of intro (nullable) |
| `categories` | JSON | List of category strings |
| `links` | JSON | List of linked page titles |
| `last_modified` | TEXT | Raw ISO timestamp string |
| `depth` | INTEGER | BFS depth level (0, 1, or 2) |
| `fetched_at` | DATETIME | UTC timestamp of fetch |

### `pages`

Clean production data. Only pages that passed transform with non-empty extract and summary. Links are not stored here — they live in `page_links`.

| Column | Type | Description |
|---|---|---|
| `pageid` | INTEGER (PK) | Wikipedia page ID |
| `title` | TEXT | Page title |
| `url` | TEXT | Full Wikipedia URL |
| `extract` | TEXT | Intro paragraph (non-nullable) |
| `summary` | TEXT | First sentence of intro (non-nullable) |
| `categories` | JSON | Deduplicated, sorted list of categories |
| `last_modified` | DATETIME | Parsed UTC datetime |
| `depth` | INTEGER | BFS depth level (0, 1, or 2) |
| `fetched_at` | DATETIME | UTC timestamp of fetch |

### `page_links`

One row per directed link relationship. Separated from `pages` so the link graph can be queried independently.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER (PK) | Auto-increment ID |
| `source_pageid` | INTEGER | Pageid of the page containing the link |
| `target_title` | TEXT | Title of the linked page |

---

## Wiki API

The pipeline uses the [MediaWiki Action API](https://www.mediawiki.org/wiki/API:Main_page) (`/w/api.php`) rather than scraping HTML. This gives structured JSON — titles, extracts, categories, links, and canonical URLs — without parsing markup.

### How a batch request works

Each request fetches up to 20 pages at once by pipe-separating titles in a single `titles` parameter. The properties requested per call are:

| Property | API param | What it returns |
|---|---|---|
| Page text | `prop=extracts` | Intro paragraph via `exintro=1`, plain text via `explaintext=1`, capped at 500 chars via `exchars=500` |
| Categories | `prop=categories` | Category membership (`cllimit=max` → up to 500) |
| Links | `prop=links` | Wikilinks to other articles (`pllimit=max`, namespace 0 only) |
| Page info | `prop=info` | Canonical URL and `touched` timestamp via `inprop=url` |

`redirects=1` is included so redirect titles (e.g. "Toronto, Ontario") are transparently resolved to their target page, avoiding duplicate pageids.

### The `exlimit` cap

`exlimit=max` caps extract responses at **20 pages per request**. This is a hard Wikipedia limit — it is not configurable. `BATCH_SIZE = 20` exists solely because of this cap. Sending more than 20 titles in one request would silently truncate extracts for the overflow pages.

### The `plcontinue` continuation header

Wikipedia caps link responses at **500 links per page per request** (`pllimit=max`). Pages with more than 500 outbound links (common for hub articles like Toronto) trigger a `continue` object in the response:

```json
{
  "continue": {
    "plcontinue": "12345|0|Next_Title",
    "continue": "||"
  }
}
```

When `plcontinue` is present, the crawler sends a follow-up request with `plcontinue` as an additional parameter. This continues until the response contains no `continue` key. Each continuation request counts against the rate limiter the same as a primary request. The crawler merges the additional links into the existing page entry in `page_info` using `setdefault` + `extend`.

---

## Crawl: Breadth-First Search

The crawl in [src/extract.py](src/extract.py) uses BFS to guarantee that every page is fetched at its shallowest possible depth before moving deeper.

### Structure

```
depth 0  →  Toronto  (1 page, the seed)
depth 1  →  up to 100 links from Toronto
depth 2  →  up to 50 links per depth-1 page
```

Each BFS level is one iteration of the outer `for depth in range(max_depth + 1)` loop. At each level:

1. All titles in `current_level` are split into batches of `BATCH_SIZE` (20).
2. Batches are dispatched concurrently to a `ThreadPoolExecutor` with `MAX_WORKERS` threads.
3. As futures complete, each result page is checked against `seen_pageids` (dedup by numeric ID) before being added to `results`.
4. Links from each page are queued into `next_level`, subject to the `MAX_LINKS_PER_DEPTH` cap.
5. At end of level, `current_level = next_level` and the loop advances.

### Two-phase deduplication

- **`visited` (by title)** — checked before queuing a link into `next_level`. Prevents the same title from being enqueued multiple times across threads.
- **`seen_pageids` (by pageid)** — checked after a page is fetched. Catches the case where two different titles resolve to the same page via redirect.

Both sets are needed: `visited` cuts the work before it happens; `seen_pageids` catches what slips through.

### Link cap

`MAX_LINKS_PER_DEPTH` bounds how many new links are queued from each individual page at a given depth. Without a cap, a single hub page with thousands of links would dominate the queue. The cap is applied in title-encounter order — the first `cap` unvisited titles win.

---

## Architecture Trade-offs

### Batched API requests vs. one request per page

**Chosen:** batch 20 pages per request using pipe-separated titles.

Batching reduces HTTP round trips by up to 20×. The cost is that a single failed request drops up to 20 pages instead of 1. The pipeline handles this by logging the failure and returning an empty list for that batch — those pages simply don't appear in staging. At `BATCH_SIZE = 20`, the batch size matches the `exlimit=max` hard cap, so there is no reason to go lower.

### ThreadPoolExecutor vs. async (asyncio)

**Chosen:** `ThreadPoolExecutor` with `MAX_WORKERS = 8`.

Threading is simpler to reason about when mixing I/O with a shared rate limiter and a mutable `seen_pageids` set. The GIL is not a bottleneck here because the work is network-bound. `asyncio` would reduce thread overhead at very high concurrency, but the Wikipedia rate limit (`REQUESTS_PER_SECOND = 15`) means throughput is capped by the limiter, not by thread count — additional workers beyond what's needed to keep the pipeline full do not help.

### Staging → production promotion vs. transform-in-place

**Chosen:** load raw data to `staging_pages` first, then promote clean records to `pages`.

All pages land in staging regardless of quality. This preserves the raw signal — you can audit what was fetched vs. what was rejected without re-running the crawl. The downside is the database is larger and the load step runs twice. The alternative (transform first, then load once) is more efficient but loses the raw record if transform logic changes.

### SQLite vs. PostgreSQL

**Chosen:** SQLite with SQLAlchemy ORM.

SQLite requires zero infrastructure, which is appropriate for a local pipeline. The ORM abstracts the dialect so swapping to Postgres requires only changing `DB_URL`. The main SQLite-specific constraint that appears in the code is the bind-parameter limit (see Throughput and Rate Limits).

### Upsert vs. insert-or-skip for `page_links`

**Chosen:** `INSERT OR IGNORE` (via `on_conflict_do_nothing`) for links, `INSERT OR REPLACE` (via `on_conflict_do_update`) for pages.

Page content (extract, summary, categories) can improve across pipeline re-runs as Wikipedia is updated, so upsert makes sense for `staging_pages` and `pages`. Links, however, have no updatable payload — a `(source_pageid, target_title)` pair either exists or it doesn't. `INSERT OR IGNORE` is cheaper because it skips the write entirely on conflict rather than re-writing the same values.

---

## Throughput and Rate Limits

Multiple independent constraints bound the pipeline's throughput. They stack — the binding constraint at any moment is whichever one is most restrictive.

### Self-imposed rate limit

`REQUESTS_PER_SECOND = 15` is enforced by a thread-safe `RateLimiter` in [src/extract.py](src/extract.py). Before each HTTP call (primary or continuation), a thread acquires a lock, measures elapsed time since the last call, and sleeps the remainder of the minimum interval (`1 / 15 ≈ 67ms`). This is a global limiter shared across all threads — 8 concurrent workers do not each get 15 RPS; together they share a ceiling of 15 RPS total.

### Wikipedia API limits

| Constraint | Value | Source |
|---|---|---|
| Extracts per request (`exlimit=max`) | 20 pages | Hard MediaWiki cap, not configurable |
| Links per page per response (`pllimit=max`) | 500 links | Triggers `plcontinue` pagination |
| Categories per response (`cllimit=max`) | 500 categories | Single response, no pagination implemented |
| Anonymous request rate (Wikipedia guideline) | ~1 req/s recommended | Pipeline runs at 15 RPS with a `User-Agent` header; this is within bot policy for identified clients |

The `User-Agent` header (`de-hiring-take-home-etl/0.1`) identifies the client per Wikipedia's [bot policy](https://en.wikipedia.org/wiki/Wikipedia:Bot_policy). Unidentified requests are more likely to be throttled.

### HTTP retry and backoff

On `429`, `500`, `502`, `503`, or `504` responses, `urllib3.Retry` automatically retries up to `MAX_RETRIES = 3` times with exponential backoff (`BACKOFF_FACTOR = 1.0` → waits of 1s, 2s, 4s). This is handled at the transport layer by the `HTTPAdapter` mounted on the session — the rate limiter and application code do not see the retried attempts.

### Database upsert limits

SQLite caps bind parameters at **32,766 per statement**. Bulk upserts are chunked in `_chunk()` ([src/load.py:78](src/load.py#L78)) based on `rows × columns_per_row`. For `staging_pages` (10 columns), the max chunk size is `32766 // 10 = 3276` rows. For `pages` (9 columns), it is `32766 // 9 = 3640` rows. For `page_links` (2 columns), it is `32766 // 2 = 16383` rows. Exceeding this limit raises a `sqlite3.OperationalError` — chunking ensures this never happens regardless of result set size.

### Practical throughput

With default config (`MAX_WORKERS=8`, `REQUESTS_PER_SECOND=15`, `BATCH_SIZE=20`):

- Throughput ceiling from rate limit: `15 requests/s × 20 pages/request = 300 pages/s` theoretical max.
- Actual throughput is network-latency-bound. At ~0.3–0.6s average response time, 8 threads saturate the 15 RPS limiter comfortably. Real-world throughput observed: **~150–250 pages/minute** depending on network conditions and how many `plcontinue` continuations a batch triggers.
- The total page count for a default run (depth 2, caps `{0:100, 1:50}`) is bounded at roughly `1 + 100 + (100 × 50) = 5,101` pages, though many links overlap and deduplication reduces this in practice.
