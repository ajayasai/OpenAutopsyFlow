# Architecture and invariants

## Deployment shape

A same-origin vanilla JavaScript/CSS workspace talks to a FastAPI API. Pydantic rejects unknown fields, non-finite numeric input and out-of-range structural values. SQLite stores the aggregate with foreign keys, WAL journaling, explicit transactions and local append-only triggers. Cryptography provides AES-GCM, HKDF and Ed25519 primitives. ReportLab renders escaped plain text into PDF. No external AI/CDN/service is invoked by application workflows.

The deployment unit is one node and a durable local volume. The API is synchronous for database operations; file reading is bounded, and the optional malware scan runs in a thread pool. SQLite writes use `BEGIN IMMEDIATE` to serialize revision checks and writes. Authenticated reads also write session timestamps and selected access audit events. This is not a high-throughput distributed architecture.

## Source map

| Module | Responsibility |
|---|---|
| `store.py`, `schema.sql` | Database, settings, canonical JSON, artifact hashes, encryption, signing, audit chaining |
| `schemas.py` | Strict API input contracts and typed record payloads |
| `security.py` | Password verification, TOTP, sessions, throttling, request limits and security headers |
| `service.py` | Case authorization, revisioned changes, finding/evidence links, report workflow and export |
| `rules.py` | Deterministic source-reference checks and review prompts |
| `documents.py` | Plain-text PDF rendering, signed ZIP creation and offline verification |
| `api.py` | API endpoints, roles, error mapping and static workspace |
| `cli.py` | Initialization, local accounts, MFA recovery, rescan, backup/restore and verification |

## Record relationships

A case has explicitly assigned members, intake revisions, examination records, evidence, specimen handovers and report versions. Every examination record has a stable UUID, a kind and an append-only version history. Injuries also have stable case-local numbers that cannot be reused/renumbered. Retiring a finding preserves previous values and makes it unavailable as an active report source.

Evidence belongs to one case and can link to a finding in that case. Original bytes, name, digest, type and link are immutable; a mistaken attachment must not silently replace a prior original. This release does not yet support an evidence-correction/withdrawal lifecycle; record the concern in a case note and do not treat the old attachment as valid merely because it is retained.

Reports copy a source snapshot and a specific template version. Report narrative, snapshot and acknowledgements participate in the approved digest. `source_revision` is compared to the current case revision before submit/approve/issue. Case membership changes also invalidate pending snapshots conservatively. A refreshed snapshot does not regenerate narrative. Reviewer comments are separate records with explicit blocking/resolution status.

Approval and issue are distinct operations. Issuing creates PDF bytes once, hashes and encrypts them, and freezes the report. Downloads use those stored bytes. Supplements retain `parent_id` pointing at the latest issued report. Only one unissued draft/review/approved report is allowed per case.

## Encryption and export

The 32-byte master secret encrypts evidence, issued PDFs, TOTP enrollment secrets and encrypted whole-database backups using distinct authenticated contexts and random nonces. An HKDF-separated seed supplies the Ed25519 export signer. Root-secret compromise affects all these purposes. A local key fingerprint rejects accidental startup with the wrong master secret.

The JSON export manifest records SHA-256 and byte count for every included member. The signature covers canonical manifest bytes. The verifier never extracts files, rejects unsafe/duplicate names and unlisted/missing members, bounds expanded size and optionally pins the signing public key supplied out of band. A key included with its own export establishes internal consistency, not trusted identity. This is not a qualified signature or independent timestamp.

## Operational gaps

No streaming large-case export, multi-tenant boundary, background job queue, schema migration framework, immutable remote audit sink, full-text document extraction, field-level encryption or key rotation is supplied. Report-template vocabulary and forms need department-specific validation. HTTP health only checks process availability; it is not a complete storage/backup readiness probe.

Primary implementation references: [FastAPI middleware](https://fastapi.tiangolo.com/advanced/middleware/), [Starlette middleware](https://www.starlette.io/middleware/) and [cryptography AEAD documentation](https://cryptography.io/en/latest/hazmat/primitives/aead/). These references describe underlying tools, not certification of this implementation.
