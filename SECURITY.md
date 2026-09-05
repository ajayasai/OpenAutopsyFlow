# Security status and reporting

**Pre-production. No independent penetration test, clinical validation, compliance certification or production security assurance is claimed. Do not deploy real casework until the acceptance gates are satisfied.**

## Sensitive information

Never place real case narratives, photographs, specimen identifiers, account passwords, production keys or live database backups in GitHub issues, discussions, pull requests or demonstration systems. Redact screenshots and error traces before sharing. The application’s export is **not** a de-identification tool.

A new repository maintainer should enable GitHub private vulnerability reporting. Once enabled, use its private security-advisory channel rather than a public issue for sensitive exploits. This package does not claim that such a channel already exists. Until a private contact channel is established, do not post exploit details or live records publicly.

## Implemented boundaries

- Server-side case membership checks; administrators do not automatically see every case. Clinical findings are examiner-only. Separate report reviewers cannot approve their own report contributions.
- Argon2id password storage; randomly generated demo passwords; optional TOTP with replay protection; revocable HttpOnly sessions with absolute and idle expiry; CSRF/origin checks and login throttling.
- Per-object authenticated encryption of evidence and issued PDFs; fixed file allow-list and size limits; quarantine until a configured scanner reports success outside demo mode.
- Immutable issued-report application state, append-only audit/history database triggers, artifact hashes, Ed25519-signed exports and encrypted backup/restore.

These are implementation properties covered by tests, not a guarantee against a determined attacker.

## Known residual risks

Structured metadata and clinical text are plaintext in SQLite; use encrypted, access-controlled storage. A compromised application host or master key defeats encryption and permits signature impersonation. Signing and encryption keys share one root secret through separate derivations. Key rotation/HSM integration is not implemented.

Audit chains are local and unsigned at each event; a privileged operator can rewrite the database or truncate the end. Retain signed exports/audit-head checkpoints in independently governed immutable storage before relying on them as evidence of continuity. Independent timestamping is not built in.

PDF uploads receive a format check and malware-scanner gate, not a full safe-PDF parser, content disarm/reconstruction or legal authenticity verification. Original photos may retain identifying EXIF data. Downloads preserve original bytes. Use an appropriately isolated viewing environment.

Only one application node is supported. Reverse-proxy request/concurrency limits are essential; SQLite serialization and PDF/ZIP generation can be abused for denial of service by authorized accounts. Shared proxies affect per-IP login throttling unless trusted forwarding is explicitly configured. No email-based recovery, SSO or automated retention policy is supplied.

The demo explicitly bypasses malware screening and allows HTTP. It is for fictional records only. Tests use synthetic password strings; none is a default deployed password.

See [threat model](docs/THREAT_MODEL.md) and [validation record](docs/VALIDATION.md) for details and untested controls.
