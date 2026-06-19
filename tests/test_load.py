from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.load import Page, PageLink, StagingPage, init_db, load_production, load_staging
from src.models import CleanPage, RawPage


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    init_db(eng)
    return eng


def _raw(pageid, title, depth=0, links=None):
    return RawPage(
        title=title,
        pageid=pageid,
        url=f"https://en.wikipedia.org/wiki/{title}",
        extract="text",
        summary="text",
        categories=["Cat"],
        links=links or [],
        last_modified="2026-01-01T00:00:00Z",
        depth=depth,
        fetched_at=datetime.now(timezone.utc),
    )


def _clean(pageid, title, depth=0, links=None):
    return CleanPage(
        title=title,
        pageid=pageid,
        url=f"https://en.wikipedia.org/wiki/{title}",
        extract="text",
        summary="text",
        categories=["Cat"],
        links=links or [],
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
        depth=depth,
        fetched_at=datetime.now(timezone.utc),
    )


def test_load_staging_upserts_existing_rows(engine):
    load_staging([_raw(1, "Toronto")], engine=engine)

    with Session(engine) as session:
        rows = session.query(StagingPage).all()
        assert len(rows) == 1
        assert rows[0].title == "Toronto"

    load_staging([_raw(1, "Toronto Updated")], engine=engine)

    with Session(engine) as session:
        rows = session.query(StagingPage).all()
        assert len(rows) == 1
        assert rows[0].title == "Toronto Updated"


def test_load_production_inserts_pages_and_links(engine):
    pages = [_clean(1, "Toronto", links=["A", "B"]), _clean(2, "A", links=["Toronto"])]

    load_production(pages, engine=engine)

    with Session(engine) as session:
        assert session.query(Page).count() == 2
        assert session.query(PageLink).count() == 3


def test_load_production_upserts_pages_and_dedupes_links(engine):
    load_production([_clean(1, "Toronto", links=["A"])], engine=engine)
    load_production([_clean(1, "Toronto Updated", links=["A", "B"])], engine=engine)

    with Session(engine) as session:
        page = session.get(Page, 1)
        assert page.title == "Toronto Updated"

        links = {pl.target_title for pl in session.query(PageLink).all()}
        assert links == {"A", "B"}


def test_load_production_rolls_back_on_error(engine, monkeypatch):
    load_production([_clean(1, "Toronto", links=["A"])], engine=engine)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.load._bulk_insert_ignore", boom)

    with pytest.raises(RuntimeError):
        load_production([_clean(1, "Toronto Updated", links=["A", "B"])], engine=engine)

    with Session(engine) as session:
        page = session.get(Page, 1)
        assert page.title == "Toronto"
        assert session.query(PageLink).count() == 1
