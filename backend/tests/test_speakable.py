"""The deterministic pass that turns a checked answer into text worth hearing."""

from app.advisor.links import Link
from app.speech.speakable import to_speech

REG = Link("the Registrar's Office", "https://www.morgan.edu/registrar")


def test_course_code_spoken() -> None:
    assert "C O S C 2 41" in to_speech("Take COSC 241.")
    assert "COSC" not in to_speech("Take COSC 241 and MATH 141.")


def test_bullets_flattened() -> None:
    out = to_speech("You still need:\n- COSC 241\n- MATH 141\n- ENGL 102")
    assert "-" not in out and "\n" not in out
    assert "C O S C 2 41, M A T H 1 41, and E N G L 1 02" in out


def test_markdown_stripped() -> None:
    out = to_speech("## Plan\n**Take** `COSC 241` now.")
    assert not any(ch in out for ch in "*#`")


def test_url_replaced_by_name() -> None:
    out = to_speech(f"Ask at {REG.url} please.", [REG])
    assert "https://" not in out and REG.name in out


def test_unmentioned_links_appended() -> None:
    out = to_speech("I can't answer that.", [REG])
    assert out.endswith("You can check the Registrar's Office.")


def test_stray_url_removed() -> None:
    assert "http" not in to_speech("See https://example.com/x for more.")


def test_idempotent() -> None:
    once = to_speech("You need:\n- COSC 241\n- MATH 141\n\nCheck **this**.", [REG])
    assert to_speech(once) == once
