from datetime import datetime, timezone

from src.models import RawPage
from src.transform import _parse_last_modified, clean_pages


def _raw(
    pageid=1,
    title="Toronto",
    extract="Some text.",
    summary="Some text.",
    categories=None,
    links=None,
    last_modified="2026-01-01T00:00:00Z",
    depth=0,
):
    return RawPage(
        title=title,
        pageid=pageid,
        url=f"https://en.wikipedia.org/wiki/{title}",
        extract=extract,
        summary=summary,
        categories=categories or [],
        links=links or [],
        last_modified=last_modified,
        depth=depth,
        fetched_at=datetime.now(timezone.utc),
    )


def test_clean_pages_normalizes_categories_links_and_dates():
    raw = _raw(categories=["B", "A", "A"], links=["Y", "X", "X"])

    cleaned = clean_pages([raw])

    assert len(cleaned) == 1
    page = cleaned[0]
    assert page.categories == ["A", "B"]
    assert page.links == ["X", "Y"]
    assert page.last_modified == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_clean_pages_skips_missing_extract_or_summary():
    raw_missing_extract = _raw(pageid=1, extract=None)
    raw_blank_summary = _raw(pageid=2, summary="   ")

    cleaned = clean_pages([raw_missing_extract, raw_blank_summary])

    assert cleaned == []


def test_clean_pages_dedupes_by_pageid():
    raw1 = _raw(pageid=1, title="A")
    raw2 = _raw(pageid=1, title="A duplicate")

    cleaned = clean_pages([raw1, raw2])

    assert len(cleaned) == 1
    assert cleaned[0].title == "A"


def test_parse_last_modified_valid():
    assert _parse_last_modified("2026-06-12T10:22:21Z") == datetime(
        2026, 6, 12, 10, 22, 21, tzinfo=timezone.utc
    )


def test_parse_last_modified_invalid_or_missing():
    assert _parse_last_modified("not-a-date") is None
    assert _parse_last_modified(None) is None
