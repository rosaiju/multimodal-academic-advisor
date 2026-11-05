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


#: Brief (live voice) mode: list items spoken before "the rest is on screen".
BRIEF_ITEMS = 3
_PARTIAL = re.compile(r"\b(partial|incomplete)\b", re.IGNORECASE)
BRIEF_CAVEAT = "The catalog I use is still partial, so check with your advisor."
#: "You are NOT eligible yet; still missing COSC220" - the verdict itself.
_NOT_ELIGIBLE = re.compile(r"\bnot eligible yet\b", re.IGNORECASE)
#: The "Not yet eligible" section of a recommendation list.
_NOT_YET_SECTION = re.compile(r"\bnot yet eligible\b", re.IGNORECASE)
BRIEF_BLOCKED = (
    "Some courses are not yet eligible for you; what each one is waiting on is on your screen."
)
_IRREGULAR = re.compile(r"\boffered irregularly\b", re.IGNORECASE)
BRIEF_IRREGULAR = (
    "The catalog lists a course here as offered irregularly, so confirm it runs "
    "before relying on it."
)
#: Requirement ids like major_required_courses: meaningless when heard.
_INTERNAL_ID = re.compile(r":?\s*\b[a-z]+(?:_[a-z]+)+\b")


def _brief(text: str) -> str:
    """The engine's full answer cut down for speech: the headline paragraph, at
    most BRIEF_ITEMS list items, and the partial-catalog caveat said once.
    Deterministic - it only removes text, never adds a fact."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paragraphs:
        return text
    lines = paragraphs[0].splitlines()
    first = next((i for i, ln in enumerate(lines) if _BULLET.match(ln)), len(lines))
    head = lines[:first]  # indented "Options: ..." lines under bullets are dropped
    items = [ln for ln in lines if _BULLET.match(ln)]
    if not items and len(paragraphs) > 1:
        items = [ln for ln in paragraphs[1].splitlines() if _BULLET.match(ln)]
    # "- COSC220 Data Structures (4 cr) - required by ..." -> keep the part before " - ".
    # A " - " before a lowercase word starts detail; "General Education - Critical" stays.
    kept = [re.split(r" - (?=[a-z])", ln, maxsplit=1)[0] for ln in items[:BRIEF_ITEMS]]
    # The verdict on a course is the answer: say it right after the headline.
    verdicts = [
        _BULLET.sub("", ln).strip()
        for ln in text.splitlines()
        if _NOT_ELIGIBLE.search(ln) and ln not in head
    ]
    out = "\n".join([*head, *verdicts, *(k for k in kept if not _NOT_ELIGIBLE.search(k))])
    if len(items) > BRIEF_ITEMS:
        out += f"\n\nAnd {len(items) - BRIEF_ITEMS} more. The full list is on your screen."
    elif len(paragraphs) > 1:
        out += "\n\nThe details are on your screen."
    # Warnings that change a registration decision are always spoken, even when
    # the line they sit on was cut. Each is the engine's own words or a fixed phrase.
    if _NOT_YET_SECTION.search(text) and not _NOT_YET_SECTION.search(out):
        out += "\n\n" + BRIEF_BLOCKED
    if _IRREGULAR.search(text) and not _IRREGULAR.search(out):
        out += "\n\n" + BRIEF_IRREGULAR
    if _PARTIAL.search(text) and not _PARTIAL.search(out):
        out += "\n\n" + BRIEF_CAVEAT
    return out


def to_speech(text: str, links: Sequence[Link] = (), *, brief: bool = False) -> str:
    if brief:
        text = _brief(text)
    text = _MD_LINK.sub(lambda m: m[1], text)  # [name](url) -> name
    for link in links:
        text = text.replace(link.url, link.name)
    text = _URL.sub("", text)
    text = _INTERNAL_ID.sub("", text)
    text = re.sub(r"\((\d+) cr\)", r"(\1 credits)", text)
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


def brief_text(text: str) -> str:
    """What the live voice actually says for an engine answer, before speech
    formatting. Stored as conversation history so a follow-up question is read
    against what the student heard, not the full written answer."""
    return _brief(text)
