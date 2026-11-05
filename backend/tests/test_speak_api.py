"""POST /advisor/speak: checked text in, spoken mp3 out."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.routers import chat as chat_router
from app.speech.base import SpeechError

REG = {"name": "the Registrar's Office", "url": "https://www.morgan.edu/registrar"}


@pytest.fixture
def spoken(monkeypatch):
    heard: list[str] = []

    def fake(text, *, settings=None):
        heard.append(text)
        return b"ID3-mp3"

    monkeypatch.setattr(chat_router, "synthesize", fake)
    return heard


def test_speak_route_returns_audio(client, spoken) -> None:
    r = client.post("/advisor/speak", json={"text": f"Take COSC 241. See {REG['url']}", "links": [REG]})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == b"ID3-mp3"
    assert "https://" not in spoken[0] and "C O S C 2 41" in spoken[0]
    assert REG["name"] in spoken[0]


def test_speak_route_requires_auth(anon_client, spoken) -> None:
    assert anon_client.post("/advisor/speak", json={"text": "hi"}).status_code == 401


def test_speak_route_503_without_key(client, monkeypatch) -> None:
    def down(text, *, settings=None):
        raise SpeechError("unavailable", "no Deepgram key is configured")

    monkeypatch.setattr(chat_router, "synthesize", down)
    r = client.post("/advisor/speak", json={"text": "hi"})
    assert r.status_code == 503
    assert "Deepgram" in r.json()["detail"]


def test_speak_route_rejects_empty_and_huge(client, spoken) -> None:
    assert client.post("/advisor/speak", json={"text": "   "}).status_code == 400
    assert client.post("/advisor/speak", json={"text": "x" * 2001}).status_code == 413
    assert spoken == []
