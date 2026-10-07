"""The common shape every source converts its listings into."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Link:
    url: str
    label: str  # e.g. "Company site", "LinkedIn", "Adzuna"
    direct: bool = False  # True when the source says this is the employer's own page


@dataclass
class Job:
    source: str  # which API/feed produced it, e.g. "JSearch"
    source_id: str  # the source's own id for the listing
    title: str
    company: str
    location: str
    remote: bool
    posted_at: datetime | None  # timezone-aware UTC
    links: list[Link] = field(default_factory=list)
    salary: str = ""
    description: str = ""

    # Filled in by the dedupe step.
    also_on: list[str] = field(default_factory=list)  # other sources' names
    merged_uids: list[str] = field(default_factory=list)  # every merged listing's uid

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"
