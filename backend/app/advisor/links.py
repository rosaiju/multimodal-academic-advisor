"""Curated links for answers the advisor cannot give.

Loaded from git-tracked YAML and attached by intent. A model never supplies a
URL: a link a student hears or clicks always came from this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

import yaml

from app.advisor.answers import Intent
from app.config import REPO_ROOT

LINKS_FILE = REPO_ROOT / "data" / "advisor_links.yaml"


@dataclass(frozen=True)
class Link:
    name: str
    url: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "url": self.url}


@cache
def _load() -> tuple[tuple[Link, frozenset[str]], ...]:
    raw = yaml.safe_load(LINKS_FILE.read_text(encoding="utf-8")) or {}
    return tuple(
        (Link(entry["name"], entry["url"]), frozenset(entry.get("intents", [])))
        for entry in raw.get("links", [])
    )


def all_links() -> list[Link]:
    return [link for link, _ in _load()]


def links_for(intent: Intent) -> list[Link]:
    return [link for link, intents in _load() if str(intent) in intents]
