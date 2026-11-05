# ruff: noqa: F811 - pytest fixtures are imported, then named as parameters
"""Model-written advisor answers: who wrote the reply, and every fallback path.

The writing model gets the engine's verified facts and prepared answer; its reply
is shown only if `check_rephrasing` passes. These tests pin down what the API
reports in each case (`source`, `fallback`), drive the real `GeminiChatProvider`
over HTTP against a local server speaking Gemini's wire format, and check that
the startup log never contains a key.
"""

from __future__ import annotations

import logging

from app.advisor.answers import deterministic_answer
from app.advisor.chat import ask
from app.config import Settings
from app.llm.chat import GeminiChatProvider, ProviderUnavailable
from tests.test_advisor import StubProvider, facts  # noqa: F401 - fixture
from tests.test_chat_providers import Recorder, gemini_reply, server  # noqa: F401 - fixture

REMAINING = "What courses do I still need to graduate?"


class Raising(StubProvider):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    def complete(self, *, system, messages, max_tokens=800):
        raise self._exc


def _ask(facts, provider, question=REMAINING, sid="g", **kw):  # noqa: F811
    return ask(facts, question, student_id=sid, provider=provider, rephrase=True, **kw)


# --- what the API says wrote the answer ----------------------------------------


def test_a_checked_model_answer_is_reported_as_model_written(facts) -> None:  # noqa: F811
    prepared = deterministic_answer(facts, REMAINING).text
    reply = _ask(facts, StubProvider(reply=prepared), sid="g1")
    assert reply.source == "engine+llm"
    assert reply.fallback is None
    assert reply.model == "stub-model"
    assert reply.model_seconds is not None


def test_a_model_answer_with_an_invented_number_is_rejected(facts) -> None:  # noqa: F811
    prepared = deterministic_answer(facts, REMAINING).text
    wrong = prepared + "\n\nGood news: you only need 12 more credits."
    reply = _ask(facts, StubProvider(reply=wrong), sid="g2")
    assert reply.source == "engine"
    assert reply.fallback == "check_failed"
    assert reply.answer == prepared  # the student reads the engine, not the model
    assert reply.discarded == wrong


def test_an_unreachable_model_is_a_fallback_not_an_error(facts) -> None:  # noqa: F811
    reply = _ask(facts, Raising(ProviderUnavailable("quota exhausted (429)")), sid="g3")
    assert reply.source == "engine"
    assert reply.fallback == "model_unavailable"


def test_a_switched_off_model_is_a_fallback(facts) -> None:  # noqa: F811
    reply = _ask(facts, StubProvider(usable=False), sid="g4")
    assert reply.fallback == "model_unavailable"


def test_a_crashing_model_is_a_fallback(facts) -> None:  # noqa: F811
    reply = _ask(facts, Raising(RuntimeError("bug in the SDK")), sid="g5")
    assert reply.fallback == "model_error"


def test_rephrasing_off_says_so(facts) -> None:  # noqa: F811
    reply = ask(facts, REMAINING, student_id="g6", provider=StubProvider(), rephrase=False)
    assert reply.source == "engine"
    assert reply.fallback == "rephrase_off"


def test_fixed_text_is_not_a_fallback(facts) -> None:  # noqa: F811
    """A crisis reply is never given to a model: that is the design, not a failure."""
    stub = StubProvider()
    reply = _ask(facts, stub, question="I want to kill myself", sid="g7")
    assert reply.source == "engine"
    assert reply.fallback is None
    assert stub.calls == []


def test_the_prompt_asks_for_an_advising_explanation_with_follow_up_context(
    facts,
) -> None:  # noqa: F811
    prepared = deterministic_answer(facts, REMAINING).text
    stub = StubProvider(reply=prepared)
    _ask(facts, stub, sid="g8")
    _ask(facts, stub, question="So I'm basically done then, right?", sid="g8")
    system, messages = stub.calls[-1]
    assert "VERIFIED FACTS FOR THIS STUDENT" in system
    assert "explain WHY" in system and "next step" in system
    # The follow-up reaches the model with the earlier turn, so it can answer in context.
    assert any(REMAINING in m.content for m in messages)
    assert messages[-1].content == "So I'm basically done then, right?"


# --- the real Gemini provider, over HTTP ----------------------------------------


def _gemini(base, monkeypatch):
    provider = GeminiChatProvider("test-key", "gemini-test")
    monkeypatch.setattr(provider, "API", base + "/v1beta/models")
    return provider


def test_gemini_written_answer_end_to_end(facts, server, monkeypatch) -> None:  # noqa: F811
    _, base = server
    prepared = deterministic_answer(facts, REMAINING).text
    Recorder.body = gemini_reply(prepared)
    reply = _ask(facts, _gemini(base, monkeypatch), sid="g9")
    assert reply.source == "engine+llm"
    assert (reply.provider, reply.model) == ("gemini", "gemini-test")
    sent = Recorder.captured["json"]
    assert "VERIFIED FACTS FOR THIS STUDENT" in sent["systemInstruction"]["parts"][0]["text"]


def test_gemini_answer_that_contradicts_the_engine_is_rejected(
    facts, server, monkeypatch
) -> None:  # noqa: F811
    _, base = server
    Recorder.body = gemini_reply("You're all set - you can graduate this spring!")
    reply = _ask(facts, _gemini(base, monkeypatch), sid="g10")
    assert reply.source == "engine"
    assert reply.fallback == "check_failed"


def test_gemini_http_error_falls_back(facts, server, monkeypatch) -> None:  # noqa: F811
    _, base = server
    Recorder.status, Recorder.body = 404, {"error": {"message": "model not found"}}
    reply = _ask(facts, _gemini(base, monkeypatch), sid="g11")
    assert reply.fallback == "model_unavailable"
    assert "model not found" in (reply.notice or "")


# --- configuration -------------------------------------------------------------


def test_gemini_key_is_read_from_either_env_name(monkeypatch) -> None:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv(name, "k-from-env")
        settings = Settings(_env_file=None, llm_provider="gemini")
        assert settings.chat_api_key == "k-from-env", name


def test_startup_log_names_the_model_but_never_the_key(monkeypatch, caplog) -> None:
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-test-key")
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.INFO), TestClient(app):
            pass
    finally:
        get_settings.cache_clear()
    logged = caplog.text
    assert "advisor answers: provider=gemini" in logged
    assert "api_key=loaded" in logged
    assert "super-secret-test-key" not in logged
