# Workflow and acceptance scenarios

## Roles

Account administrators provision/disable accounts and version templates. They still need case membership to see records. A case-member administrator may assign/change roles or revoke another member; those changes are audited. Disabling an account revokes sessions; password changes and backup restore also revoke sessions. Local console recovery requires host-level authorization and is part of the trust boundary.

An examiner records clinical observations and edits report narrative. A coordinator records specimens, laboratory work/tasks, uploads supporting documents and assists with issuing approved versions. A reviewer reviews evidence, comments, approves and may issue; narrative edits require an examiner role and make that account ineligible to approve the report. Auditors are read-only at the case-workflow level. The application does not verify professional licensing or prevent a human administrator controlling multiple accounts.

## Normal path

Create intake, assign a separate reviewer, record structured findings and specimens, and upload supporting requisitions/photos/results. Create a report using an approved local template. The application copies structured observations into appropriate initial sections; the opinion is empty until an examiner supplies it.

Use explicit source tokens. Save the narrative, inspect source-link resolution and review pending work. Write a specific rationale for each warning before submitting. An assigned reviewer can add blocking comments, resolve them, and approve. Assigned operational staff can issue an approved version. After issue, add new findings/evidence to the case and prepare a supplement rather than changing the issued report.

## Required regression scenarios

| Scenario | Expected result |
|---|---|
| Report mentions Injury 4, but no active Injury 4 exists | Structural missing-reference blocker; no automatic medical conclusion |
| A new laboratory result arrives after a draft snapshot | Snapshot becomes stale; new result remains unreviewed until explicitly reviewed |
| Unreviewed/pending laboratory material remains | Warning needs a reasoned human acknowledgement; local validation may require stricter rules |
| Another session edits a finding before save | Stale revision rejected with no lost update |
| Another session edits the report | Stale report-version save rejected |
| Author changes role to reviewer | Still cannot approve the report they authored/contributed to |
| Reviewer has an unresolved blocking comment | Approval/issue blocked |
| A record changes after approval | Issue blocked until draft refresh and new review |
| User tries to modify issued report | Rejected; original PDF bytes remain identical |
| Supplement points at a non-latest issued version | Rejected |
| Unauthorized case ID/evidence ID is guessed | No case content returned |
| Malware screening is absent or fails outside demo | Original remains quarantined, unavailable for ordinary download/review |
| Export is altered or signed with an unexpected pinned key | Verification fails |
| Backup has altered ciphertext or wrong key | Restore fails; existing deployment is not overwritten |

The complete executable tests are under `tests/`. The browser harness also exercises the examiner-to-reviewer path. Neither a passing check nor a reviewer click establishes that a medical opinion is factually correct, that a specimen physically changed hands or that an artifact is legally admissible.
