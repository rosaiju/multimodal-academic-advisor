from __future__ import annotations

from pathlib import Path

from app.speech.base import SpeechError, Transcript
from app.speech.benchmark import PHRASES, find_recordings, recognized, report, run_one

CODES = ["COSC459", "COSC243"]
PHRASE = next(p for p in PHRASES if p.id == "cs-459")


def run(tmp_path: Path, fake):
    audio = tmp_path / "cs-459.webm"
    audio.write_bytes(b"x")
    return run_one(fake, PHRASE, audio, provider="p", model="m", catalog_codes=CODES)


def test_spoken_code_is_normalized_and_recognized(tmp_path) -> None:
    r = run(tmp_path, lambda *a: Transcript("computer science four fifty nine", 0.9))
    assert r.ok and r.recognized and "COSC" in r.normalized and r.confidence == 0.9


def test_wrong_code_is_not_recognized(tmp_path) -> None:
    r = run(tmp_path, lambda *a: Transcript("computer science four fifty eight", 0.9))
    assert r.ok and not r.recognized


def test_failure_is_recorded_not_raised(tmp_path) -> None:
    def boom(*a):
        raise SpeechError("unavailable", "down")

    r = run(tmp_path, boom)
    assert not r.ok and not r.recognized and "down" in r.error
    assert "FAILED" in report([r])


def test_recognized_ignores_spacing_and_case() -> None:
    assert recognized("what is cosc 459?", ["COSC459"])
    assert not recognized("cosc 45", ["COSC459"])


def test_find_recordings_matches_known_ids_only(tmp_path) -> None:
    (tmp_path / "cs-459.wav").write_bytes(b"x")
    (tmp_path / "other.wav").write_bytes(b"x")
    (tmp_path / "math-141.txt").write_bytes(b"x")
    assert set(find_recordings(tmp_path)) == {"cs-459"}
    assert find_recordings(tmp_path / "nope") == {}
