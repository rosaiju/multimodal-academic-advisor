# Academic Advisor — Frontend

React + Vite + Tailwind. Three screens matching the backend's own shape: upload
(stores nothing), review and confirm (the only way data becomes trusted), then
the degree audit and advising plan.

## Running it

Two terminals.

**Terminal 1 — backend** (from `backend/`):

    .venv/Scripts/python -m uvicorn app.main:app --reload

**Terminal 2 — frontend** (from `frontend/`):

    npm install      # first time only
    npm run dev

Open **http://localhost:5173**

Vite proxies `/api/*` to `http://127.0.0.1:8000`, so the browser makes
same-origin requests and CORS never applies.

## No mock data

Every value on screen comes from a backend endpoint. If the backend is not
running the app says so and shows nothing — inventing coursework would defeat a
system built around telling verified data apart from guesses.

| Screen | Endpoints |
|---|---|
| Upload | `GET /health`, `GET /programs`, `POST /ingest/transcript` |
| Review | `POST /ingest/confirm` |
| Dashboard | `GET /students/{id}/audit/summary`, `GET /students/{id}/plan` |

## Known gaps

- **No authentication.** The student id is hardcoded to `demo-student`. The
  backend takes a raw id with no auth, flagged in PR #3.
- Scanned and photographed transcripts need an API key on the backend. Without
  one the upload screen says so, and text and PDF still work.

## Sample transcript

`data/samples/demo_transcript.txt` — seven courses across two terms.
