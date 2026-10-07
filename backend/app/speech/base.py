"""The vendor-neutral speech-to-text contract.

Nothing in this module knows about Deepgram, OpenAI or any other vendor. The
voice relay (app/routers/voice.py) talks only to `LiveSpeechProvider`, so a new
vendor is one new class and one line in app/speech/registry.py.

Rules every provider keeps:

* Keys come from `Settings` and stay on the server. Nothing here is ever sent to
  the browser.
* A vendor's own error text is logged and never raised: it can name the account.
* Course-code normalization is NOT a provider concern. Providers return what they
  heard; `app.speech.course_codes` rewrites it, identically for every provider.

The event shapes are also the seam for a future live voice advisor
(mic -> streaming STT -> advisor -> TTS): any component that yields these events
can feed the same relay.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal

from app.config import Settings

SpeechErrorKind = Literal["unavailable", "unreadable"]


class SpeechError(RuntimeError):
    """Transcription failed.

    `unavailable`: the service cannot be used right now (no key, rejected key, no
    credit, rate limited, down, unreachable, or a reply we could not parse).
    `unreadable`: the service is fine but could not decode this recording.
    """

    def __init__(self, kind: SpeechErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind: SpeechErrorKind = kind


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float


@dataclass(frozen=True)
class Partial:
    """The provider's current guess at the phrase being spoken. Replaced, not kept."""

    text: str


@dataclass(frozen=True)
class Final:
    """A finished phrase. Kept."""

    text: str
    confidence: float


@dataclass(frozen=True)
class UtteranceEnd:
    """The provider heard no words for a while: the speaker has paused."""


LiveEvent = Partial | Final | UtteranceEnd


class LiveSpeechProvider(ABC):
    """One streaming transcription session with one vendor.

    Every failure to connect is `SpeechError("unavailable")`; a connection that
    drops later simply ends `events()`.
    """

    #: Stable id used in SPEECH_PROVIDER and shown in status responses.
    name: str = ""

    @property
    @abstractmethod
    def model(self) -> str:
        """The model this session uses (for status, logs and benchmarks)."""

    @classmethod
    @abstractmethod
    def availability(cls, settings: Settings) -> tuple[bool, str]:
        """(usable, reason). Cheap and offline: it checks configuration, not the network."""

    @abstractmethod
    async def connect(
        self, keyterms: Sequence[str] = (), *, settings: Settings | None = None
    ) -> None: ...

    @abstractmethod
    async def send_audio(self, chunk: bytes) -> None: ...

    @abstractmethod
    async def finish(self) -> None:
        """Ask the provider to flush what it has and end the stream."""

    @abstractmethod
    def events(self) -> AsyncIterator[LiveEvent]: ...

    @abstractmethod
    async def close(self) -> None: ...
