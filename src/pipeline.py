import logging

from src.extract import crawl
from src.load import load_production, load_staging
from src.logging_config import setup_logging
from src.transform import clean_pages

logger = logging.getLogger(__name__)


def main() -> None:

    # Setup folders and logging 
    setup_logging()

    logger.info("Starting crawl")

    # EXTRACT
    raw_pages, crawl_stats = crawl()

    # LOAD
    load_staging(raw_pages)

    #TRANSFORM
    logger.info("Transforming pages")
    clean = clean_pages(raw_pages)
    load_production(clean)
    
    logger.info(
        "Pipeline complete: %d raw page(s), %d production page(s)",
        len(raw_pages),
        len(clean),
    )

    pass_rate = len(clean) / len(raw_pages) * 100 if raw_pages else 0
    logger.info(
        "\n"
        "  ── Pipeline Stats ──────────────────────────\n"
        "  Pages fetched        : %d\n"
        "  Pages produced       : %d  (%.1f%% pass rate)\n"
        "  Elapsed time         : %.2f min\n"
        "  Throughput           : %.1f pages/min\n"
        "  Total API requests   : %d\n"
        "  Avg request time     : %.2fs\n"
        "  Min request time     : %.2fs\n"
        "  Max request time     : %.2fs\n"
        "  ─────────────────────────────────────────────",
        crawl_stats["pages_fetched"],
        len(clean),
        pass_rate,
        crawl_stats["elapsed_minutes"],
        crawl_stats["pages_per_minute"],
        crawl_stats["total_requests"],
        crawl_stats["avg_request_seconds"],
        crawl_stats["min_request_seconds"],
        crawl_stats["max_request_seconds"],
    )


if __name__ == "__main__":
    main()
