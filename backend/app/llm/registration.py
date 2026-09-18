"""Wiring the model-backed extractor into the ingestion layer.

This file is the seam. `app/ingestion/` defines the protocol and knows nothing
about models; this registers an implementation with it at application startup. The
dependency points one way only, which is what keeps the deterministic path
independently testable.

Registration is best-effort by design. If no API key is configured the application
starts normally and plain-text transcripts keep working - a missing key should
narrow what the system accepts, never stop it booting.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.ingestion.extractor import register_extractor
from app.llm.provider import ProviderError, get_vision_provider
from app.llm.transcript_vision import VisionTranscriptExtractor

logger = logging.getLogger(__name__)


def register_vision_extractor(settings: Settings | None = None) -> str | None:
    """Register the vision extractor if a provider is configured.

    Returns its name, or None with a logged explanation when unavailable.
    """
    settings = settings or get_settings()
    try:
        provider = get_vision_provider(settings)
    except ProviderError as exc:
        logger.info("vision transcript extractor unavailable: %s", exc)
        return None

    extractor = VisionTranscriptExtractor(provider)
    register_extractor(extractor)
    return extractor.name
