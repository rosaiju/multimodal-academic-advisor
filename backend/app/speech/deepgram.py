"""Deepgram pre-recorded transcription over plain HTTP.

`urllib`, deliberately, as for Gemini and Ollama in app/llm/chat.py: one POST and
one JSON path do not justify an SDK dependency.

Two rules this module keeps:

* The key travels only in the Authorization header. Never in the URL, where it
  would end up in proxy and server logs.
* Deepgram's own error text is logged here and never raised. It can name the
  account or project, and it goes nowhere near a client.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence

from app.config import Settings, get_settings

# Re-exported: these now live in app.speech.base, and existing imports keep working.
from app.speech.base import SpeechError, SpeechErrorKind, Transcript

__all__ = ["SpeechError", "SpeechErrorKind", "Transcript", "transcribe"]

log = logging.getLogger(__name__)


def _listen_url(settings: Settings, keyterms: Sequence[str]) -> str:
    params = [
        ("model", settings.deepgram_model),
        ("smart_format", "true"),
        # Opt out of Deepgram's Model Improvement Program: students' questions
        # about their own records are not training data.
        ("mip_opt_out", "true"),
    ]
    params += [("keyterm", term) for term in keyterms]
    base = settings.deepgram_base_url.rstrip("/")
    return f"{base}/v1/listen?{urllib.parse.urlencode(params)}"


def transcribe(
    audio: bytes,
    mime_type: str,
    keyterms: Sequence[str] = (),
    *,
    settings: Settings | None = None,
) -> Transcript:
    """Transcribe one recording. Raises SpeechError; never returns a partial result."""
    settings = settings or get_settings()
    if not settings.deepgram_api_key:
        raise SpeechError("unavailable", "no Deepgram key is configured")

    request = urllib.request.Request(
        _listen_url(settings, keyterms),
        data=audio,
        method="POST",
        headers={
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": mime_type,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.deepgram_timeout_seconds) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode("utf-8", "replace")
        log.warning("Deepgram returned HTTP %s: %s", exc.code, detail)
        kind: SpeechErrorKind = "unreadable" if exc.code == 400 else "unavailable"
        raise SpeechError(kind, f"Deepgram returned HTTP {exc.code}") from exc
    except OSError as exc:  # URLError, refused connections and timeouts are all OSError
        log.warning("Deepgram could not be reached: %s", exc)
        raise SpeechError("unavailable", "Deepgram could not be reached") from exc

    return _parse(raw)


def _parse(raw: bytes) -> Transcript:
    try:
        best = json.loads(raw)["results"]["channels"][0]["alternatives"][0]
        text = str(best.get("transcript") or "").strip()
        confidence = float(best.get("confidence") or 0.0)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        log.warning("Deepgram reply was not in the documented shape: %r", raw[:200])
        raise SpeechError("unavailable", "Deepgram's reply was not in the expected shape") from exc
    return Transcript(text=text, confidence=confidence)
