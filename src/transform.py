import logging
from datetime import datetime

from src.models import CleanPage, RawPage

logger = logging.getLogger(__name__)


# We convert wikipedias last modified time to a format better for python
# Ex: "2024-01-15T10:32:00Z" -> "2024-01-15T10:32:00+00:00"
def _parse_last_modified(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Could not parse last_modified value: %r", raw)
        return None


def clean_pages(raw_pages: list[RawPage]) -> list[CleanPage]:
    cleaned: list[CleanPage] = []
    seen_ids: set[int] = set()

    for raw in raw_pages:

        # It should already be deduped but just in case we do it again
        if raw.pageid in seen_ids:
            continue
        seen_ids.add(raw.pageid)

        # We strip blank space from every page
        extract = (raw.extract or "").strip()
        summary = (raw.summary or "").strip()

        # Skip if one of the features is missing
        if not extract or not summary:
            logger.warning("Skipping page %r: missing extract/summary", raw.title)
            continue
        
        # We try to create CleanPage object if there are any issues we skip the record and log an issue
        try:
            cleaned.append(
                CleanPage(
                    title=raw.title.strip(),
                    pageid=raw.pageid,
                    url=raw.url,
                    extract=extract,
                    summary=summary,
                    categories=sorted(set(raw.categories)),
                    links=sorted(set(raw.links)),
                    last_modified=_parse_last_modified(raw.last_modified),
                    depth=raw.depth,
                    fetched_at=raw.fetched_at,
                )
            )
        except ValueError:
            logger.warning("Skipping invalid page %r", raw.title, exc_info=True)
            continue
    
    # Log some basic info and pass the pages to be loaded to the database
    logger.info("Transformed %d/%d pages", len(cleaned), len(raw_pages))
    return cleaned
