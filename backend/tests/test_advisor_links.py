"""Curated links: attached by code to out-of-scope answers, never taken from a model."""

from __future__ import annotations

from app.advisor.chat import ask
from app.advisor.links import Link, all_links, links_for
from app.advisor.answers import Intent
from tests.test_advisor import StubProvider, facts  # noqa: F401 - fixture


def test_unknown_question_gets_curated_links(facts) -> None:  # noqa: F811
    reply = ask(facts, "can I get a parking permit?", student_id="s", use_llm=False)
    assert reply.intent is Intent.UNKNOWN
    assert reply.links
    assert set(reply.links) <= set(all_links())


def test_known_intent_has_no_links(facts) -> None:  # noqa: F811
    reply = ask(facts, "how many credits am I missing?", student_id="s", use_llm=False)
    assert reply.intent is Intent.CREDITS
    assert reply.links == []


def test_links_survive_rephrase(facts) -> None:  # noqa: F811
    stub = StubProvider(reply="See https://evil.example for parking.")
    reply = ask(facts, "can I get a parking permit?", student_id="s", provider=stub)
    assert reply.links == links_for(Intent.UNKNOWN)
    assert all("evil" not in link.url for link in reply.links)


def test_yaml_urls_are_https() -> None:
    assert all_links()
    for link in all_links():
        assert isinstance(link, Link)
        assert link.url.startswith("https://")
        assert link.name
