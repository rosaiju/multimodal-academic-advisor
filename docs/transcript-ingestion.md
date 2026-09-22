# Transcript Ingestion

How a student's transcript becomes coursework a degree audit will count, and the
rules that govern each step.

## The flow

```
  upload                  review + accept              audit
    │                           │                        │
    ▼                           ▼                        ▼
POST /ingest/transcript   POST /ingest/confirm   GET /students/{id}/audit
    │                           │                        │
 extracted rows            confirmed rows          AuditResult
 UNVERIFIED_EXTRACTION     STUDENT_CONFIRMED       computed, never generated
 stored: false             written to disk
```

Upload and confirm are **separate calls on purpose**. Uploading stores nothing —
the response says so in a field. One combined "upload and apply" endpoint would be
friendlier and would make the provenance rule meaningless.

## Extractors

Registered in preference order. The first one that can read a given document wins;
if it *declines* a specific document (`DocumentNotReadable`) the next one tries.

| Extractor | Reads | Confidence ceiling |
|---|---|---|
| `text-parser` | `.txt`, `.csv`, `.md`, any `text/*` | HIGH |
| `pdf-text-parser` | PDFs **with a text layer** | HIGH |
| `vision:<model>` | images, scanned PDFs | **MEDIUM** |

**A deterministic reader always gets first refusal.** A registrar-exported PDF
contains the real characters; handing it to a vision model is strictly worse, so
`pdf-text-parser` sits ahead of any model. A scanned PDF has no text layer, so it
declines and falls through.

`GET /health` reports which extractors are live and whether scans are accepted.
Without an API key the vision extractor is simply absent — the app starts normally
and text transcripts keep working.

## Provenance

```
UNVERIFIED_EXTRACTION  ──confirm──▶  STUDENT_CONFIRMED  ──▶  audit engine
        │
        └── cannot reach an audit, by construction
```

Three independent checks enforce this, which is deliberate — they protect the same
thing from different directions:

1. `ExtractedCourse.provenance` is fixed at `UNVERIFIED_EXTRACTION` regardless of
   what produced the row.
2. `RecordStore.load()` refuses a file containing anything not
   `STUDENT_CONFIRMED`. A record file is editable on disk; being in the folder is
   not trust.
3. The audit engine filters on `TRUSTED_FOR_AUDIT` when reading any record.

## Confidence

Three coarse buckets, never a float. A score like `0.82` invites a UI to
auto-accept above a threshold, and every auto-accepted row is a course added to
someone's degree audit without them looking.

- **HIGH** — every field read exactly, course exists in the catalog
- **MEDIUM** — needs a human eye (unknown code, odd credits, *or read by a model*)
- **LOW** — a required field is missing

**A model row is never HIGH.** The parser earns HIGH because a regex that matched
a line will match it identically forever. A model reading pixels is making a claim,
and a tidy claim is still a claim. Capping at MEDIUM puts every model row in
`needs_review`, which is what "never silently auto-confirm" means in practice.

Every downgrade names its reason in words a student can act on.

## What extraction never does

- **Never repairs a course code.** An unrecognised code is flagged as-is. Rewriting
  it to a similar catalog code would put a course on a record the student never took.
- **Never fills a missing field.** `confirm` raises on an incomplete row rather
  than defaulting a grade or credit value, because a guess here decides a degree.
- **Never bulk-accepts.** There is no `confirm_all` helper, and a test asserts none
  appears.

## Corrections

A student can fix a misread field while confirming. The extracted row is sent back
**verbatim** and the correction travels beside it:

```json
{"extracted": {"code": "COSC111", "grade": "A", ...}, "grade": "B"}
```

Both values are stored. Sending a single pre-edited row would make a correction
indistinguishable from an accurate read, and the record would lose the fact that
the extractor got it wrong — which is also how you find out an extractor is bad.

## Where the LLM lives

```
app/llm/          ──registers itself with──▶  app/ingestion/
                                                    │
app/audit/  ◀── never imports either ───────────────┘
```

`app/ingestion/` defines the `TranscriptExtractor` protocol and knows nothing about
models. `app/llm/registration.py` registers an implementation at startup. Tests
walk every module in both packages asserting the imports do not appear.

## Student records

One JSON file per student under `student_record_dir` — files rather than a table so
a person can open a record and see exactly what the system believes and where each
row came from. Each entry keeps its source document, extractor, raw transcript line,
and any correction.

Re-uploading the same transcript **replaces** rows by course code rather than
appending, so it cannot double a student's coursework. A genuine retake is two rows
with the same code in one upload, and the engine counts it once.

## Known gaps

- No OCR fallback for scans when no API key is configured — they return 415 with
  guidance rather than failing silently.
- `pymupdf` is declared but unused; `pdfplumber` does the work.
- Term formats other than `"Fall 2024"` style are not recognised by the text
  parser; a student supplies the term at confirmation instead.
