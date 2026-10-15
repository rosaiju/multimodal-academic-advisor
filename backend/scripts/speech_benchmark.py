"""Run recorded phrases through speech providers and write a comparison report.

Record each phrase yourself (any app; webm, ogg, m4a, mp3 or wav) and save it as
`backend/benchmarks/recordings/<phrase id>.<ext>`. Recordings and reports are
git-ignored. Run from backend/:

    python scripts/speech_benchmark.py --list
    python scripts/speech_benchmark.py --providers deepgram openai

Each provider needs its own key in .env (DEEPGRAM_API_KEY, OPENAI_API_KEY). A
provider with no key is reported as skipped, never as a pass. Nothing is sent
anywhere for a provider you do not list.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.catalog.registry import registry as catalog  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.speech import registry as speech  # noqa: E402
from app.speech.benchmark import PHRASES, find_recordings, report, run_one  # noqa: E402
from app.speech.keyterms import spoken_course_codes  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--providers", nargs="+", default=["deepgram"])
    ap.add_argument("--recordings", type=Path, default=BACKEND / "benchmarks" / "recordings")
    ap.add_argument("--out", type=Path, default=BACKEND / "benchmarks" / "reports")
    ap.add_argument("--list", action="store_true", help="show phrases and which are recorded")
    args = ap.parse_args()

    found = find_recordings(args.recordings)
    if args.list:
        for p in PHRASES:
            mark = "recorded" if p.id in found else "missing "
            print(f"[{mark}] {p.id:<16} say: {p.say}")
        return 0
    if not found:
        print(f"No recordings in {args.recordings}. Run with --list for what to record.")
        return 1

    base = get_settings()
    catalog.load(base.catalog_dir)
    programs = catalog.list_programs()
    codes = [c.code for p in programs for c in p.courses]
    keyterms = spoken_course_codes(programs)

    results = []
    for name in args.providers:
        settings = base.model_copy(update={"speech_provider": name})
        usable, why = speech.availability(settings)
        if not usable:
            print(f"skipped {name}: {why}")
            continue
        model = speech.create_live_provider(settings).model

        def transcribe(audio, mime, terms, _s=settings):
            return speech.transcribe(audio, mime, terms, settings=_s)

        for phrase in PHRASES:
            if phrase.id in found:
                results.append(
                    run_one(
                        transcribe, phrase, found[phrase.id], provider=name, model=model,
                        catalog_codes=codes, keyterms=keyterms,
                    )  # fmt: skip
                )
    if not results:
        print("Nothing ran.")
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "latest.md").write_text(report(results), encoding="utf-8")
    (args.out / "latest.json").write_text(
        json.dumps([r.as_dict() for r in results], indent=2), encoding="utf-8"
    )
    print(report(results))
    print(f"Written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
