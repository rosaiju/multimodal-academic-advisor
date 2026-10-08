"""Compare speech providers on the phrases that matter for advising.

Pure logic lives here so it can be tested without audio or a network; the CLI is
scripts/speech_benchmark.py. A recording is a file named `<phrase id>.<ext>` in
the recordings folder (git-ignored: they are someone's voice).

A phrase passes when every course code it is *meant* to contain appears in the
normalized transcript. Normalization is the production one
(app.speech.course_codes), so this measures what the student would actually see.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from app.speech.base import SpeechError, Transcript
from app.speech.course_codes import normalize_course_mentions


@dataclass(frozen=True)
class Phrase:
    id: str
    say: str
    expect: tuple[str, ...]  # catalog codes the transcript should contain


PHRASES: tuple[Phrase, ...] = (
    Phrase("cs-459", "computer science four fifty nine", ("COSC459",)),
    Phrase("cosc-243", "COSC two forty three", ("COSC243",)),
    Phrase("spelled-459", "C O S C four five nine", ("COSC459",)),
    Phrase("can-i-take-354", "Can I take computer science three fifty four?", ("COSC354",)),
    Phrase("before-241", "What do I need before COSC two forty one?", ("COSC241",)),
    Phrase("after-220", "What classes can I take after computer science two twenty?", ("COSC220",)),
    Phrase("math-141", "MATH one forty one", ("MATH141",)),
    Phrase("cloud-261", "cloud computing two sixty one", ("CLCO261",)),
)

MIME_BY_SUFFIX = {
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


@dataclass(frozen=True)
class Result:
    provider: str
    model: str
    phrase_id: str
    said: str
    expected: tuple[str, ...]
    ok: bool  # the call succeeded
    raw: str
    normalized: str
    recognized: bool  # every expected code is in `normalized`
    latency_ms: int
    confidence: float | None
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _squash(text: str) -> str:
    return text.upper().replace(" ", "")


def recognized(normalized: str, expect: Iterable[str]) -> bool:
    flat = _squash(normalized)
    return all(_squash(code) in flat for code in expect)


def find_recordings(folder: Path) -> dict[str, Path]:
    """phrase id -> recording, for the phrases that have one."""
    wanted = {p.id for p in PHRASES}
    found: dict[str, Path] = {}
    for path in sorted(folder.glob("*")) if folder.is_dir() else []:
        if path.stem in wanted and path.suffix.lower() in MIME_BY_SUFFIX:
            found[path.stem] = path
    return found


def run_one(
    transcribe: Callable[..., Transcript],
    phrase: Phrase,
    audio: Path,
    *,
    provider: str,
    model: str,
    catalog_codes: Sequence[str],
    keyterms: Sequence[str] = (),
) -> Result:
    started = time.perf_counter()
    try:
        heard = transcribe(audio.read_bytes(), MIME_BY_SUFFIX[audio.suffix.lower()], keyterms)
    except SpeechError as exc:
        ms = round((time.perf_counter() - started) * 1000)
        return Result(
            provider, model, phrase.id, phrase.say, phrase.expect,
            False, "", "", False, ms, None, f"{exc.kind}: {exc}",
        )  # fmt: skip
    ms = round((time.perf_counter() - started) * 1000)
    normalized = normalize_course_mentions(heard.text, catalog_codes)
    return Result(
        provider, model, phrase.id, phrase.say, phrase.expect,
        True, heard.text, normalized, recognized(normalized, phrase.expect),
        ms, heard.confidence or None,
    )  # fmt: skip


def report(results: Sequence[Result]) -> str:
    """Markdown: a per-provider summary, then every phrase side by side."""
    lines = [
        "# Speech provider benchmark",
        "",
        "| provider | model | recognized | succeeded | median ms | mean confidence |",
        "|---|---|---|---|---|---|",
    ]
    for provider, model in sorted({(r.provider, r.model) for r in results}):
        rs = [r for r in results if (r.provider, r.model) == (provider, model)]
        done = [r for r in rs if r.ok]
        times = sorted(r.latency_ms for r in done)
        conf = [r.confidence for r in done if r.confidence is not None]
        median = times[len(times) // 2] if times else "-"
        mean = f"{sum(conf) / len(conf):.2f}" if conf else "n/a"
        lines.append(
            f"| {provider} | {model} | {sum(r.recognized for r in rs)}/{len(rs)} "
            f"| {len(done)}/{len(rs)} | {median} | {mean} |"
        )
    lines += [
        "",
        "## Per phrase",
        "",
        "| phrase | provider | heard | normalized | code? | ms | conf |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: (r.phrase_id, r.provider)):
        heard = r.raw if r.ok else f"FAILED ({r.error})"
        conf = f"{r.confidence:.2f}" if r.confidence is not None else "n/a"
        lines.append(
            f"| {r.said} | {r.provider} | {heard} | {r.normalized} "
            f"| {'yes' if r.recognized else 'NO'} | {r.latency_ms} | {conf} |"
        )
    return "\n".join(lines) + "\n"
