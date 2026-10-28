# Resilient AI — Implementation Blueprint

**SIH 2026 · AI-Powered Application Validation, Document Authentication & Regulatory Compliance Verification (AICTE)**

Resilient AI is an evidence-first, human-reviewed scrutiny platform for AICTE institutional
applications (initially Extension of Approval — EoA). It ingests application documents,
extracts structured data, checks completeness and consistency, evaluates versioned regulatory
rules, detects duplicate/anomalous documents, verifies documents against (simulated, clearly
labelled) issuing-authority sources, and presents evidence-linked findings to authorized human
reviewers. **It never makes final regulatory decisions.**

## Core principles

1. **Compliance ≠ Authenticity ≠ Review status.** These are three separate outputs:
   - *Compliance*: does the application satisfy the applicable requirement?
   - *Authenticity*: is there evidence the document is issued by the claimed authority, or signs of alteration?
   - *Review status*: is there enough reliable evidence to resolve the case, or must a human investigate?
2. **Evidence-linked everything.** Every finding references its documents, pages, extracted
   fields, the exact rule version and the evaluator used.
3. **Deterministic first.** Dates, identifiers, numeric thresholds and rule logic are evaluated
   by deterministic code. Gemini is an optional assistant for classification and
   normalization — never the authority for compliance decisions.
4. **Graceful degradation.** The system must remain usable when Gemini is unavailable or
   rate-limited; every cap is labelled (simulated / unavailable / manual review).

## Stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 15 (App Router), TypeScript, Tailwind CSS, Lucide icons |
| Backend | FastAPI (Python 3.11), Pydantic v2, SQLAlchemy 2 |
| Database | Supabase PostgreSQL (`spnnwmlkanpwwdqutitr.supabase.co`) |
| Auth | Backend-managed sessions (bcrypt + JWT + `user_sessions`), role-based authorization |
| Storage | Private local storage (`backend/var/storage`) — Supabase Storage adapter defined for later |
| Document processing | PyMuPDF (text/pages/render), pikepdf (metadata), RapidOCR (scans), OpenCV + ImageHash (dupes/ELA-style analysis) |
| AI | Gemini API (configurable model, structured-output assist, graceful fallback) |
| Reports | ReportLab PDF |
| Tests | pytest + httpx integration tests |

## Architecture

Modular monolith:

```
Institution/Reviewer ──► Next.js UI
                              │  (JWT session, role-aware routes)
                              ▼
                     FastAPI (routers → services)
        ┌──────────┬──────────┬──────────┬───────────┬──────────┐
        ▼          ▼          ▼          ▼           ▼          ▼
   storage     extraction  checks     rule_engine  anomaly   verification
   (files,     (PyMuPDF/  (complete   (versioned   (sha256,  (simulated adapters,
    sha256)    RapidOCR,   + consist-  rules in    phash,    reference_records)
               Gemini)     ency)       Supabase)   metadata)
        └──────────┴──────────┴────┬─────┴───────────┴──────────┘
                                   ▼
                       Supabase PostgreSQL (2-stage validation runs,
                       findings + evidence, review actions, audit chain)
```

## Database

31 tables (see `backend/migrations/`). Highlights:

- `profiles` / `user_sessions` — users (admin, reviewer, institution, auditor) and sessions.
- `institutions` / `institution_memberships` — synthetic demo institutions.
- `applications` + `application_status_history` — workflow status with full transition log.
- `documents` / `document_versions` / `document_pages` — immutable versions; pages carry text,
  OCR lines with normalized bounding boxes and confidence.
- `extracted_fields` / `field_corrections` — extraction with page/box evidence and a correction
  history (original is preserved, never overwritten).
- `compliance_rules` / `rule_versions` — **immutable versions** (DB trigger blocks updates).
- `validation_runs` / `rule_evaluations` — every evaluation stores rule_version_id, inputs, evidence.
- `findings` / `finding_evidence` / `review_actions` — fingerprinted findings (dedup beyond runs)
  with evidence links and a full reviewer action log.
- `verification_providers` / `reference_records` / `verification_attempts` — adapter pattern with
  explicit statuses: verified · mismatch · expired · pending · unavailable · not_supported · manual_review_required.
- `infrastructure_evidence`, `report_records`, `audit_events` (append-only, SHA-256 hash chain,
  `verify_audit_chain()` detects tampering; audit rows are update/delete protected by trigger).

## API surface (v1)

`/api/v1/auth` (login/logout/me) · `/api/v1/institutions` · `/api/v1/applications`
(+ `/submit`, `/documents`, `/validate`, `/findings`, `/verify`, `/reports`, `/history`)
· `/api/v1/documents/{id}` (+ `/stream`, `/extractions`, `/process`, `/reprocess`)
· `/api/v1/findings/{id}/review` · `/api/v1/rules` (+ create version) · `/api/v1/audit`
· `/api/v1/dashboard/summary` · `/api/v1/system/health` · seed/reset (`/api/v1/system/seed`).

## Processing pipeline (per document)

1. **Validate & store** — magic-byte check, size/type limits, generated storage name, SHA-256.
2. **Text layer** — PyMuPDF per page; if pages have little real text → RapidOCR with
   line-level boxes + confidence (`document_pages.lines`).
3. **Classify** — keyword rules first; Gemini assist (optional) with fallback to declared category.
4. **Extract fields** — expected_fields from category + deterministic patterns (+ Gemini assist);
   store raw/normalized value, confidence, page, bbox, method.
5. **Forensics** — exact duplicates (SHA-256 across whole system), near-duplicate page analysis
   (perceptual hash), PDF metadata (producer/editor fingerprints, timestamps), ELA-style
   re-save diff on rendered pages (investigative aid only).
6. **Infrastructure analysis** (for infra evidence) — basic image quality/duplicate detection;
   flags "needs more evidence / inspection recommended". Advisory only.

## Validation run (per application)

1. **Completeness** — required/conditional docs per category checklist, duplicate coverage, expired
   documents (date rules), missing required fields, unreadable documents.
2. **Consistency** — fuzzy name matching (RapidFuzz) where appropriate, exact matching for
   identifiers/dates/numbers; conflicts become findings with source A/B evidence.
3. **Rules** — versioned rule engine (completeness/expiry/consistency/authenticity/verification/
   infrastructure). Outcomes: pass · fail · needs_review · not_applicable · unable_to_evaluate.
   Demo rules are labelled `is_demo_rule` until verified against the official handbook.
4. **Findings** — upserted by deterministic fingerprint; evidence rows attached; prior reviewer
   dispositions are preserved (auto-resolve only in controlled cases).
5. **Verification** — simulated adapters against synthetic `reference_records`; results labelled
   `is_simulated` everywhere.

## Seed dataset (demo-critical)

Synthetic institutions + applications covering: complete case, missing document, expired
certificate, cross-document mismatch, exact duplicate, version-difference anomaly, verification
unavailable, simulated verification, low-quality scan, permission boundary.

## Security

Backend-managed roles + authorization checks on every endpoint; private storage with
authorized streaming only; magic-byte validation; generated filenames; append-only audit with
hash chain; secrets only in `.env` (never committed); RLS policies shipped for the future
Supabase-Auth path (backend currently connects with the privileged connection and enforces
authz in code — documented trade-off).
