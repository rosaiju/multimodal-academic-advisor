## What this changes

<!-- One or two sentences. What does this PR do, and why? -->

## How I tested it

<!-- Commands run, or what you clicked through. "It compiles" is not testing. -->

- [ ] `cd backend && ruff check . && black --check . && pytest -q` passes locally

## Checklist

- [ ] No `.env`, API keys, or credentials in this diff
- [ ] No real student data (everything is fictional)
- [ ] Nothing in `app/catalog/` or `app/audit/` imports the LLM layer
- [ ] I read my own `git diff` before opening this

## Notes for the reviewer

<!-- Anything you are unsure about, or want a second opinion on. Optional. -->
