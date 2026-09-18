# Multimodal AI Academic Advisor

COSC 490 Senior Capstone — Morgan State University

A multimodal system that acts as a university academic advisor. It handles text,
voice, uploaded transcripts, and academic records, and answers questions about
degree progress, course selection, and student concerns.

> **All student data in this repository is fictional.** No real academic records are
> used at any point. See [Data policy](#data-policy).

## The core design decision

**The language model never computes degree progress. A deterministic Python rules
engine does.**

The LLM handles conversation, document extraction, and explanation. It cannot state
an academic fact unless it obtained that fact by calling a tool backed by the rules
engine. Every claim shown to a student carries a provenance tag saying where it came
from.

This is enforced mechanically, not by convention:

- `app/catalog/` and `app/audit/` are forbidden from importing the AI layer.
  `tests/test_no_llm_in_engine.py` fails the build if anyone does.
- Degree requirements live in git-tracked, schema-validated YAML. The model cannot
  modify them.
- AI-extracted transcript data cannot reach the audit engine until a human confirms it.

A consequence worth noting: **the dashboard and degree audit run with zero LLM calls.**
An expired API key cannot break the core demo.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Repo, config, catalog schema + loader, `AuditResult` contract | **Done** |
| 1 | Audit engine, matcher, prereq DAG, dashboard, chat with tools | Not started |
| 2 | Transcript upload, vision extraction, voice, planner, wellbeing | Not started |
| 3 | What-if simulation, multi-term planning, optional extras | Not started |

Full plan, team split, and roadmap: [`docs/`](docs/) and the project plan document.

## Quick start

Requires Python 3.11–3.13. (3.14 works for the core, but several ingestion
dependencies still lag — pin 3.12 or 3.13 for the team.)

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"

cp ../.env.example .env         # optional — the audit engine runs without any API key

pytest -q
uvicorn app.main:app --reload
```

Then open <http://localhost:8000/docs> for the auto-generated API explorer, or
<http://localhost:8000/health> to confirm the catalog loaded.

## Layout

```
backend/app/
  catalog/     Degree requirements: YAML schema, loader, registry.   NO LLM.
  audit/       The rules engine: blocks, matcher, prereq DAG.        NO LLM.
  ingestion/   PDF and image transcript extraction + validation.
  llm/         Provider-neutral LLM interface (Anthropic / OpenAI).
  advisor/     Conversation, tool-calling loop, wellbeing triage.
  schemas/     Pydantic contracts shared across the whole system.
data/catalog/  The degree catalogs themselves. Source of truth.
frontend/      React + Vite + Tailwind.
```

## Adding a degree program

Drop a new YAML file in `data/catalog/`. No code changes. The loader validates it at
startup and refuses to start on a typo — a course code named in a requirement block
that does not exist in the course list is a fatal error, not a warning.

See `data/catalog/demo_university_cs.yaml` for the format and
`app/catalog/schema.py` for the full field reference.

## Provenance tags

| Tag | Means | Can affect a degree audit? |
|---|---|---|
| `verified` | From the catalog or computed by the engine | Yes |
| `student_confirmed` | AI-extracted, then confirmed by the student | Yes |
| `ai_suggested` | LLM advice or phrasing | No |
| `unverified_extraction` | AI-extracted, not yet confirmed | **No** |

## Data policy

Student records are generated fixtures. The repository contains no FERPA-covered
data and none should ever be added. Do not upload a real transcript to a development
instance.

The wellbeing feature provides **referrals only** — it does not diagnose, assess
severity, or offer treatment. Crisis-keyword matches bypass the language model
entirely and return a fixed referral card.

## Team

| Owner | Area |
|---|---|
| Person 1 | Degree audit engine, catalog encoding, matcher, prereq DAG |
| Person 2 | LLM provider layer, tool-calling, prompts, wellbeing rules |
| Person 3 | Data models, transcript ingestion, validation, seed data |
| Person 4 | React frontend, API routers, integration, deployment |

Branch per feature, PR reviewed by one teammate, no direct commits to `main`.
Weekly integration session where the demo must run end to end.
