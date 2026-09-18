# How we work

Four people in one repository. The rules below exist so nobody loses work.

**The one rule that matters: never commit directly to `main`.** Everything reaches
`main` through a pull request that a teammate approved. `main` is protected, so a
direct push will be rejected — that rejection is the system working, not a bug.

## Daily loop

```bash
# 1. Start from an up-to-date main
git checkout main
git pull

# 2. Branch. Name it <your-area>/<what-it-does>
git checkout -b audit/credit-rollups

# 3. Work, committing as you go — small commits, not one giant one
git add backend/app/audit/gpa.py
git commit -m "Add major GPA calculation with repeat-course policy"

# 4. Push your branch
git push -u origin audit/credit-rollups

# 5. Open the PR
gh pr create --fill          # or use the link git prints
```

Then ask a teammate to review. After it merges:

```bash
git checkout main
git pull
git branch -d audit/credit-rollups
```

## Branch naming

Prefix with your area so the branch list stays readable:

| Prefix | Owner | Area |
|---|---|---|
| `audit/` | Person 1 | Catalog, audit engine, matcher, prereq DAG |
| `ai/` | Person 2 | LLM provider, tools, prompts, wellbeing |
| `data/` | Person 3 | Models, ingestion, validation, seed data |
| `web/` | Person 4 | Frontend, routers, integration |
| `docs/` | anyone | Documentation only |

## Review expectations

- **One approval** is required to merge. Review within a day so nobody is blocked.
- Reviewing is not rubber-stamping. Pull the branch and run it if the change is
  more than cosmetic.
- Disagreement goes in the PR thread, not in a DM — so the reasoning is recorded
  and the rest of the team can learn from it.

## When two people change the same file

This will happen, and it is normal. Rebase onto current `main` and fix it locally:

```bash
git checkout main && git pull
git checkout your-branch
git rebase main
# resolve conflicts, then:
git add <fixed files>
git rebase --continue
git push --force-with-lease      # NOT --force
```

`--force-with-lease` refuses to push if someone else has pushed to your branch since
you last fetched. Plain `--force` would silently destroy their commits. Use the safe
one, always.

## Before you push: secrets

CI blocks any PR that tracks a `.env` file or matches a credential pattern, but check
yourself too:

```bash
git status                       # is .env showing as untracked? good. tracked? stop.
git diff --cached                # read what you are actually committing
```

Put keys in `backend/.env`, which is gitignored. `.env.example` is the template and
must stay empty of real values.

**If a key ever does get pushed: rotate it immediately.** Deleting the commit is not
enough — assume anything pushed to a shared repo is compromised. Tell the team, then
issue a new key.

## Never commit

- `.env`, API keys, tokens, passwords
- `*.db`, `.venv/`, `node_modules/`, `__pycache__/`
- Real student records. **All data in this project is fictional** — no FERPA-covered
  information enters this repository under any circumstances, including in a
  screenshot or a test fixture.

## Architecture rules CI enforces

- Nothing in `backend/app/catalog/` or `backend/app/audit/` may import the LLM layer.
  `tests/test_no_llm_in_engine.py` fails the build otherwise. If you need model output
  in the engine, the design is wrong — raise it with the team rather than adding an
  exception.
- `ruff check`, `black --check`, and `pytest` must pass before merge.

Run all three locally before opening a PR:

```bash
cd backend
ruff check . && black --check . && pytest -q
```
