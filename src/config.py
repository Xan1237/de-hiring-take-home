from pathlib import Path

# Crawl target
SEED_TITLE = "Toronto"
LANGUAGE = "en"
API_URL = f"https://{LANGUAGE}.wikipedia.org/w/api.php"

# Crawl shape
MAX_DEPTH = 2
MAX_LINKS_PER_DEPTH = {0: 100, 1: 50}
BATCH_SIZE = 20

# HTTP / concurrency
MAX_WORKERS = 8
REQUESTS_PER_SECOND = 15
MAX_RETRIES = 3

# Delay after error and max hang time
BACKOFF_FACTOR = 1.0
REQUEST_TIMEOUT = 30

#Header to be included with wikipedia request
USER_AGENT = "de-hiring-take-home-etl/0.1 (https://github.com/Xan1237/de-hiring-take-home)"

# Storage
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "pipeline.db"
DB_URL = f"sqlite:///{DB_PATH}"
