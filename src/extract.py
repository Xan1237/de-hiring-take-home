import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import (
    API_URL,
    BACKOFF_FACTOR,
    BATCH_SIZE,
    MAX_DEPTH,
    MAX_LINKS_PER_DEPTH,
    MAX_RETRIES,
    MAX_WORKERS,
    REQUEST_TIMEOUT,
    REQUESTS_PER_SECOND,
    SEED_TITLE,
    USER_AGENT,
)
from src.models import RawPage

logger = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe limiter that enforces a minimum interval between calls."""

    # We need to use a lock to ensure the diffrent threads making calls to wiki api dont try to edit last_call at the same time
    def __init__(self, rate_per_second: float):
        self._min_interval = 1.0 / rate_per_second
        self._lock = threading.Lock()
        self._last_call = 0.0


    def wait(self) -> None:
        # Makes sure no other threads are trying to acess last_call variable
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            sleep_for = self._min_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last_call = time.monotonic()


class WikipediaClient:
    def __init__(self):
        # We create a session here to resuse a single tcp connection instead of making multiple sepreate requests
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

        # retry settings in case of a url failure
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        # We mount the behaviors from the retry and backtrack logic to the session (tcp)
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=MAX_WORKERS)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

        self._rate_limiter = RateLimiter(REQUESTS_PER_SECOND)
        self._request_times: list[float] = []
        self._times_lock = threading.Lock()


    # Function is used to store the time per request
    def _timed_get(self, url: str, **kwargs) -> requests.Response:
        start = time.monotonic()
        response = self._session.get(url, **kwargs)
        elapsed = time.monotonic() - start
        with self._times_lock:
            self._request_times.append(elapsed)
        return response

    # Sats of time per request
    def request_stats(self) -> dict:
        times = self._request_times
        if not times:
            return {"count": 0, "avg_seconds": 0.0, "min_seconds": 0.0, "max_seconds": 0.0}
        return {
            "count": len(times),
            "avg_seconds": sum(times) / len(times),
            "min_seconds": min(times),
            "max_seconds": max(times),
        }


    def fetch_pages_batch(self, titles: list[str], depth: int) -> list[RawPage]:

        # Paramater list for the wiki api 
        params = {
            "action": "query",
            "format": "json",
            "prop": "extracts|categories|links|info",
            "titles": "|".join(titles),
            "explaintext": 1,
            "exsectionformat": "plain",
            "exintro": 1,
            "exchars": 500,
            "exlimit": "max",
            "inprop": "url",
            "cllimit": "max",
            "pllimit": "max",
            "plnamespace": 0,
            "redirects": 1,
        }

        # Ensure we are not exceeding the rate limit
        self._rate_limiter.wait()

        # We hit the wiki api and catch any errors we pipe it through the time storing function as well
        try:
            response = self._timed_get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            logger.exception("Failed to fetch batch of %d page(s)", len(titles))
            return []
        pages_data = data.get("query", {}).get("pages", {})

        page_info: dict[int, dict] = {}

        # Check if the page exisits
        for page in pages_data.values():
            if "missing" in page:
                logger.warning("Page %r does not exist", page.get("title", "unknown"))
                continue
            page_info[page["pageid"]] = page

        # This header indicates the response was too large and we need a subsequent api request to get he rest of the data
        while "plcontinue" in data.get("continue", {}):
            continue_params = {
                "action": "query",
                "format": "json",
                "prop": "links",
                "titles": "|".join(titles),
                "pllimit": "max",
                "plnamespace": 0,
                "plcontinue": data["continue"]["plcontinue"],
            }

            # Check for rate limits
            self._rate_limiter.wait()

            # Send the follow up request with the continue param to get the rest of the data
            try:
                response = self._timed_get(API_URL, params=continue_params, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError):
                logger.warning("Failed to fetch link continuation for batch")
                break
            for page in data.get("query", {}).get("pages", {}).values():
                pageid = page.get("pageid")
                if pageid in page_info:
                    page_info[pageid].setdefault("links", []).extend(page.get("links", []))



        # We store the RawPage object in the results array and return it
        results = []
        for page in page_info.values():
            extract = page.get("extract") or None
            summary = extract.split("\n\n", 1)[0] if extract else None

            results.append(RawPage(
                title=page.get("title", ""),
                pageid=page["pageid"],
                url=page.get("fullurl", ""),
                extract=extract,
                summary=summary,
                categories=[c["title"] for c in page.get("categories", [])],
                links=[link["title"] for link in page.get("links", [])],
                last_modified=page.get("touched"),
                depth=depth,
                fetched_at=datetime.now(timezone.utc),
            ))

        return results


def crawl(seed_title: str = SEED_TITLE, max_depth: int = MAX_DEPTH) -> tuple[list[RawPage], dict]:

    # Initialize object to query wikipedia
    client = WikipediaClient()

    # Keep track of visited to avoid duplicate page visits due to circular objects
    visited: set[str] = {seed_title}
    seen_pageids: set[int] = set()

    # Keep track of at what level we see the page
    current_level: set[str] = {seed_title}
    results: list[RawPage] = []

    # We start the timer
    start_time = time.monotonic()

    

    # BFS loop one iteration per depth level (0, 1, 2)
    for depth in range(max_depth + 1):
        # If there are no titles to fetch at this depth, stop early
        if not current_level:
            break

        # Sets can't be sliced, so convert to list first
        titles = list(current_level)

        # Split the full title list into chunks of BATCH_SIZE (20) so each thread gets a batch
        batches = [titles[i:i + BATCH_SIZE] for i in range(0, len(titles), BATCH_SIZE)]
        logger.info("Fetching depth %d: %d page(s) in %d batch(es)", depth, len(titles), len(batches))

        # Collect titles discovered at this depth to fetch in the next iteration
        next_level: set[str] = set()

        # Open a thread pool the with block waits for all threads to finish before moving on
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

            # Submit all batches to the thread pool at once each runs fetch_pages_batch on a worker thread
            futures = {
                executor.submit(client.fetch_pages_batch, batch, depth): batch
                for batch in batches
            }

            # Process results as each batch finishes, not in submission order
            for future in as_completed(futures):
                try:
                    pages = future.result()
                except Exception:
                    logger.exception("Batch failed unexpectedly: %r", futures[future])
                    continue
                for page in pages:

                    # Skip this page if we added the page with said name to the queue
                    if page.pageid in seen_pageids:
                        continue
                    seen_pageids.add(page.pageid)
                    results.append(page)

                    # Only collect links if there is a next depth to fetch because we dont need them at the last depth
                    if depth < max_depth:
                        # How many unique new links to queue from this page this is to reduce workload
                        cap = MAX_LINKS_PER_DEPTH.get(depth, 0)
                        added = 0
                        for link_title in page.links:
                            # Stop once we've queued enough links from this page
                            if added >= cap:
                                break

                            # The dedupes by page id if they had diffrent page names linking to the same page
                            if link_title not in visited:
                                visited.add(link_title)    
                                next_level.add(link_title)
                                added += 1

        # Replace current level with the links we just collected next loop iteration fetches these
        current_level = next_level

    # Get the post crawl stats and log them
    elapsed_minutes = (time.monotonic() - start_time) / 60
    rate = len(results) / elapsed_minutes if elapsed_minutes > 0 else float("inf")
    req_stats = client.request_stats()
    logger.info(
        "Crawl complete: %d pages fetched in %.2f min (%.1f pages/min)",
        len(results),
        elapsed_minutes,
        rate,
    )

    # Return the page resutls to be processed in transform stage
    return results, {
        "pages_fetched": len(results),
        "elapsed_minutes": elapsed_minutes,
        "pages_per_minute": rate,
        "total_requests": req_stats["count"],
        "avg_request_seconds": req_stats["avg_seconds"],
        "min_request_seconds": req_stats["min_seconds"],
        "max_request_seconds": req_stats["max_seconds"],
    }
