# Trial Preparation Automation — Working Prototype

A runnable implementation of the trial-prep workflow: upload a folder of case
documents, get a structured, source-cited trial preparation report, with a
mandatory attorney review gate before anything is exported.

This is the local-prototype build discussed in the design spec (see the
published artifact from this conversation for the full architecture,
diagrams, and production deployment guidance). The pipeline itself is model-
agnostic orchestration code (`run_pipeline.py` / `approve_and_export.py`
step through ingest -> analysis -> report -> review gate -> export
explicitly and deterministically); `pipeline/config.py` supports two model
providers, switched with `MODEL_PROVIDER` in `.env`:

- **`huggingface`** (default) — HF's OpenAI-compatible router in front of
  `prism-ml/Ternary-Bonsai-27B-AWQ-4bit`, the one $0-priced text+vision
  model on the router as of this build (verified working, including OCR).
  Needs an HF token with a small prepaid balance — the *model* is free,
  but HF's own monthly included-credits allowance is easy to exhaust, and
  `canPay` must be `true` on the account (check via
  `huggingface.co/api/whoami-v2`) for any router model to respond at all.
  It's a heavy chain-of-thought reasoner: expect ~15-20 min for a
  6-document case, and note the `REASONING_TOKEN_FLOOR` in
  `pipeline/config.py` that budgets extra tokens for its reasoning
  overhead. Also not every backend behind the router guarantees
  schema-valid JSON, so this path uses a prompt-embedded schema + parse +
  one repair retry rather than a hard guarantee.
- **`openai`** — native Structured Outputs, schema-guaranteed JSON, and
  meaningfully faster. Needs a funded `OPENAI_API_KEY`.

## What's real vs. stubbed in this build

| Piece | Status |
|---|---|
| Classification, extraction, timeline, evidence matrix, witness analysis, report drafting | **Real** — live API calls, JSON-schema enforced (guaranteed under `openai`, best-effort + retry under `huggingface`) |
| OCR | **Real** — scanned pages/images are sent to the model's vision endpoint directly for transcription (no Tesseract/system dependency needed; requires a vision-capable model) |
| Legal research (RAG) | **Real retrieval mechanics**, **demo corpus** — TF-IDF retrieval over `legal_corpus/sample_provisions.json` (9 real, well-known IPC/CrPC/Evidence Act provisions). `legal_corpus/precedents.json` ships empty on purpose: the system never fabricates a case citation, so with no precedent corpus indexed it will honestly report "no matching precedent found" rather than inventing one. Wire `pipeline/legal_index.py::retrieve()` to a real vector DB over a licensed corpus (Indian Kanoon / SCC Online / Manupatra) for production use. |
| DOCX / PDF export | **Real** |
| Notification | **Real, local-only** — macOS notification + log line. Swap `pipeline/notify.py::send()` for an email/Slack call in production. |
| Auth, multi-tenant storage, cloud deployment | **Not built** — out of scope for a local prototype; see the deployment section of the design spec artifact. |

## Setup

```bash
cd trial_prep
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env and add your HF_API_TOKEN (or switch to openai)
```

## Run it

A `samples/case_demo/` folder is included: a fictional assault case (FIR,
charge sheet, two witness statements with a deliberate timing contradiction,
a medical report, and prior hearing notes — no bail order document, on
purpose, to exercise the "missing documents" detection).

```bash
# Stage 1: run the pipeline through report draft (stops at the review gate)
python run_pipeline.py samples/case_demo --case-id demo1

# Read the draft
open output/demo1/draft_report.md   # or just cat it

# Stage 2: after review, either approve and export, or send back for revision
python approve_and_export.py demo1 --reviewer "Your Name" --approve
# or:
python approve_and_export.py demo1 --reviewer "Your Name" --revise "check the timing contradiction claim"
```

Approving produces `output/demo1/trial_prep_report.docx` and `.pdf`, both
watermarked with review status, and fires a local notification.

To run it on a real case, point `run_pipeline.py` at a folder containing
your own PDFs/DOCX/TXT/images instead of `samples/case_demo`.

## Web frontend

A small Flask app wraps the same pipeline for browser use — upload,
processing status, the rendered report, and the review gate, without
touching the CLI.

```bash
python webapp/app.py
# open http://127.0.0.1:5050
```

- **Home page**: upload multiple documents (optionally name the case) to
  start a run, and see every case's status at a glance.
- **Case page**: while processing, auto-refreshes every few seconds and
  shows the live audit-log tail; once a draft is ready, renders the full
  report (documents, timeline, evidence matrix, witness cards with
  cross-exam questions, strengths/weaknesses/missing docs/risks,
  applicable laws) with **Approve & export** and **Request revision**
  actions; once approved, shows DOCX/PDF download links.

It's a thin wrapper, not a second implementation: uploads run
`run_pipeline.run_case()` on a background thread (the AI stages are slow),
revisions run `approve_and_export.revise_case()` the same way, and
approval runs `approve_and_export.approve_case()` inline since export is
fast, local, non-AI work. A case's UI state (`processing` / `draft_ready` /
`approved` / `error`) is derived from the same `case_file.json`, the audit
log, and a `.processing` lock file in `output/<case_id>/` — there's no
separate state store to drift out of sync with the CLI path. This is a
single-process, single-user dev server (`app.run(debug=True)`); it is not
meant to be exposed beyond localhost as-is.

## Architecture

```
pipeline/
  config.py           API client + call_structured() — the shared
                       schema-enforced + validate + one-retry helper every
                       AI stage uses (this is the error-handling backbone)
  audit.py             JSONL audit log per case_id, in logs/
  ingest.py             upload -> file-type routing -> OCR / native
                         extraction -> normalization
  classify.py            AI classification
  extract.py              AI structured extraction
  aggregate.py             case_file assembly (append-only enrichment)
  timeline.py               AI timeline generator
  evidence.py                AI evidence matrix builder
  witness.py                  AI witness analyzer
  legal_index.py                TF-IDF retrieval (swap for real vector DB)
  legal_research.py              AI query generation + RAG synthesis
  report.py                       AI report composer (narrative sections only)
  export.py                        DOCX / PDF rendering
  notify.py                         local notification
  persistence.py                    case_file.json save/load + the
                                     .processing lock used by the webapp
  status.py                         derives processing/draft_ready/
                                     approved/error from the files above

run_pipeline.py     stages 1-9: ingest through report draft
approve_and_export.py   stages 10-11: human review gate -> export -> notify
webapp/app.py        Flask frontend -- calls the same functions above
webapp/templates/    Jinja templates (upload/list, case detail)
webapp/static/        stylesheet
```

Every document record is append-only enriched (never mutated) so any claim
in the final report can be traced back to a `doc_id` + page reference — see
`logs/<case_id>.jsonl` for the full audit trail of what happened to each
document, including OCR/classification confidence flags and any stage that
fell back to "needs review" instead of guessing.

## Security note

This prototype is **not** hardened for real client data: no encryption at
rest beyond the filesystem's own, no access control (the webapp has no
login and trusts anyone who can reach it), and it calls a third-party API
directly rather than through a zero-data-retention enterprise agreement.
Do not point it at real privileged case files, and do not expose the
webapp beyond localhost, without first addressing the security/privacy
section of the design spec (encryption, RBAC, DPDP Act 2023 alignment,
retention policy, ZDR API tier).
