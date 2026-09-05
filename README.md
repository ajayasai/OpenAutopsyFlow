# OpenAutopsyFlow

**Evidence-linked autopsy casework, with human-controlled report review.**

Version **0.2.0** · Apache-2.0 · Self-hosted · Pre-production

> This is a working initial release, not a validated clinical/medicolegal system. Use synthetic data until independent security review, departmental workflow validation and operational acceptance are complete. No claim of superiority over every commercial product is supported by the current evidence.

Public source repository: **[ajayasai/OpenAutopsyFlow](https://github.com/ajayasai/OpenAutopsyFlow)**.

This GitHub edition contains the runnable application, tests, deployment configuration and documentation. Generated screenshots, the sample PDF and the static OpenAPI snapshot are not committed; the browser harness regenerates synthetic previews, and authenticated `/api/schema` exposes the running API schema. The original full source ZIP also contains the generated previews. **Use the GitHub runtime lock for current installs:** the original ZIP predates the dependency-security update discovered during hosted CI.

## New in 0.2: a review that can be retraced

Open **[the review workbench](docs/REVIEW_WORKBENCH.md)** through the link above the main application. It adds full preserved draft revisions, exact narrative/source comparisons, reviewer-specific original-evidence receipts and independently retainable signed audit checkpoints. Approval requires the assigned independent reviewer to open and explicitly attest to the required originals for that exact report review round. Reassignment, disablement, changed sources and resubmission cannot silently reuse an obsolete review.

The additive schema-v2 migration authenticates the existing store before DDL, checks migration integrity on restart and rolls back atomically on failure. Existing issued reports stay unchanged; existing reports contribute only their currently stored legacy baseline, not invented earlier drafts. **Back up before upgrading.** Previously approved unissued reports with required evidence need a new review round.

See [0.2 validation](docs/VALIDATION_0_2.md), [workbench and upgrade guide](docs/REVIEW_WORKBENCH.md), and [competitive evaluation protocol](docs/COMPETITIVE_VALIDATION.md). These are tested improvements over OpenAutopsyFlow 0.1, not proof of universal superiority over commercial systems.

## What is implemented

| Area | Working capabilities |
|---|---|
| Case intake | Identifiers, examination date, authority, examiner, identification status, priorities, due dates, case-scoped team access, append-only intake amendment history |
| Examination | External/internal findings, organ measurements with explicit units, stable numbered injuries, specimen records, reasoned amendments and retirement without deletion |
| Evidence | Original PDF/JPEG/PNG/TXT uploads, case/finding links, SHA-256 hashes, AES-GCM encrypted originals, bounded uploads, quarantine and explicit human review |
| Pending work | Laboratory requests, specimen/result links, tasks, assignees, deadlines, search, server-side filtering and aggregate counts across matching cases |
| Reports | Versioned section templates, human-authored narratives, PDF preview, reviewer comments, separate-account approval, frozen issued PDF bytes and linked supplements |
| Traceability | Explicit source references, missing-injury detection, stale-snapshot detection, review rationales for pending/unreviewed materials, changed-source indicators |
| Provenance | Append-only finding/intake history, per-case hash-chained audit events, specimen handover records and separate staff countersignatures |
| Portability | Signed JSON/PDF/evidence ZIP exports, independent verification with optional trusted-key pinning, encrypted backups and tested restore into a new directory |
| Access controls | Argon2id passwords, HttpOnly sessions, CSRF/origin checks, case roles, optional authenticator MFA, account disable/password reset/session revocation |

**Not implemented:** interactive 3D body annotation, EDRS/HL7/FHIR/DICOM integrations, automatic laboratory connectivity, SSO, high-availability/multi-tenant hosting, offline mobile synchronization, production key rotation, qualified electronic signatures, jurisdiction-approved forms, or medical decision automation. PDF templates configure section structure; this is not a Word-style visual template designer.

## Run a synthetic demonstration

Python 3.13 is the locally tested interpreter. GitHub Actions tests Python 3.11, 3.12 and 3.13; consult the [hosted workflow results](https://github.com/ajayasai/OpenAutopsyFlow/actions) for the current revision.

```bash
git clone https://github.com/ajayasai/OpenAutopsyFlow.git
cd OpenAutopsyFlow
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
python scripts/run_demo.py
```

Open `http://127.0.0.1:8000`. On first startup, the launcher prints **randomly generated** passwords for `examiner`, `reviewer`, `coordinator` and `auditor`. There are no fixed runtime passwords. Existing demo data is never reseeded or overwritten. Stop with Ctrl+C. The dedicated demo directory is `artifacts/demo-data`, excluded from publishing.

For the first synthetic case, open **Reports**. A deliberately missing **Injury 4** demonstrates a structural prompt. Add that injury as an examiner, explicitly refresh the report snapshot, reconcile the narrative, acknowledge remaining review prompts, and submit. Sign in as `reviewer`, open the review workbench, open each required original and record its review receipt before approval and issuance. The third synthetic case already awaits review.

**Demo mode disables malware scanning and permits non-HTTPS cookies. It must never hold real case material.** An installed Python environment can run the application without external AI services or CDNs. Installation and dependency updating normally need network access.

## Review rules, not medical judgments

`[[injury:4]]`, `[[record:UUID]]` and `[[evidence:UUID]]` create explicit links. A limited English parser also recognizes phrases such as “Injury 4”; it is not a multilingual clinical NLP system.

Missing references, stale snapshots and empty required sections block workflow transitions as structural problems. Pending laboratory work, unreviewed results and absent injury photographs/documents are **human review prompts**, not automatic determinations about a medical opinion. Each warning requires a specific recorded rationale at submission. Departmental validation must determine whether stricter local policies are necessary.

Refreshing source records **never silently rewrites the opinion**. It preserves typed prose and resets acknowledgements. The examiner must reconcile prose against changed sources. Authors, last editors and historical report narrative/snapshot contributors cannot approve the same report after being reassigned to a reviewer role. This enforces different accounts, not proof of different human identities.

Issued report rows and original PDFs cannot be changed through the application. New evidence belongs in a supplementary version linked to the latest issued report. The original remains available. A privileged database administrator can bypass local triggers; this is not tamper-proof storage.

## Test and inspect

```bash
python -m pip install -r requirements-dev.lock
python -m coverage run -m pytest
python -m coverage report --fail-under=85
node --check openautopsyflow/static/app.js
node --check openautopsyflow/static/workbench.js

# Optional offline browser harness (Chromium DOM + real in-process API)
python -m pip install '.[ui]'
python -m playwright install chromium
python scripts/browser_smoke.py

# Review workbench: default uses a real local HTTPS test server
python scripts/workbench_smoke.py --mode live
# Explicit fallback for browser-network-restricted environments (NOT HTTPS validation)
python scripts/workbench_smoke.py --mode offline

# Sequential synthetic benchmark, not a vendor comparison
python scripts/benchmark.py --cases 1000 --repeats 30
```

See [recorded validation](docs/VALIDATION.md), [benchmark data](docs/benchmark-results.json), the authenticated `/api/schema` endpoint, [architecture](docs/ARCHITECTURE.md) and [workflow acceptance tests](docs/WORKFLOW.md). The browser harness does not substitute for staging tests of TLS, browser cookie delivery or CSP enforcement. The first hosted run passed all three Python test jobs but exposed vulnerable dependency pins. The runtime lock and package requirements were updated to patched releases, without suppressing audit findings. The same test matrix and dependency audit run on the updated source; consult [GitHub Actions](https://github.com/ajayasai/OpenAutopsyFlow/actions) for the latest result. Historical local coverage and browser results in `docs/VALIDATION.md` describe the original dependency environment, not a claim that the updated packages were retested locally.

## Repository and source integrity

The source is published at **[ajayasai/OpenAutopsyFlow](https://github.com/ajayasai/OpenAutopsyFlow)** on `main`. GitHub publication does not deploy a live casework service or validate the application for clinical use.

Verify the reviewed source inventory locally:

```bash
python scripts/publish_github.py --dry-run
```

The checksum manifest excludes runtime data. The publisher's non-dry-run mode is only for creating a **new, differently named repository** using your own locally authenticated GitHub CLI; it deliberately refuses an existing repository. Do not run it against this repository to update it. Normal contributions should use reviewed Git commits and pull requests. See [publishing details](docs/PUBLISHING.md).

## Deployment, trust and limitations

Read [SECURITY.md](SECURITY.md), the [threat model](docs/THREAT_MODEL.md), [deployment runbook](docs/DEPLOYMENT.md) and [export/restore guide](docs/PORTABILITY.md) before deployment.

Evidence, issued PDFs and whole backups are encrypted. **Case metadata, narratives and other structured fields remain plaintext in SQLite.** Protected encrypted disks, access-controlled backups, HTTPS, careful account provisioning and separate key custody are deployment requirements, not features supplied automatically by this package. An application bundle signature is not a clinician’s qualified electronic signature. A local audit chain cannot independently establish an event’s time, identity or legal admissibility.

This release is intentionally a single-node SQLite application. Export generation is bounded at 128 MiB of uncompressed members; the verifier permits at most 256 MiB. Uploaded originals are limited to 12 MiB each. There is no automatic retention/deletion policy or historical key rotation facility yet. Schema upgrades now use an additive, checksummed migration framework; no automatic downgrade is provided. Malware scanner integration is fail-closed outside demo mode, but real ClamAV operation has not been exercised in the local validation environment.

## Relationship to existing products

Forensic Advantage already advertises autopsy entry, photographs, laboratory-request tracking and report approval, alongside substantially broader functions. Forensic Filer provides coroner/medical-examiner case management and reporting. FATAL describes interactive 3D autopsy annotation. These are existing baselines, not inventions of this project. [1–3]

OpenAutopsyFlow’s proposed competitive focus is inspectable source, explicit provenance, reproducible workflow tests and portable records. A [comparison and validation plan](docs/COMPARISON.md) distinguishes tested behavior from unverified competitor capabilities. No proprietary code, screens, templates or body models were copied.

### Public sources

1. [Forensic Advantage: Medical Examiner CMS](https://www.forensicadvantage.com/medical-examiner-edition), accessed 5 September 2026.
2. [Forensic Filer Online: product information](https://www.forensicfiler.com/forensic-filer-online.aspx), accessed 5 September 2026.
3. [Petersen et al., FATAL, Computers in Biology and Medicine 182 (2024), 109170](https://pubmed.ncbi.nlm.nih.gov/39303395/), DOI 10.1016/j.compbiomed.2024.109170.

## License

Original application code is available under [Apache License 2.0](LICENSE). Dependencies retain their own licenses. No font files or real case records are distributed. See [NOTICE](NOTICE).
