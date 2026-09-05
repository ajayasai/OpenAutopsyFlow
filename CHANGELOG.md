# Changelog

## 0.2.0 — 2026-09-05

- Added an authenticated review workbench with frozen narrative/source traceability, exact source-field differences and preserved revision comparisons.
- Added transactionally captured, append-only report history and evidence-specific independent reviewer receipts. Resubmission invalidates old receipts; issuance rechecks approver eligibility and current required-original metadata.
- Added independently retainable signed audit checkpoints and a trusted-key-pinned offline verifier.
- Added checksummed, atomic schema migrations with pre-DDL key verification, honest legacy baselines and v1/v2 backup restoration compatibility.
- Included report revisions and receipts in case exports. Added explicit report-version attribution to edit/transition audit events.
- Expanded synthetic regression coverage and browser harnesses; added real HTTPS browser and non-root container CI jobs; raised the coverage floor to 85%.

Upgrade note: back up first. Existing issued reports are preserved. Previously approved unissued reports with required evidence need a fresh review round. This is pre-production software, not a validated clinical release.


## 0.1.0 — 5 September 2026

Initial independently implemented casework application. Includes case/finding version history, controlled evidence uploads, laboratory/task dashboard, traceability prompts, author/reviewer workflow, immutable issued PDFs, supplementary reports, signed exports, encrypted backup/restore, local administration, optional TOTP and a responsive browser workspace.

Includes backend/API regression tests, an offline Chromium/ASGI workflow harness, a synthetic single-process benchmark, deployment examples and a safe new-public-repository publisher. Pre-production; not independently audited or validated for clinical/medicolegal casework. Repository publication and hosted CI execution are not implied by the existence of this source package.
