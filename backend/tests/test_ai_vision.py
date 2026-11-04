"""The local (Ollama) vision reader for scanned transcripts.

Mocked throughout: a loopback server stands in for Ollama. The live run against
qwen2.5vl:3b is recorded in docs/advisor.md, not repeated here. Every row a model
reads is UNVERIFIED and still has to be confirmed by the student.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import app.ingestion.extractor as extractor_module
from app.config import Settings
from app.llm.provider import (
    AnthropicVisionProvider,
    OllamaVisionProvider,
    ProviderError,
    get_vision_provider,
)
from app.llm.registration import register_vision_extractor
from app.llm.transcript_vision import PROMPT, VisionTranscriptExtractor
from tests.test_chat_providers import Recorder, server  # noqa: F401

SAMPLE_PNG = Path(__file__).resolve().parents[2] / "data" / "samples" / "demo_transcript.png"
PROGRAM = "morgan_cosc_bs_2026_2028"

ROWS = [
    ("COSC 111", "Fall 2024", "A", 4),
    ("ENGL 101", "Fall 2024", "B", 3),
    ("MATH 241", "Fall 2024", "B", 4),
    ("UNIV 101", "Fall 2024", "A", 1),
    ("COSC 112", "Spring 2025", "A", 4),
    ("ENGL 102", "Spring 2025", "A", 3),
    ("MATH 242", "Spring 2025", "C", 4),
]
MODEL_JSON = json.dumps(
    {
        "institution": "MORGAN STATE UNIVERSITY",
        "courses": [
            {"code": c, "title": None, "term": t, "grade": g, "credits": cr, "raw_line": c}
            for c, t, g, cr in ROWS
        ],
    }
)


def ollama_reply(content: str) -> dict:
    return {"message": {"role": "assistant", "content": content}}


def test_sample_png_exists_and_is_a_png() -> None:
    assert SAMPLE_PNG.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_request_shape(server) -> None:  # noqa: F811
    _, base = server
    Recorder.body = ollama_reply(MODEL_JSON)
    provider = OllamaVisionProvider(base, "qwen2.5vl:3b", timeout=5)
    out = provider.read_document(b"PNGDATA", media_type="image/png", prompt="read it")
    assert out == MODEL_JSON
    sent = Recorder.captured["json"]
    assert Recorder.captured["path"] == "/api/chat"
    assert sent["model"] == "qwen2.5vl:3b"
    assert sent["format"] == "json" and sent["stream"] is False
    assert sent["options"]["temperature"] == 0
    message = sent["messages"][0]
    assert message["content"] == "read it"
    assert base64.b64decode(message["images"][0]) == b"PNGDATA"


def test_pdf_is_refused_and_not_offered() -> None:
    provider = OllamaVisionProvider("http://127.0.0.1:9", "qwen2.5vl:3b")
    with pytest.raises(ProviderError, match="not PDFs"):
        provider.read_document(b"%PDF", media_type="application/pdf", prompt="x")
    extractor = VisionTranscriptExtractor(provider)
    assert extractor.can_handle("t.png", "image/png")
    assert extractor.can_handle("t.jpg", None)
    assert not extractor.can_handle("t.pdf", "application/pdf")


def test_empty_reply_is_an_error(server) -> None:  # noqa: F811
    _, base = server
    Recorder.body = ollama_reply("  ")
    with pytest.raises(ProviderError):
        OllamaVisionProvider(base, "m").read_document(b"x", media_type="image/png", prompt="p")


def test_unreachable_server_is_a_provider_error() -> None:
    provider = OllamaVisionProvider("http://127.0.0.1:9", "m", timeout=1)
    with pytest.raises(ProviderError):
        provider.read_document(b"x", media_type="image/png", prompt="p")


def test_settings_select_the_provider() -> None:
    chosen = get_vision_provider(
        Settings(vision_provider="ollama", ollama_vision_model="qwen2.5vl:3b")
    )
    assert isinstance(chosen, OllamaVisionProvider) and chosen.name == "qwen2.5vl:3b"
    # Empty keeps the original rule: follow LLM_PROVIDER, and a missing key raises.
    with pytest.raises(ProviderError):
        get_vision_provider(
            Settings(vision_provider="", llm_provider="anthropic", anthropic_api_key="")
        )
    explicit = get_vision_provider(
        Settings(vision_provider="anthropic", llm_provider="ollama", anthropic_api_key="k")
    )
    assert isinstance(explicit, AnthropicVisionProvider)


def test_off_by_default() -> None:
    assert Settings.model_fields["vision_provider"].default == ""


@pytest.fixture
def ollama_vision(server, monkeypatch):  # noqa: F811
    """The vision extractor registered against the loopback 'Ollama'."""
    _, base = server
    monkeypatch.setattr(extractor_module, "_registry", list(extractor_module._registry))
    settings = Settings(vision_provider="ollama", ollama_base_url=base)
    name = register_vision_extractor(settings)
    assert name == "vision:qwen2.5vl:3b"
    return name


def test_png_upload_returns_unverified_rows_that_need_confirmation(client, ollama_vision) -> None:
    Recorder.body = ollama_reply(MODEL_JSON)
    response = client.post(
        "/ingest/transcript",
        files={"file": ("demo_transcript.png", SAMPLE_PNG.read_bytes(), "image/png")},
        data={"program_id": PROGRAM},
    )
    assert response.status_code == 200, response.text
    extraction = response.json()["extraction"]
    assert extraction["extractor"] == ollama_vision
    rows = extraction["courses"]
    assert [r["code"] for r in rows] == [c.replace(" ", "") for c, *_ in ROWS]
    assert {r["provenance"] for r in rows} == {"unverified_extraction"}
    assert {r["confidence"] for r in rows} == {"medium"}
    # The prompt went to the model with the image.
    assert Recorder.captured["json"]["messages"][0]["content"] == PROMPT

    # Nothing is stored by an upload.
    assert client.get("/students/{me}/record").status_code == 404

    # The existing review flow: only rows the student confirms are stored.
    confirm = client.post(
        "/ingest/confirm",
        json={
            "program_id": PROGRAM,
            "source_name": "demo_transcript.png",
            "extractor": ollama_vision,
            "courses": [{"extracted": r} for r in rows[:3]],
        },
    )
    assert confirm.status_code == 200, confirm.text
    stored = client.get("/students/{me}/record").json()
    assert len(stored["confirmed"]) == 3


def test_a_failing_local_model_is_a_503_without_internals(client, ollama_vision) -> None:
    Recorder.status, Recorder.body = 500, {"error": "model runner crashed at C:\secret"}
    response = client.post(
        "/ingest/transcript",
        files={"file": ("demo_transcript.png", SAMPLE_PNG.read_bytes(), "image/png")},
        data={"program_id": PROGRAM},
    )
    assert response.status_code == 503
    assert "secret" not in response.text and "plain-text" in response.json()["detail"]
