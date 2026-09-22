# COSC 490 — Progress Report 1

**Project:** Multimodal Academic Advisor
**Repository:** github.com/rosaiju/multimodal-academic-advisor (private)
**Reporting period:** September 17–22, 2026
**Report date:** September 22, 2026
**State reported:** `main` at commit `13efa15`

---

## 1. What the system does today

A student signs in, uploads a transcript, reviews every extracted row, and
confirms it. A deterministic Python engine then audits that confirmed record
against the Morgan State 2026-2028 Computer Science B.S. catalog and returns
credits earned, which requirement blocks are satisfied, which courses are
eligible next, and which are blocked and by what prerequisite. A conversational
advisor answers questions about that audit in plain English.

The architectural commitment behind the project is that **the language model
never computes degree progress**. A rules engine computes it; the model is
permitted only to rephrase an answer the engine has already written in full, and
any model reply naming a course code outside the catalog is discarded. Because
the engine writes a complete answer on its own, the entire demo runs with no API
key configured — which is what makes it presentable now, while both student
credit applications are still pending.

## 2. Verified progress

All figures below were re-verified against `main` on September 22, 2026, not
carried forward from earlier notes.

| Item | Status |
|---|---|
| Commits on `main` | 47, from September 17 |
| Pull requests | 6 opened, 6 merged, 0 open |
| Backend test suite | **604 passing**, 23 test files |
| Lint / format | `ruff` clean, `black` clean across 77 files |
| Frontend | `oxlint` clean (3 warnings, 0 errors), production build succeeds |
| Continuous integration | Green on `13efa15`: Python 3.11 and 3.14, Node 22 and 24, plus a committed-secret scan |
| Browser walkthrough | 11 of 11 steps pass end to end in Chromium |
| Python source | ~13,500 lines |
| Frontend source | ~1,800 lines |

**Completed components.** Catalog layer with a YAML schema and a `doctor`
validation CLI; the Morgan State 2026-2028 catalog encoded as 56 courses (41
COSC) with a verified prerequisite graph; the degree audit engine; a prerequisite
graph and course recommender; transcript ingestion for plain text, text-layer
PDF, and DegreeWorks exports, with per-row provenance and a mandatory human
confirmation step; account registration and JWT authentication scoping every
student record to its owner; a React dashboard; and the conversational advisor
with four provider integrations (Anthropic, OpenAI, Gemini, Ollama) behind one
interface, added without a single new dependency.

**End-to-end run, verified in a real browser on September 22:** register → upload
→ review → confirm → audit → chat → refusal, 11 of 11 steps, no console errors
and no failed requests other than one 404 that the app issues deliberately to
decide which screen to show. On the sample transcript the audit reports 23.00 of
120 credits (19.2% by credit count), 4 of 6 encoded requirement blocks satisfied,
and COSC220 as the leading recommendation because it unlocks six later courses.

**Honest reporting of gaps is itself a feature.** The system refuses to print a
degree-completion percentage while the encoded catalog is partial, and says why.
Eleven contradictions and ambiguities were found in the published catalog while
encoding it; four are resolved from primary sources and seven are written up in
`docs/catalog-open-questions.md` as questions for the department.

## 3. Team contributions

Four members have repository access or pending invitations. To state this
accurately: **all 47 commits to date were authored by Rohan Sainju**, who built
the catalog layer, audit engine, ingestion pipeline, authentication, frontend,
advisor, and CI configuration, and reviewed and merged all six pull requests.

| Member | Repository status | Code contributed so far |
|---|---|---|
| Rohan Sainju (`rosaiju`) | Owner | 47 commits, 6 PRs |
| `JoshuerMorgan` | Write access, accepted | None yet |
| `Sameer-Shiwakoti` | **Invitation pending since Sep 18 — expires ~Sep 25** | None yet |
| `SawcyD` | **Invitation pending since Sep 21 — expires ~Sep 28** | None yet |

Work has not yet been divided across the team. Doing so is the first item in
the next period, and the two pending invitations are the blocker — GitHub
invitations expire after seven days and disappear without notice.

## 4. Remaining work

**Immediate, blocking.**
1. Two teammates must accept their invitations before they expire.
2. Assign owned workstreams so contributions are distributed and visible.

**Known technical gaps.**
3. **No live LLM provider has ever been called.** All four integrations are
   tested against a local server that replays their documented request and
   response shapes, and every failure path is covered, but no real endpoint has
   ever answered. This is the single largest unverified claim in the project.
   Running a local Ollama model would close it without waiting on credits.
4. **The encoded catalog is partial** — 6 requirement blocks, with 34 of 56
   courses satisfying no encoded block. The 7 open questions need Dr. Wang or
   the department to adjudicate before the remaining blocks can be encoded.
5. **Voice input is not built.** The Web Speech API was in the approved plan.
   The "multimodal" claim currently rests on text and PDF ingestion.
6. **Scanned transcripts require an API key.** Text and text-layer PDFs parse
   deterministically; a photographed transcript returns a 415 naming the reason.
7. **Chat history is in process memory** and is lost on restart.
8. **Nothing is deployed.** Every demo is localhost; each teammate runs both
   servers. There is no URL to share and no deployment is planned yet.
9. Branch protection on `main` is unavailable: rule enforcement on a private
   repository needs GitHub Pro, pending a GitHub Education application.
