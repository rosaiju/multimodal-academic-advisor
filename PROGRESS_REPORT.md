# COSC 490 — Progress Report 1

**Project:** Multimodal Academic Advisor
**Repository:** github.com/rosaiju/multimodal-academic-advisor (private)
**Period:** September 17–22, 2026 · **State reported:** `main` at `13efa15`

## What the system does

A student uploads a transcript, reviews every extracted row, and confirms it. A
deterministic Python engine audits that record against the Morgan State 2026-2028
Computer Science B.S. catalog and reports credits earned, requirement blocks
satisfied, eligible courses, and what each blocked course awaits. A
conversational advisor answers questions about that audit.

The commitment behind the project is that the language model never computes
degree progress. The engine computes it; the model may only rephrase an answer
the engine already wrote, and any reply naming a course outside the catalog is
discarded. An import-graph test fails the build if the AI layer reaches the
catalog or audit packages. Because the engine answers unaided, the demo needs no
API key.

## Verified progress

Re-verified against `main` on September 22: 47 commits, 6 pull requests merged,
**604 backend tests passing**, linters and the frontend build clean, CI green on
Python 3.11/3.14 and Node 22/24, and a scripted browser run passing 11 of 11
steps. Completed: the catalog layer and validation CLI, the Morgan catalog
encoded as 56 courses with a verified prerequisite graph, the audit engine,
transcript ingestion with per-row provenance and mandatory human confirmation,
authentication, the React dashboard, and the advisor with four provider
integrations.

## Team progress

All 47 commits to date were authored by Rohan Sainju. Of four members, two have
repository access and two invitations are unaccepted, one expiring about
September 25. Dividing owned workstreams is the next step and is blocked on that.

## Remaining work

No live LLM provider has ever been called; the integrations are tested only
against a local replay server, and this is the largest unverified claim. The
encoded catalog is partial — 6 requirement blocks, 7 open questions awaiting
department adjudication. Voice input is not built, so the multimodal claim rests
on text and PDF ingestion. Nothing is deployed.
