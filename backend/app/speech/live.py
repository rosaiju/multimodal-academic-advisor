"""Deepgram live transcription: what the student sees while they talk.

Two halves. `LiveTranscript` and `parse_live_message` are pure and decide what
the question box shows. `DeepgramLive` is the one connection to Deepgram's
live API.

The box shows finished phrases plus the phrase still being heard, and course
codes are converted on the combined text - so "computer science two forty" plus
a partial "three" becomes "COSC 243" as soon as the last word lands, and only
because COSC 243 is in the catalog.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.speech.course_codes import normalize_course_mentions

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Partial:
    """Deepgram's current guess at the phrase being spoken. Replaced, not kept."""

    text: str


@dataclass(frozen=True)
class Final:
    """A finished phrase. Kept."""

    text: str
    confidence: float


@dataclass(frozen=True)
class UtteranceEnd:
    """Deepgram heard no words for utterance_end_ms: the speaker has paused."""


LiveEvent = Partial | Final | UtteranceEnd


def parse_live_message(raw: str | bytes) -> LiveEvent | None:
    """One Deepgram live message as an event, or None for anything we ignore."""
    try:
        message = json.loads(raw)
        kind = message.get("type")
        if kind == "UtteranceEnd":
            return UtteranceEnd()
        if kind != "Results":
            return None
        best = message["channel"]["alternatives"][0]
        text = str(best.get("transcript") or "")
        if message.get("is_final"):
            return Final(text, float(best.get("confidence") or 0.0)) if text.strip() else None
        return Partial(text)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        log.warning("ignoring unexpected Deepgram live message: %r", raw[:200])
        return None


@dataclass
class LiveTranscript:
    """Finished phrases plus the current partial, as the student should see them."""

    _finals: list[str] = field(default_factory=list)
    _confidences: list[float] = field(default_factory=list)
    _partial: str = ""

    def add_partial(self, text: str) -> None:
        self._partial = text.strip()

    def add_final(self, text: str, confidence: float) -> None:
        text = text.strip()
        if text:
            self._finals.append(text)
            self._confidences.append(confidence)
        self._partial = ""

    def text(self, catalog_codes: Iterable[str]) -> str:
        joined = " ".join([*self._finals, self._partial]).strip()
        return normalize_course_mentions(joined, catalog_codes)

    @property
    def confidence(self) -> float:
        if not self._confidences:
            return 0.0
        return sum(self._confidences) / len(self._confidences)

    @property
    def heard_speech(self) -> bool:
        return bool(self._finals or self._partial)
