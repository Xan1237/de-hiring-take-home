import logging

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from src.config import DB_URL
from src.models import CleanPage, RawPage

logger = logging.getLogger(__name__)

# All tables inherit from this class
class Base(DeclarativeBase):
    pass


class StagingPage(Base):
    __tablename__ = "staging_pages"

    pageid: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    extract: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    links: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    last_modified: Mapped[str | None] = mapped_column(String, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)


# We don't store links because we seperate them into a diffrent table
class Page(Base):
    __tablename__ = "pages"

    pageid: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    extract: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    last_modified: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)


# Extra table we have in prod to seperate them from the base page table
class PageLink(Base):
    __tablename__ = "page_links"
    __table_args__ = (UniqueConstraint("source_pageid", "target_title", name="uq_page_links_source_target"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_pageid: Mapped[int] = mapped_column(Integer, nullable=False)
    target_title: Mapped[str] = mapped_column(String, nullable=False)


# This class manages our db connection and queries
def get_engine():
    return create_engine(DB_URL)


# Initialize a database
def init_db(engine) -> None:
    Base.metadata.create_all(engine)

# The max rows * columns we can in/upsert to the database at one time
_SQLITE_MAX_PARAMS = 32_766


# We chunk the data based on the max upload params sated above 
def _chunk(rows: list[dict], cols_per_row: int) -> list[list[dict]]:
    # We use max to ensure at least 1 row is always the response
    size = max(1, _SQLITE_MAX_PARAMS // cols_per_row)
    return [rows[i : i + size] for i in range(0, len(rows), size)]


# If their are any 409 we upsert the data
def _bulk_upsert(session: Session, model, rows: list[dict], conflict_cols: list[str]) -> None:
    """Insert rows, updating any existing row that matches on conflict_cols."""
    if not rows:
        return

    update_cols = [c for c in rows[0] if c not in conflict_cols]
    for batch in _chunk(rows, len(rows[0])):
        # Create and execute the query for a batch in the chunk
        stmt = sqlite_insert(model).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict_cols,
            set_={col: getattr(stmt.excluded, col) for col in update_cols},
        )
        session.execute(stmt)


# Compared to bulk insert on any 409 we continue we don't upsert the value
def _bulk_insert_ignore(session: Session, model, rows: list[dict], conflict_cols: list[str]) -> None:
    """Insert rows, silently skipping any that collide on conflict_cols."""
    if not rows:
        return

    for batch in _chunk(rows, len(rows[0])):
        stmt = sqlite_insert(model).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        session.execute(stmt)

# we load the data in the staging database
def load_staging(raw_pages: list[RawPage], engine=None) -> None:
    engine = engine or get_engine()
    init_db(engine)

    rows = [
        {
            "pageid": page.pageid,
            "title": page.title,
            "url": page.url,
            "extract": page.extract,
            "summary": page.summary,
            "categories": page.categories,
            "links": page.links,
            "last_modified": page.last_modified,
            "depth": page.depth,
            "fetched_at": page.fetched_at,
        }
        for page in raw_pages
    ]
    # Using with keyword does transation control and auto closes the connection after
    with Session(engine) as session:
        try:
            with session.begin():
                _bulk_upsert(session, StagingPage, rows, conflict_cols=["pageid"])
        except Exception:
            logger.exception("Failed to load staging data; transaction rolled back")
            raise

    logger.info("Upserted %d page(s) into staging_pages", len(rows))


def load_production(clean_pages: list[CleanPage], engine=None) -> None:
    engine = engine or get_engine()
    init_db(engine)

    page_rows = [
        {
            "pageid": page.pageid,
            "title": page.title,
            "url": page.url,
            "extract": page.extract,
            "summary": page.summary,
            "categories": page.categories,
            "last_modified": page.last_modified,
            "depth": page.depth,
            "fetched_at": page.fetched_at,
        }
        for page in clean_pages
    ]
    link_rows = [
        {"source_pageid": page.pageid, "target_title": link}
        for page in clean_pages
        for link in page.links
    ]

    # Using with keyword does transation control and auto closes the connection after
    with Session(engine) as session:
        try:
            # Both inserts are in one transaction if either fails, both roll back together
            # so pages and their links are never out of sync
            with session.begin():
                # Upsert clean pages update existing rows if pageid already exists
                _bulk_upsert(session, Page, page_rows, conflict_cols=["pageid"])
                # Insert links silently skip any source->target pair already in the table
                # We skip because links cannot be updated but page content can be updates if we re run
                _bulk_insert_ignore(
                    session, PageLink, link_rows, conflict_cols=["source_pageid", "target_title"]
                )
        except Exception:
            logger.exception(
                "Failed to load production data; transaction rolled back, "
                "pages/page_links left unchanged"
            )
            raise

    logger.info(
        "Upserted %d page(s) and %d link(s) into production tables",
        len(page_rows),
        len(link_rows),
    )
