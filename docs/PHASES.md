# Resilient AI — Phase Tracker

Single source of truth for build progress. Statuses:
`completed` · `in_progress` · `pending` · `blocked`.

> Update this file whenever a phase is completed. Each entry links to what was built.

| # | Phase | Scope | Status | Evidence |
| --- | --- | --- | --- | --- |
| 0 | **Repo & schema** | Git structure, .gitignore, migrations 0001–0006 (31 tables), requirements.txt, venv with deps | completed | `backend/migrations/`, `backend/requirements.txt` |
| 1 | **Blueprint & tracking** | `docs/BLUEPRINT.md`, `docs/PHASES.md`, `README.md` | completed | `docs/` |
| 2 | **Foundation & auth** | FastAPI skeleton, config, SQLAlchemy models, bcrypt+JWT sessions, RBAC, audit hash chain wiring, institutions & applications CRUD, status history, seed data, run migrations | completed | `backend/app/` |
| 3 | **Document processing** | upload validation (magic bytes/limits/sha256), private storage, PyMuPDF text layers, RapidOCR for scans, pages with boxes+confidence, classification (rules+Gemini), field extraction, processing jobs | completed | `app/services/processing.py`, `extraction.py`, `storage.py` |
| 4 | **Completeness & consistency** | category checklists, missing/expired/unreadable checks, cross-document comparison limited to fields that must agree, evidence-linked findings | completed | `app/services/checks.py`, `rule_engine.py` |
| 5 | **Rule engine** | versioned rules (immutable versions), evaluation storage, rule management API/UI | completed | `app/services/rule_engine.py`, `frontend/app/(app)/rules` |
| 6 | **Authenticity & verification** | sha256 dupes, phash near-dupes, PDF metadata forensics, simulated verification adapters with explicit statuses and field-level comparison | completed | `app/services/anomaly.py`, `verification.py` |
| 7 | **Review workflows & reports** | findings queue, reviewer actions, field corrections, release-to-institution, PDF scrutiny reports | completed | `app/routers/workflow_routes.py`, `app/services/reports.py` |
| 8 | **Dashboard** | live statistics from persisted data | completed | `GET /api/v1/dashboard/summary`, `frontend/app/(app)/dashboard` |
| 9 | **Frontend** | Next.js + login + applications + upload + documents + findings + review + reports | completed | `frontend/app/(app)/` |
| 10 | **Testing & hardening** | pytest unit suites for storage security, auth tokens, extraction, rule evaluators, consistency scope, verification comparison | in_progress | `backend/tests/` (37 tests) |
| 11 | **Demo dataset breadth** | seeded cases A–J (only expired, duplicate, verification mismatch, healthy case exist today) | pending | `app/seed.py` |

## Recent fixes (2026-10-29)

- Frontend production build repaired (report fields, missing imports, unused imports); `npm run build` passes.
- Reprocessing a document no longer fails on the `document_pages` uniqueness constraint.
- Date extraction no longer lets a generic "Issued on" pattern overwrite an expiry date, which
  previously marked a valid fire NOC as expired.
- Source verification now compares real extracted fields against reference data and reports
  `manual_review_required` when a field cannot be compared instead of silently "verified".
- Cross-document consistency is restricted to institution-level fields; document-specific
  numbers/dates/issuers are no longer reported as conflicts.
- Seed now generates a genuine byte-identical duplicate pair and clears derived findings on reseed.

## Credentials & integration notes

- Supabase project: `spnnwmlkanpwwdqutitr.supabase.co` (Postgres direct; Anon/Service keys not provisioned — backend uses direct connection + in-code authorization).
- Gemini: API key stored in `backend/.env` (`GEMINI_API_KEY`). All Gemini use is **optional assist** with graceful fallback; model id is env-configurable (`GEMINI_MODEL`, default `gemini-2.5-flash`).
- GitHub: `https://github.com/Nexus2005/ShadeX-SIH2026.git` (branch `main`).
- DigiLocker / issuing-authority / AICTE records integrations: **not authorized** — adapter interfaces only, simulated data clearly labelled.

## Known boundaries (do not misrepresent)

- No live government integrations. Simulated verification is labelled in UI, API and reports.
- Metadata/ELA/phash findings are **preliminary indicators**, never proof of fraud.
- Audit trail is tamper-evident (hash chain + triggers), not cryptographically immutable.
- Demo rules must remain labelled until verified against the official AICTE handbook.
- Tests are unit-level and database-free; there is no HTTP integration suite yet.
