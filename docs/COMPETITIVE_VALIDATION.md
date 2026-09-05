# Competitive evidence, not universal-superiority claims

Reviewed 5 September 2026. Public vendor pages describe capabilities, not independently reproduced test outcomes. Absence of a feature on a marketing page is not evidence that the product lacks it.

| Comparator | Publicly described baseline | What still needs direct evaluation |
|---|---|---|
| Forensic Advantage Medical Examiner CMS | Autopsy entry, photographs, report review/approval, configurable document generation, custody tracking, laboratory work, scene/offline workflows and EDRS integration | Revision fidelity, evidence-specific reviewer binding, stale-source behavior, migration safety, performance and export completeness on equivalent cases |
| Forensic Filer Online | Case data, autopsy information, narratives, Media Vault images, forms including body diagrams, reporting and hosted support/backup arrangements | Equivalent review and tampering scenarios; institutional usability and support quality |
| Coroner Pro | Collaborative casework, notifications, autopsy forms, image annotation, sample tracking and report workflows | Exact provenance semantics, review independence and measurable operational outcomes |

Primary sources:
- https://www.forensicadvantage.com/medical-examiner-edition
- https://www.forensicfiler.com/forensic-filer-online.aspx
- https://www.coronerpro.com/

## Implemented competitive focus

OpenAutopsyFlow 0.2 provides inspectable tests and code for immutable draft-revision capture, reviewer-specific original-evidence receipts, source/narrative comparison, independent audit checkpoints and additive migration safety. These are concrete improvements over this project's 0.1 implementation. They are not established exclusive capabilities relative to the products above.

## Reproducible acceptance protocol

Use only synthetic cases, identical workflows and supported configurations. Record product/version, deployment settings, operator training, actual steps and whether configuration could resolve an observed failure. Have qualified departmental reviewers define the intended workflow before running timed trials.

1. Change a finding after the report snapshot. Measure stale-source detection, visibility of the precise changed field, and whether prose is silently rewritten.
2. Replace one version's wording and request both drafts. Verify earlier narrative, sources and acknowledgements remain recoverable.
3. Attempt approval without opening/reviewing a linked original, with another reviewer's receipt, after resubmission, and after approver disablement. Record actual acceptance/rejection and audit evidence.
4. Add new laboratory evidence after approval. Test re-review and supplementary reporting without modifying previously issued PDF bytes.
5. Attempt cross-case access using known identifiers through every interface, including history, evidence, comparisons and exports. Test denied roles and disabled accounts.
6. Export a case and verify independent readability, hashes, full revision/evidence/review coverage, missing originals and quarantine exclusions. Introduce a changed or truncated audit chain and test against an independently retained anchor.
7. Rehearse backup, interrupted migration, wrong-key startup and restore on disposable copies. Measure recoverability and record any manual intervention.
8. Run counterbalanced usability trials: intake time, time to find changed evidence, review completion, operator errors and missed planted workflow inconsistencies. Include accessibility and realistic case size/concurrency tests.

Publish successful and failed outcomes with denominators and uncertainty. A zero-issue dependency scan is not a penetration test. A synthetic test suite is not a clinical validation study. Local timings do not establish production capacity or superiority over vendor-hosted deployments.

## Remaining material gaps

Single-node SQLite is not PostgreSQL/high availability. There is no enterprise SSO or external identity proofing, live LIS/EDRS/HL7/FHIR/DICOM connectivity, offline mobile synchronization, anatomy annotation, qualified electronic signatures, validated jurisdictional forms, field-level database encryption/key rotation or contracted operational support. Those gaps preclude claiming that this release replaces every broader commercial system. Independent security and departmental acceptance testing remain necessary.
