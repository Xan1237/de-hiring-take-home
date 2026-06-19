# Setup

**Requirements:** Python 3.13+, [`uv`](https://docs.astral.sh/uv/)

```bash
uv sync
uv run python -m src.pipeline
```

Output is written to `data/pipeline.db` (SQLite) and `data/pipeline.log`.

To start fresh, delete the database:

```bash
rm data/pipeline.db
```

---

## Running Tests

The test suite uses [pytest](https://docs.pytest.org/) and covers extraction, transformation, and loading.

**Run all tests:**

```bash
uv run pytest
```

**Run with verbose output** (shows each test name and pass/fail):

```bash
uv run pytest -v
```

**Run a single test file:**

```bash
uv run pytest tests/test_extract.py
uv run pytest tests/test_transform.py
uv run pytest tests/test_load.py
```

**Run a single test by name:**

```bash
uv run pytest -k "test_crawl_respects_max_depth"
```

Tests use in-memory SQLite and monkeypatched HTTP calls — no network access or database file is required.
