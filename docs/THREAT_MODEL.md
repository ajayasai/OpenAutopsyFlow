# Threat model

## Assets and trust boundaries

Protect identifying case data, findings, photographs, lab results, report history, access assignments, specimen records, credentials and audit continuity. The public source repository must never contain live records or production secrets. Treat the browser, application service, local database, filesystem, optional scanner, TLS proxy, administrators and export recipients as separate trust boundaries.

## Threats, controls, residual risks

| Threat | Implemented control | Residual risk / acceptance requirement |
|---|---|---|
| Case-ID guessing | Every case/report/evidence operation checks membership server-side | Review every future endpoint for the same check; host administrators remain trusted |
| Cross-site writes/session theft | HttpOnly/SameSite sessions, CSRF token, origin check, expiry/revocation, no-store headers | Real browser/TLS/proxy validation and workstation security are still required |
| Credential guessing | Argon2id, per-account/IP throttling, optional TOTP with used-counter rejection | Reverse-proxy rate limiting and MFA-enrollment operations need independent testing |
| Unauthorized clinical changes | Case roles, revision comparisons, append-only histories and separate report approval | Different accounts do not prove different people or qualifications |
| Lost updates/stale signoff | Transactional aggregate/report version checks and source-snapshot invalidation | Source refresh cannot determine whether typed prose still reflects changed facts |
| Malicious uploads | Format/size bounds, photo validation, quarantine and optional ClamAV command gate | No complete PDF sanitization, malware-proof guarantee or quarantined-evidence recovery workflow |
| Database/media theft | Authenticated encryption for selected blobs and whole backups | Structured metadata/plaintext narratives require full-disk protection |
| Artifact alteration | Hashes, immutable application originals and signed ZIP verification | Signature identity must be pinned independently; signer and encryption share a root secret |
| Audit manipulation | Local append-only triggers, hash links and export audit head | A privileged administrator can rewrite/truncate; external independent checkpoints are needed |
| Denial of service | Bounded request/file/export sizes and login throttling | SQLite serialization, user-created record volumes and synchronous rendering need deployment capacity controls |
| Accidental public disclosure | Source-only release manifest, forbidden runtime paths and basic secret-pattern detection | Automated scans cannot recognize every sensitive narrative; human review remains mandatory |

Out of scope for current assurance: malicious host root, compromised runtime dependencies, supply-chain attacks, colluding administrators/reviewers, clinically incorrect opinions, real-world custody disputes, client endpoint compromise and accredited records retention. These are not solved by a PDF hash.

The signed case export includes finding histories, intake history, reviewer discussion, used templates, current assignments, report snapshots/PDFs and the per-case event chain. It deliberately excludes unrelated system/account audit events and quarantined originals. Complete intermediate unissued narrative versions are not retained in this release; narrative edit events record hashes. The encrypted database backup preserves all currently retained database records. Evaluate disclosure requirements before treating a case export as a complete institutional evidentiary package.
