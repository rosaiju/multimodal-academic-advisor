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


def test_markdown_link_keeps_only_its_text() -> None:
    out = to_speech("Ask [the Registrar](https://www.morgan.edu/registrar) today.")
    assert out == "Ask the Registrar today."


def test_url_keeps_trailing_punctuation_and_www_removed() -> None:
    assert to_speech("See https://example.com/x. Then go.") == "See. Then go."
    assert "www" not in to_speech("Visit www.morgan.edu for more.")


# --- no markdown syntax is ever read aloud -------------------------------------

SYMBOLS = set("*#`_~|>")


def _clean(out: str) -> None:
    assert not SYMBOLS & set(out), out
    assert "--" not in out and "==" not in out, out


def test_heading_becomes_its_own_sentence() -> None:
    out = to_speech("## Your plan\nTake COSC 241 next.")
    _clean(out)
    assert out.startswith("Your plan. ")


def test_bold_italic_and_strikethrough_stripped() -> None:
    out = to_speech("***Key point:*** take _this_ and ~~not that~~ course.")
    _clean(out)
    assert "Key point: take this and not that course." == out


def test_horizontal_rules_dropped() -> None:
    for rule in ("---", "***", "___", "- - -"):
        out = to_speech(f"First part.\n\n{rule}\n\nSecond part.")
        assert out == "First part. Second part.", (rule, out)


def test_table_read_as_rows() -> None:
    out = to_speech("| Course | Credits |\n|---|:---:|\n| COSC 241 | 4 |\n| MATH 141 | 3 |")
    _clean(out)
    assert "Course, Credits" in out and "C O S C 2 41, 4" in out


def test_code_fence_lines_dropped() -> None:
    out = to_speech("Run this:\n```text\nCOSC 241\n```\nDone.")
    _clean(out)
    assert "text" not in out and "C O S C 2 41" in out


def test_numbered_and_star_bullets_flattened() -> None:
    out = to_speech("Take:\n1. COSC 241\n2. MATH 141\n* ENGL 102")
    _clean(out)
    assert "1." not in out and "E N G L 1 02" in out


def test_blockquote_and_escapes_stripped() -> None:
    out = to_speech("> Note: \*not\* final.")
    _clean(out)
    assert "\\" not in out and out == "Note: not final."
