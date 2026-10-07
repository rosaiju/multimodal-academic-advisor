"""What the student sees while they talk, and how Deepgram's messages are read.

Pure logic - no sockets. The message shapes are the ones Deepgram's live API
returned on Sep 22 2026 (see test_speech_live.py for the wire itself).
"""

from __future__ import annotations

import json

from app.speech.live import Final, LiveTranscript, Partial, UtteranceEnd, parse_live_message

CODES = ["UNIV101", "COSC243", "COSC241"]


def results(text: str, *, is_final: bool, confidence: float = 0.9) -> str:
    return json.dumps(
        {
            "type": "Results",
            "is_final": is_final,
            "speech_final": is_final,
            "channel": {"alternatives": [{"transcript": text, "confidence": confidence}]},
        }
    )


class TestLiveTranscript:
    def test_starts_empty(self) -> None:
        transcript = LiveTranscript()
        assert transcript.text(CODES) == ""
        assert transcript.heard_speech is False
        assert transcript.confidence == 0.0

    def test_a_new_partial_replaces_the_old_one(self) -> None:
        transcript = LiveTranscript()
        transcript.add_partial("Can I")
        transcript.add_partial("Can I take")
        assert transcript.text(CODES) == "Can I take"

    def test_finals_accumulate_and_clear_the_partial(self) -> None:
        transcript = LiveTranscript()
        transcript.add_partial("Can I")
        transcript.add_final("Can I take", 0.9)
        transcript.add_partial("University")
        assert transcript.text(CODES) == "Can I take University"
        transcript.add_final("University one zero one?", 0.8)
        assert transcript.text(CODES) == "Can I take UNIV 101?"

    def test_codes_convert_across_a_final_and_a_partial(self) -> None:
        transcript = LiveTranscript()
        transcript.add_final("Computer Science two forty", 0.9)
        transcript.add_partial("three")
        assert transcript.text(CODES) == "COSC 243"

    def test_an_unknown_code_is_shown_as_heard(self) -> None:
        transcript = LiveTranscript()
        transcript.add_final("computer science nine ninety nine", 0.9)
        assert transcript.text(CODES) == "computer science nine ninety nine"

    def test_confidence_is_the_mean_of_the_finals(self) -> None:
        transcript = LiveTranscript()
        transcript.add_final("a", 0.9)
        transcript.add_final("b", 0.5)
        transcript.add_partial("c")
        assert transcript.confidence == 0.7

    def test_an_empty_final_is_not_speech(self) -> None:
        transcript = LiveTranscript()
        transcript.add_final("   ", 0.0)
        assert transcript.heard_speech is False
        assert transcript.confidence == 0.0

    def test_a_partial_counts_as_speech(self) -> None:
        transcript = LiveTranscript()
        transcript.add_partial("Can")
        assert transcript.heard_speech is True


class TestParseLiveMessage:
    def test_interim_result_is_a_partial(self) -> None:
        assert parse_live_message(results("Can I take", is_final=False)) == Partial("Can I take")

    def test_final_result_is_a_final(self) -> None:
        event = parse_live_message(results("Can I take COSC 241?", is_final=True, confidence=0.97))
        assert event == Final("Can I take COSC 241?", 0.97)

    def test_empty_final_is_ignored(self) -> None:
        assert parse_live_message(results("", is_final=True)) is None

    def test_utterance_end(self) -> None:
        raw = json.dumps({"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": 2.4})
        assert parse_live_message(raw) == UtteranceEnd()

    def test_other_message_types_are_ignored(self) -> None:
        assert parse_live_message(json.dumps({"type": "Metadata", "request_id": "x"})) is None
        assert parse_live_message(json.dumps({"type": "SpeechStarted"})) is None

    def test_malformed_messages_are_ignored(self) -> None:
        assert parse_live_message("not json") is None
        assert parse_live_message(json.dumps({"type": "Results"})) is None
        assert parse_live_message(b"\x00\x01") is None
