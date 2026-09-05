# Competitive scope and evaluation plan

## Current evidence, not a “best in the world” label

This release has not been tested head-to-head against commercial deployments. Public product descriptions cannot establish the absence of hidden/configurable features. “Unknown” must not be converted into a claimed advantage.

| Capability | OpenAutopsyFlow 0.1.0 | Public baseline / limitation |
|---|---|---|
| Intake, autopsy records, photos, lab tracking, reports | Implemented, intentionally narrow | These are existing Forensic Advantage capabilities, not novel claims [1] |
| Coroner/ME electronic case management | Implemented subset | Forensic Filer already supplies broad records/reporting workflows [2] |
| Explicit evidence/finding/report links | Implemented with regression tests | Comparable depth in commercial configurations was not independently tested |
| Frozen issued bytes and linked supplements | Implemented and tested | No claim that all competitors lack this |
| Open implementation and portable manifest verifier | Apache-2.0 source and local verifier included | Inspectability is a project property, not evidence of better clinical outcomes |
| Interactive anatomical 3D annotation | Not implemented | FATAL describes this functionality [3] |
| Institutional operations/integrations | Many gaps | Real laboratory, registry, identity and departmental integrations require separate work |

## How to earn a defensible superiority claim

Recruit authorized practitioners and administrators to define a bounded task set using synthetic or properly governed material. Compare the same cases and departmental configuration, with order randomized and training time recorded. Include at least intake, complex injury amendments, new lab results after draft/approval/issue, disputed/corrected attachments, role changes, missing references, export verification and isolated restore.

Measure completion time, corrected/uncorrected record defects, false-positive prompts, missed stale results, reviewer burden, report fidelity, successful export/reconstruction, accessibility and operator satisfaction. Report medians and tail latencies with confidence intervals; account for repeated cases and repeated users. Publish all failure categories rather than only favorable examples. Security and clinical correctness need distinct evaluation: faster data entry does not establish sound opinions.

Separate single-node performance from multi-user concurrency and high-availability claims. Measure realistic photo/report sizes and long histories. Retention, outage recovery, malware handling and trust-anchor compromise belong in acceptance testing, not marketing. The included 1,000-case benchmark has synthetic tasks, no photos/reports, a warm cache and a sequential in-process client; it cannot establish enterprise capacity or beat a competitor.

## Sources

[1] [Forensic Advantage Medical Examiner CMS](https://www.forensicadvantage.com/medical-examiner-edition), accessed 5 September 2026.

[2] [Forensic Filer Online](https://www.forensicfiler.com/forensic-filer-online.aspx), accessed 5 September 2026.

[3] [Petersen et al., FATAL (2024)](https://pubmed.ncbi.nlm.nih.gov/39303395/), DOI 10.1016/j.compbiomed.2024.109170.


## Version 0.2 update

See [the updated competitor evidence and reproducible evaluation protocol](COMPETITIVE_VALIDATION.md). New review/provenance capabilities are documented in [the review workbench guide](REVIEW_WORKBENCH.md); they are not proven exclusive to this project.
