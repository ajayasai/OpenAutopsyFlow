# Review workbench and provenance (0.2)

Open `/review`, or use **Open evidence review workbench** above the original casework application. It uses the same session, CSRF protection and case memberships. Static pages contain no case data. Search assigned cases, choose a report, and review its frozen narrative beside supporting originals. An examiner still writes the medical opinion in the original casework editor.

## Independent original-evidence review

Approval now requires a receipt from the approving reviewer for each laboratory-result original in the snapshot and each original explicitly cited or directly linked to a referenced finding. An unrelated, unreferenced non-laboratory attachment is not automatically classified as relevant. Explicit links and the existing limited English injury-reference parser drive this requirement; this is not a clinical natural-language completeness test.

The reviewer opens each original through the workbench and writes a specific note with an explicit checkbox attestation. The server verifies the original bytes against their recorded hash before returning them. A receipt records the report identifier, in-review version, content digest, evidence identifier/hash, reviewer, timestamp and statement. A prior file-open audit event must match that same review round, reviewer and content digest. A download does not prove reading or comprehension; the attestation remains a human responsibility.

Receipts do not change the case-level evidence-reviewed flag, touch the case revision or silently update report sources. Case-level unreviewed-material prompts and examiner rationales remain separate. Missing/quarantined references and stale source snapshots remain structural blockers. There is no automatic cause/manner-of-death inference, opinion generation or medical approval.

Returning a report to draft and submitting again starts a new review round, even when its prose is unchanged. Old receipts remain in history but cannot authorize the new round. Receipts from another reviewer do not authorize the approving account. An author, last editor or historical report narrative/snapshot contributor cannot attest as an independent reviewer. Issuance rechecks that the approving reviewer is active, still assigned as a reviewer and independent, and that required originals still match clean reviewed metadata.

This establishes different accounts, not proof of different human identities. Organizational identity governance and qualified-signature requirements are not implemented by a local receipt.

## Preserved revisions and comparisons

Every new report insert/update captures the report fields, full narrative, frozen source snapshot, acknowledgements and workflow metadata into `report_history` in the same transaction. Evidence ciphertext, authentication secrets and duplicate issued-PDF bytes are not copied into history. History and receipt tables reject UPDATE/DELETE through SQLite triggers. A privileged database operator can bypass these protections; they are not write-once hardware.

History is paginated at 50 versions. Compare any two captured versions for exact before/after narrative, source-field changes, acknowledgements and workflow state. Source changes distinguish missing fields from explicit nulls, preserve JSON-pointer field paths, and expose additions, amendments, retirements and reactivations. Reference resolution always uses the report's frozen snapshot, not newer case facts. Refresh never rewrites the medical narrative.

At migration, an existing report contributes only its currently stored version, explicitly labelled `legacy_baseline`. Earlier drafts are not reconstructed. An old issued report is not reopened and receives no invented retrospective receipts. Previously approved but unissued reports with required originals need a new review round before issuance.

## Independently retained audit checkpoints

**Save audit checkpoint** returns a domain-separated Ed25519-signed JSON checkpoint of the case audit head and event count. Obtain the deployment public key through the administrator's established trusted channel (`openautopsyflow public-key` is available locally). Retain BOTH that key and the checkpoint outside the application, under independently controlled custody.

A later case export includes `audit.json`. Verify that chain against the retained checkpoint:

```bash
python -m openautopsyflow.review retained-checkpoint.json audit.json --trusted-key-file trusted-public-key.txt
```

The verifier rejects invalid signatures, wrong trusted keys, mismatched scopes, changes to the anchored prefix and chains shorter than the checkpoint. It permits later events but reports their count as unanchored: they have local chain consistency only. A new checkpoint cannot reveal tampering that occurred before it was created. The checkpoint neither independently timestamps events nor authenticates clinical conclusions. Compromise of the signing key or replacement of the externally retained anchor remains a trust failure.

A checkpoint covers the audit chain, not an independently timestamped hash of the entire database. A bundle signature verifies export integrity separately; it is not a clinician's qualified electronic signature.

## Migration and storage operations

Back up the deployment and separately retain its encryption key before upgrading. Stop old application processes. Version 0.2 upgrades a supported v1 store to schema v2 using an immediate transaction. It checks the existing master-key marker and supported version before schema changes, records a migration checksum, and rolls all migration DDL back on failure. Restart verifies the applied checksum. Unknown schemas and changed checksums fail closed.

The upgrade is additive; there is no automatic downgrade. Restoring a v1 backup upgrades it through the same migration; v2 backups retain revision history and receipts. The existing encrypted-backup restore path revokes restored sessions. Test backup and restore on a copy before any operational rollout.

Structured history remains plaintext in SQLite, just like existing case metadata. Encryption of disks, backups and access-controlled storage is still required. Full snapshots increase storage per report edit; this is a deliberate fidelity tradeoff, not a claim of unlimited storage. The existing bounded single-node export limit still applies. Exports now include `report-history.json` and `review-receipts.json` in addition to the original case/evidence/audit material.
