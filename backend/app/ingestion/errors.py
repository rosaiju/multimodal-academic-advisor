"""Ingestion exceptions.

Kept in their own module with no imports of its own, so extractors can share them
without the package importing itself in a circle.
"""

from __future__ import annotations


class UnsupportedDocument(RuntimeError):
    """No registered extractor can read this document."""


class DocumentNotReadable(RuntimeError):
    """This extractor handles the file TYPE but cannot read THIS document.

    Raised mid-extraction rather than from `can_handle`, because some documents
    only reveal the problem once opened - a scanned PDF looks like any other PDF
    until you find it has no text layer. `extract_transcript` treats it as a
    decline and tries the next candidate.
    """
