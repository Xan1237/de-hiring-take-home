from datetime import datetime

from pydantic import BaseModel


class RawPage(BaseModel):
    title: str
    pageid: int
    url: str
    extract: str | None = None
    summary: str | None = None
    categories: list[str] = []
    links: list[str] = []
    last_modified: str | None = None
    depth: int
    fetched_at: datetime


class CleanPage(BaseModel):
    title: str
    pageid: int
    url: str
    extract: str
    summary: str
    categories: list[str] = []
    links: list[str] = []
    last_modified: datetime | None = None
    depth: int
    fetched_at: datetime
