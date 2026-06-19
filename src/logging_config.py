import logging
import sys

from src.config import DATA_DIR

# Format for every log line: timestamp [LEVEL] module: message
# e.g. 2026-06-18 12:34:51,949 [INFO] src.extract: Fetching depth 0...
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    if logging.getLogger().handlers:
        return

    # Create the data/ directory if it doesn't exist yet
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Windows console uses cp1252 by default which crashes on non-ASCII page titles
    # Re-open stdout's file descriptor in UTF-8 mode so unicode characters log correctly
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.stream = open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False, buffering=1)

    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        handlers=[
            stream_handler,                                                      
            logging.FileHandler(DATA_DIR / "pipeline.log", encoding="utf-8"),
        ],
    )
