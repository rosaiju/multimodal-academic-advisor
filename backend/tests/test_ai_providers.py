"""JSON-mode routing calls and the understanding-provider factory, on the wire.

Reuses the loopback server from test_chat_providers.py. Gemini has never been
called live (no key exists); these tests pin the request we send and the reply we
parse against its documented shapes, and that the key travels only in the
x-goog-api-key header.
"""

from __future__ import annotations

from app.config import Settings
from app.llm.chat import (
    GeminiChatProvider,
    OllamaChatProvider,
    get_understanding_provider,
)
from tests.test_chat_providers import MESSAGES, Recorder, gemini_reply, server  # noqa: F401


def test_ollama_json_mode_request(server) -> None:  # noqa: F811
    _, base = server
    Recorder.body = {"message": {"role": "assistant", "content": '{"intent": "credits"}'}}
    text = OllamaChatProvider(base, "qwen2.5:7b").complete_json(system="S", messages=MESSAGES)
    assert text == '{"intent": "credits"}'
    sent = Recorder.captured["json"]
    assert Recorder.captured["path"] == "/api/chat"
    assert sent["format"] == "json"
    assert sent["options"]["temperature"] == 0
    assert sent["options"]["num_predict"] <= 120
    assert sent["messages"][0] == {"role": "system", "content": "S"}


def test_gemini_json_mode_request_and_key_header(server, monkeypatch) -> None:  # noqa: F811
    _, base = server
    monkeypatch.setattr(GeminiChatProvider, "API", f"{base}/v1beta/models")
    Recorder.body = gemini_reply('{"intent": "plan_semesters"}')
    provider = GeminiChatProvider("test-key-123", "gemini-2.0-flash")
    assert provider.complete_json(system="S", messages=MESSAGES) == '{"intent": "plan_semesters"}'
    captured = Recorder.captured
    assert captured["path"] == "/v1beta/models/gemini-2.0-flash:generateContent"
    assert "key=" not in captured["path"] and "test-key-123" not in captured["path"]
    assert captured["headers"]["x-goog-api-key"] == "test-key-123"
    config = captured["json"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["temperature"] == 0
    assert captured["json"]["systemInstruction"] == {"parts": [{"text": "S"}]}


def test_gemini_plain_completion_is_unchanged(server, monkeypatch) -> None:  # noqa: F811
    _, base = server
    monkeypatch.setattr(GeminiChatProvider, "API", f"{base}/v1beta/models")
    Recorder.body = gemini_reply("You need 97 credits.")
    GeminiChatProvider("k", "gemini-2.0-flash").complete(system="S", messages=MESSAGES)
    assert "responseMimeType" not in Recorder.captured["json"]["generationConfig"]


def test_understanding_provider_for_ollama_uses_its_own_model_and_timeout() -> None:
    settings = Settings(
        llm_provider="ollama",
        ollama_chat_model="qwen2.5:7b",
        ollama_understanding_model="llama3.2:3b",
        advisor_understanding_timeout_seconds=12,
    )
    provider = get_understanding_provider(settings)
    assert provider.name == "llama3.2:3b"
    assert provider._timeout == 12
    settings = Settings(llm_provider="ollama", ollama_chat_model="qwen2.5:7b")
    assert get_understanding_provider(settings).name == "qwen2.5:7b"


def test_understanding_provider_without_a_key_is_unavailable() -> None:
    provider = get_understanding_provider(Settings(llm_provider="gemini", gemini_api_key=""))
    assert provider.available()[0] is False
