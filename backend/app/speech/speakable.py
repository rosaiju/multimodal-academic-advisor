"""Turn a checked advisor answer into text that sounds right when spoken.

Plain code, no model: it runs after `check_rephrasing`, so it must not be a
way for anything to add facts. It only reformats: course codes spelled out,
markdown and bullets flattened, URLs swapped for curated link names.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.advisor.links import Link

_CODE = re.compile(r"\b([A-Z]{3,4})\s?(\d)(\d{2})\b")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+?(?=[.,;:!?)]*(?:\s|$))")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_MARKDOWN = re.compile(r"[*_`#>]+")


def _join(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def to_speech(text: str, links: Sequence[Link] = ()) -> str:
    text = _MD_LINK.sub(lambda m: m[1], text)  # [name](url) -> name
    for link in links:
        text = text.replace(link.url, link.name)
    text = _URL.sub("", text)
    # "COSC 241" -> "C O S C 2 41": Aura then says "C O S C two forty-one".
    text = _CODE.sub(lambda m: f"{' '.join(m[1])} {m[2]} {m[3]}", text)

    sentences: list[str] = []
    bullets: list[str] = []

    def flush() -> None:
        if bullets:
            joined = _join(bullets) + "."
            if sentences and sentences[-1].endswith(":"):
                sentences[-1] = sentences[-1] + " " + joined
            else:
                sentences.append(joined)
            bullets.clear()

    for raw in text.splitlines():
        line = _MARKDOWN.sub("", raw).strip()
        if not line:
            continue
        if _BULLET.match(raw):
            bullets.append(_BULLET.sub("", line).rstrip(".;,"))
            continue
        flush()
        sentences.append(line)
    flush()

    out = " ".join(sentences)
    out = re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()
    for link in links:
        if link.name not in out:
            out += f" You can check {link.name}."
    return out.strip()
