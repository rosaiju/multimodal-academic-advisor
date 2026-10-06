"""Live smoke test: ask a REAL chat model to phrase the advisor's answers.

Nothing here is mocked. It builds a synthetic student record in memory (no
account, no file under student_records/), runs the same `build_facts()` the chat
route runs, and calls `ask()` with a real provider. Every reply is then checked
independently of the advisor's own guard:

- did a live model actually produce the text (source == "engine+llm"), or did
  the advisor fall back to the engine's text?
- are the engine's credit figures, eligible courses, blockers and in-progress
  courses still present, and is the partial-catalog warning still there?

Usage (from backend/):

    python scripts/live_llm_smoke.py --provider ollama --model qwen2.5:7b
    python scripts/live_llm_smoke.py --provider gemini          # needs GEMINI_API_KEY

Exit code is 0 only when the provider was reachable and every reply passed the
independent checks. A run where the model was never used is reported as such,
never as a pass.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.advisor.chat import ask  # noqa: E402
from app.advisor.consistency import check_rephrasing  # noqa: E402
from app.advisor.facts import build_facts  # noqa: E402
from app.audit.record import CompletedCourse, StudentRecord  # noqa: E402
from app.catalog.loader import load_program  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.llm.chat import get_chat_provider  # noqa: E402
from app.schemas.provenance import Provenance  # noqa: E402

PROGRAM_FILE = "morgan_cosc_bs_2026_2028.yaml"

#: A made-up student. COSC 220 is in progress, which blocks COSC 352 and 354.
SYNTHETIC_COURSES = [
    ("COSC111", "Fall 2025", "A", "4"),
    ("COSC112", "Spring 2026", "B", "4"),
    ("MATH141", "Spring 2026", "B", "4"),
    ("ENGL101", "Fall 2025", "A", "3"),
    ("COSC220", "Fall 2026", "IP", "4"),
]

QUESTIONS = [
    ("remaining", "What do I still need to graduate?"),
    ("eligible", "What should I take next semester?"),
    ("blocker", "Can I take COSC 354?"),
    ("in_progress", "What courses am I taking right now?"),
    ("credits", "How many credits am I missing?"),
    ("unsupported", "Who is the best professor for COSC 241 and what is the parking policy?"),
]


def synthetic_record() -> StudentRecord:
    return StudentRecord(
        student_id="synthetic-smoke-student",
        completed=[
            CompletedCourse(
                code=code,
                term=term,
                grade=grade,
                credits=Decimal(credits),
                provenance=Provenance.STUDENT_CONFIRMED,
                institution="Morgan State University",
            )
            for code, term, grade, credits in SYNTHETIC_COURSES
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default="ollama", choices=["ollama", "gemini"])
    parser.add_argument("--model", help="model name; defaults to the configured one")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--out", help="write the full results as JSON here")
    args = parser.parse_args()

    base = get_settings()
    overrides: dict = {"llm_provider": args.provider}
    if args.model:
        overrides[f"{args.provider}_chat_model"] = args.model
    if args.provider == "ollama":
        overrides["ollama_timeout_seconds"] = args.timeout
    settings = Settings(**{**base.model_dump(), **overrides})
    provider = get_chat_provider(settings)

    usable, reason = provider.available()
    print(f"provider={provider.provider_id} model={provider.name} available={usable} ({reason})")
    if not usable:
        print("LIVE VERIFICATION BLOCKED: provider not available. Nothing was tested live.")
        return 2

    program = load_program(settings.catalog_dir / PROGRAM_FILE)
    facts = build_facts(program, synthetic_record())

    results = []
    live = 0
    failures = 0
    for label, question in QUESTIONS:
        started = time.perf_counter()
        reply = ask(
            facts,
            question,
            student_id=f"smoke-{label}",
            conversation_id=f"smoke-{time.time_ns()}",
            provider=provider,
        )
        elapsed = time.perf_counter() - started
        # Independent re-check: the advisor's own guard decides what it ships, this
        # decides whether the run passes. For an engine fallback the raw model text
        # is not returned, so the check runs on what the student saw.
        problems = (
            check_rephrasing(reply.prepared, reply.answer, facts, question=question)
            if reply.source == "engine+llm"
            else []
        )
        live += reply.source == "engine+llm"
        failures += bool(problems)
        results.append(
            {
                "label": label,
                "question": question,
                "source": reply.source,
                "model": reply.model,
                "intent": str(reply.intent),
                "seconds": round(elapsed, 1),
                "notice": reply.notice,
                "independent_check_problems": problems,
                "answer": reply.answer,
                "prepared": reply.prepared,
                "discarded_model_text": reply.discarded,
            }
        )
        status = "LIVE" if reply.source == "engine+llm" else "FALLBACK"
        print(f"\n=== [{label}] {question}\n--- {status} in {elapsed:.1f}s, intent={reply.intent}")
        if reply.notice:
            print(f"notice: {reply.notice}")
        print(reply.answer)
        if reply.discarded:
            print(f"--- discarded model text:\n{reply.discarded}")
        if problems:
            print(f"!!! independent check: {problems}")

    print(
        f"\nSUMMARY: {live}/{len(QUESTIONS)} answered by the live model, "
        f"{len(QUESTIONS) - live} fell back to the engine, {failures} failed re-check."
    )
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"full results: {args.out}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
