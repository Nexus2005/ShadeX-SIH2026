# ShadeX-SIH2026

**Resilient AI** — evidence-first scrutiny for AICTE institutional applications (SIH 2026).

The platform ingests an application's documents, extracts structured fields with page/box
evidence, checks completeness and cross-document consistency, evaluates **versioned** regulatory
rules, flags duplicate or anomalous documents, and runs **simulated** issuing-authority
verification. Findings are queued for authorised human reviewers, who can confirm, dismiss,
request clarification or resolve them; scrutiny reports are exported as PDFs.

It assists scrutiny. It never issues a regulatory decision, and no finding is proof of fraud.

## What is implemented

| Area | Status |
| --- | --- |
| Auth (bcrypt + JWT sessions, RBAC: admin / reviewer / institution / auditor) | done |
| Institutions, applications, status history & workflow transitions | done |
| Upload validation (magic bytes, size limits, SHA-256, generated filenames) | done |
| PDF text layer + RapidOCR fallback with line boxes and confidence | done |
| Classification (keyword rules first, optional Gemini assist) and field extraction | done |
| Completeness, expiry and cross-document consistency findings with evidence | done |
| Immutable versioned rule engine (6 demo rules) | done |
| Exact-duplicate (SHA-256), near-duplicate (phash), PDF metadata forensics | done |
| Simulated verification adapters with explicit statuses | done |
| Reviewer actions, field corrections, PDF scrutiny reports | done |
| Audit trail with SHA-256 hash chain | done |
| Dashboard, applications, findings, rules, verification, reports, audit, users UI | done |
| Unit tests (`backend/tests`, 37 tests) | done |
| Multi-case seeded demo dataset (A–J) | partial |

## Stack

Next.js 14 (App Router, TypeScript, Tailwind) · FastAPI (Python 3.11, Pydantic v2,
SQLAlchemy 2) · Supabase PostgreSQL (31 tables) · PyMuPDF, RapidOCR, OpenCV, ImageHash,
RapidFuzz · ReportLab · Gemini (optional assist, graceful fallback).

## Repository layout

```
backend/            FastAPI app, services, SQL migrations, seed, tests
frontend/           Next.js app (login, dashboard, applications, findings, rules, ...)
docs/BLUEPRINT.md   Architecture and design principles
docs/PHASES.md      Build tracker (single source of truth for progress)
run_backend.ps1     Start API on :8000 (Windows)
run_frontend.ps1    Start UI on :3000
start_all.ps1       Start both and wait for health
```

## Getting started

### Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt    # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS / Linux

cp .env.example .env        # fill DATABASE_URL, SECRET_KEY, (optional) GEMINI_API_KEY
.venv/Scripts/python tools/run_migrations.py
.venv/Scripts/python -m app.seed
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

API docs: <http://127.0.0.1:8000/api/docs> · health: `/api/health`

### Frontend

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000
```

Set `NEXT_PUBLIC_API_URL` if the API is not on `http://127.0.0.1:8000/api/v1`.

### Demo accounts

All demo accounts use the password `Password123!`:

| Email | Role |
| --- | --- |
| `admin@resilient-ai.local` | admin |
| `reviewer@resilient-ai.local` | reviewer |
| `institution@resilient-ai.local` | institution |
| `auditor@resilient-ai.local` | auditor |

### Tests

Pure unit tests — no database or network access required:

```bash
cd backend
.venv/Scripts/python -m pytest -q
```

## Demo dataset

`app.seed` creates one synthetic application (`RAI-*`) with eight documents covering:
a valid fire NOC, an **expired** fire NOC, a typo'd institution name that produces a
**verification mismatch**, and two **byte-identical** lab photos that trigger the duplicate
detector. Re-running the seed rebuilds the documents and clears the findings derived from them.

## Honest boundaries

- **No live government integrations.** Verification providers are simulated and labelled
  `is_simulated` in the API, UI and reports. DigiLocker/issuer adapters are interfaces only.
- **Preliminary indicators.** Metadata, phash and duplicate signals are investigative aids,
  never proof of tampering or fraud.
- **Rules are demo rules.** Every seeded rule is marked `is_demo_rule` until verified against
  the applicable AICTE handbook clause.
- **Tamper-evident, not immutable.** The audit trail is an append-only hash chain with triggers;
  it is not a cryptographic ledger.
- Gemini is optional. With no API key the system runs fully deterministically.

See `docs/BLUEPRINT.md` for design principles and `docs/PHASES.md` for current progress.
