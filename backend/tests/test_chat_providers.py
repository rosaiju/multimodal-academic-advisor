"""The Gemini and Ollama HTTP paths, against a local server speaking their shapes.

The stub provider in test_advisor.py proves the advisor degrades correctly. It
does NOT prove that `GeminiChatProvider` can parse a Gemini response, because a
stub satisfying the protocol never exercises a line of HTTP or JSON handling.

So these tests run a real HTTP server on a loopback port and have the providers
talk to it. That is as far as honesty allows without credentials: it verifies the
request we send and the reply we parse are the shapes each vendor documents, and
that every failure mode degrades. It does NOT verify that Google or a local
Ollama accepts them - only a real key and a real daemon can, and neither exists
on this machine. See docs/demo-reference.md.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import Settings
from app.llm.chat import (
    ChatTurn,
    GeminiChatProvider,
    OllamaChatProvider,
    ProviderUnavailable,
    get_chat_provider,
)

MESSAGES = [ChatTurn(role="user", content="How many credits am I missing?")]


class Recorder(BaseHTTPRequestHandler):
    """Captures the request, replies with whatever the test queued."""

    status = 200
    body: dict | str = {}
    captured: dict = {}

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's API
        self._respond()

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode()
        type(self).captured = {
            "path": self.path,
            # Lowercased: HTTP header names are case-insensitive and urllib
            # capitalises them on the way out.
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "json": json.loads(raw) if raw else None,
        }
        self._respond()

    def _respond(self):
        payload = type(self).body
        encoded = (json.dumps(payload) if isinstance(payload, dict) else payload).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    Recorder.status, Recorder.body, Recorder.captured = 200, {}, {}
    yield httpd, f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def gemini_reply(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class TestGeminiWireFormat:
    def test_parses_a_normal_response(self, server, monkeypatch) -> None:
        httpd, base = server
        Recorder.body = gemini_reply("You need 97 credits.")
        provider = GeminiChatProvider("test-key", "gemini-2.0-flash")
        monkeypatch.setattr(provider, "API", base + "/v1beta/models")
        assert provider.complete(system="sys", messages=MESSAGES) == "You need 97 credits."

    def test_sends_the_key_as_a_header_not_a_query_parameter(self, server, monkeypatch) -> None:
        """A key in the URL lands in proxy and server access logs."""
        httpd, base = server
        Recorder.body = gemini_reply("ok")
        provider = GeminiChatProvider("secret-key-value", "gemini-2.0-flash")
        monkeypatch.setattr(provider, "API", base + "/v1beta/models")
        provider.complete(system="sys", messages=MESSAGES)
        assert Recorder.captured["headers"]["x-goog-api-key"] == "secret-key-value"
        assert "secret-key-value" not in Recorder.captured["path"]

    def test_maps_roles_and_system_instruction(self, server, monkeypatch) -> None:
        httpd, base = server
        Recorder.body = gemini_reply("ok")
        provider = GeminiChatProvider("k", "gemini-2.0-flash")
        monkeypatch.setattr(provider, "API", base + "/v1beta/models")
        provider.complete(
            system="SYSTEM TEXT",
            messages=[
                ChatTurn(role="user", content="first"),
                ChatTurn(role="assistant", content="second"),
            ],
        )
        sent = Recorder.captured["json"]
        assert sent["systemInstruction"]["parts"][0]["text"] == "SYSTEM TEXT"
        # Gemini calls the assistant "model" - getting this wrong is a 400.
        assert [c["role"] for c in sent["contents"]] == ["user", "model"]

    def test_quota_exhaustion_is_reported_as_unavailable(self, server, monkeypatch) -> None:
        httpd, base = server
        Recorder.status, Recorder.body = 429, {"error": {"message": "quota"}}
        provider = GeminiChatProvider("k", "gemini-2.0-flash")
        monkeypatch.setattr(provider, "API", base + "/v1beta/models")
        with pytest.raises(ProviderUnavailable, match="quota exhausted"):
            provider.complete(system="s", messages=MESSAGES)

    def test_a_rejected_key_is_reported_as_unavailable(self, server, monkeypatch) -> None:
        httpd, base = server
        Recorder.status, Recorder.body = 403, {"error": {"message": "bad key"}}
        provider = GeminiChatProvider("k", "gemini-2.0-flash")
        monkeypatch.setattr(provider, "API", base + "/v1beta/models")
        with pytest.raises(ProviderUnavailable, match="rejected the credential"):
            provider.complete(system="s", messages=MESSAGES)

    def test_a_safety_block_is_not_mistaken_for_an_answer(self, server, monkeypatch) -> None:
        httpd, base = server
        Recorder.body = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
        provider = GeminiChatProvider("k", "gemini-2.0-flash")
        monkeypatch.setattr(provider, "API", base + "/v1beta/models")
        with pytest.raises(ProviderUnavailable, match="SAFETY"):
            provider.complete(system="s", messages=MESSAGES)

    def test_non_json_body_is_handled(self, server, monkeypatch) -> None:
        httpd, base = server
        Recorder.body = "<html>gateway error</html>"
        provider = GeminiChatProvider("k", "gemini-2.0-flash")
        monkeypatch.setattr(provider, "API", base + "/v1beta/models")
        with pytest.raises(ProviderUnavailable, match="non-JSON"):
            provider.complete(system="s", messages=MESSAGES)

    def test_unavailable_without_a_key(self) -> None:
        ok, reason = GeminiChatProvider("", "gemini-2.0-flash").available()
        assert ok is False
        assert "GEMINI_API_KEY" in reason


class TestOllamaWireFormat:
    def test_parses_a_normal_response(self, server) -> None:
        httpd, base = server
        Recorder.body = {"message": {"role": "assistant", "content": "Take COSC 220."}}
        provider = OllamaChatProvider(base, "llama3.1")
        assert provider.complete(system="s", messages=MESSAGES) == "Take COSC 220."

    def test_sends_system_as_the_first_message(self, server) -> None:
        httpd, base = server
        Recorder.body = {"message": {"content": "ok"}}
        OllamaChatProvider(base, "llama3.1").complete(system="SYSTEM", messages=MESSAGES)
        sent = Recorder.captured["json"]
        assert sent["messages"][0] == {"role": "system", "content": "SYSTEM"}
        assert sent["stream"] is False, "streaming would break the single-response parse"
        assert sent["model"] == "llama3.1"

    def test_available_is_false_when_nothing_is_listening(self) -> None:
        # Port 1 is reserved and never has an Ollama on it.
        ok, reason = OllamaChatProvider("http://127.0.0.1:1", "llama3.1").available()
        assert ok is False
        assert "no Ollama server" in reason

    def test_available_names_the_models_that_are_pulled(self, server) -> None:
        httpd, base = server
        Recorder.body = {"models": [{"name": "mistral:latest"}]}
        ok, reason = OllamaChatProvider(base, "llama3.1").available()
        assert ok is False
        assert "mistral:latest" in reason

    def test_a_tag_suffix_still_counts_as_pulled(self, server) -> None:
        """Ollama reports llama3.1:latest for a model pulled as llama3.1."""
        httpd, base = server
        Recorder.body = {"models": [{"name": "llama3.1:latest"}]}
        ok, _ = OllamaChatProvider(base, "llama3.1").available()
        assert ok is True

    def test_a_server_with_no_models_says_so(self, server) -> None:
        httpd, base = server
        Recorder.body = {"models": []}
        ok, reason = OllamaChatProvider(base, "llama3.1").available()
        assert ok is False
        assert "no models pulled" in reason

    def test_empty_completion_is_an_error_not_an_answer(self, server) -> None:
        httpd, base = server
        Recorder.body = {"message": {"content": "   "}}
        with pytest.raises(ProviderUnavailable, match="empty completion"):
            OllamaChatProvider(base, "llama3.1").complete(system="s", messages=MESSAGES)


class TestProviderSelection:
    @pytest.mark.parametrize(
        "provider_id,expected",
        [
            ("anthropic", "anthropic"),
            ("openai", "openai"),
            ("gemini", "gemini"),
            ("ollama", "ollama"),
        ],
    )
    def test_each_configured_provider_is_built(self, provider_id, expected) -> None:
        settings = Settings(llm_provider=provider_id)
        assert get_chat_provider(settings).provider_id == expected

    def test_ollama_counts_as_configured_without_a_key(self) -> None:
        """It needs no credential, so 'configured' is the wrong question to ask."""
        assert Settings(llm_provider="ollama").llm_configured is True

    def test_gemini_needs_a_key_to_count_as_configured(self) -> None:
        assert Settings(llm_provider="gemini").llm_configured is False
        assert Settings(llm_provider="gemini", gemini_api_key="k").llm_configured is True

    def test_chat_model_follows_the_provider(self) -> None:
        assert Settings(llm_provider="gemini").chat_model.startswith("gemini")
        assert Settings(llm_provider="ollama").chat_model == "llama3.1"
