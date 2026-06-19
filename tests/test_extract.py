from datetime import datetime, timezone

import requests

from src import extract


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json


def _query_response(pageid, title, extract_text="Intro.\n\nMore detail.", links=None, categories=None):
    return {
        "query": {
            "pages": {
                str(pageid): {
                    "pageid": pageid,
                    "title": title,
                    "fullurl": f"https://en.wikipedia.org/wiki/{title}",
                    "extract": extract_text,
                    "categories": [{"title": c} for c in (categories or [])],
                    "links": [{"title": link} for link in (links or [])],
                    "touched": "2026-01-01T00:00:00Z",
                }
            }
        }
    }


def _missing_response(title):
    return {"query": {"pages": {"-1": {"ns": 0, "title": title, "missing": True}}}}


def test_fetch_page_success(monkeypatch):
    client = extract.WikipediaClient()
    response = FakeResponse(
        _query_response(1, "Toronto", links=["A", "B"], categories=["Cat1"])
    )
    monkeypatch.setattr(client._session, "get", lambda *a, **k: response)

    pages = client.fetch_pages_batch(["Toronto"], depth=0)

    assert len(pages) == 1
    page = pages[0]
    assert page.title == "Toronto"
    assert page.pageid == 1
    assert page.links == ["A", "B"]
    assert page.categories == ["Cat1"]
    assert page.summary == "Intro."
    assert page.depth == 0


def test_fetch_page_missing(monkeypatch):
    client = extract.WikipediaClient()
    response = FakeResponse(_missing_response("Nonexistent"))
    monkeypatch.setattr(client._session, "get", lambda *a, **k: response)

    pages = client.fetch_pages_batch(["Nonexistent"], depth=0)
    assert pages == []


def test_fetch_page_request_exception(monkeypatch):
    client = extract.WikipediaClient()

    def raise_exc(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(client._session, "get", raise_exc)

    pages = client.fetch_pages_batch(["Toronto"], depth=0)
    assert pages == []


def _raw_page(title, pageid, depth, links=None):
    return extract.RawPage(
        title=title,
        pageid=pageid,
        url=f"https://en.wikipedia.org/wiki/{title}",
        extract="text",
        summary="text",
        categories=[],
        links=links or [],
        last_modified="2026-01-01T00:00:00Z",
        depth=depth,
        fetched_at=datetime.now(timezone.utc),
    )


def _fake_fetch(pages):
    def fetch_pages_batch(self, titles, depth):
        result = []
        for title in titles:
            page = pages.get(title)
            if page:
                result.append(page.model_copy(update={"depth": depth}))
        return result

    return fetch_pages_batch


def test_crawl_respects_max_depth_and_circular_links(monkeypatch):
    pages = {
        "Seed": _raw_page("Seed", 1, 0, links=["A", "B", "Seed"]),
        "A": _raw_page("A", 2, 1, links=["B", "C"]),
        "B": _raw_page("B", 3, 1, links=["A"]),
    }
    monkeypatch.setattr(extract.WikipediaClient, "fetch_pages_batch", _fake_fetch(pages))

    results, _ = extract.crawl(seed_title="Seed", max_depth=1)

    titles = {p.title for p in results}
    assert titles == {"Seed", "A", "B"}  # "C" never queued: depth 1 is max_depth


def test_crawl_caps_links_per_page(monkeypatch):
    monkeypatch.setattr(extract, "MAX_LINKS_PER_DEPTH", {0: 2, 1: 2})

    pages = {
        "Seed": _raw_page("Seed", 1, 0, links=["A", "B", "C", "D"]),
        "A": _raw_page("A", 2, 1),
        "B": _raw_page("B", 3, 1),
        "C": _raw_page("C", 4, 1),
        "D": _raw_page("D", 5, 1),
    }
    monkeypatch.setattr(extract.WikipediaClient, "fetch_pages_batch", _fake_fetch(pages))

    results, _ = extract.crawl(seed_title="Seed", max_depth=1)

    depth1_titles = {p.title for p in results if p.depth == 1}
    assert len(depth1_titles) == 2
    assert depth1_titles <= {"A", "B", "C", "D"}


def test_crawl_dedupes_redirects_by_pageid(monkeypatch):
    pages = {
        "Seed": _raw_page("Seed", 1, 0, links=["Alias", "Real"]),
        "Alias": _raw_page("Real", 2, 1),  # redirects to "Real" -> same pageid
        "Real": _raw_page("Real", 2, 1),
    }
    monkeypatch.setattr(extract.WikipediaClient, "fetch_pages_batch", _fake_fetch(pages))

    results, _ = extract.crawl(seed_title="Seed", max_depth=1)

    depth1 = [p for p in results if p.depth == 1]
    assert len(depth1) == 1
    assert depth1[0].pageid == 2
