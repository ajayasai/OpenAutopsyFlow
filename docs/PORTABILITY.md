# Export verification and backup recovery

## Case exports

Only assigned examiners/reviewers can request a case export. The workspace warns that the ZIP contains sensitive material. It is not anonymized. A base export contains `case.json`, per-case `audit.json`, `record-history.json`, `review-discussion.json`, used `templates.json`, current `assignments.json`, report snapshot JSON and issued PDF originals. With “include clean evidence,” it also includes cleared original attachments. Quarantined originals are intentionally excluded. Unrelated system/account logs are not included in a case export; use the encrypted full backup for complete database preservation. Intermediate unissued narrative texts are not retained, although edit hashes are audited. No automatic database import from case ZIPs is implemented.

The manifest records each included file’s SHA-256 and byte length. `manifest.ed25519` authenticates canonical manifest bytes using the application’s signing key. `public-key.txt` is a convenience, not an identity trust anchor. Obtain and pin the expected key through a separately governed channel.

```bash
# An administrator obtains the public key through a controlled channel:
python -m openautopsyflow.cli public-key > trusted-application-public-key.txt

# Verification needs no deployment master key or active application:
python -m openautopsyflow.cli verify-bundle /secure/case-export.zip \
  --trusted-key trusted-application-public-key.txt
```

Without `--trusted-key`, the verifier reports `identity_anchored: false`. It can confirm internal cryptographic consistency but cannot tell whether an attacker replaced both the package and included key. The verifier rejects duplicate/unsafe archive names, altered bytes, unknown formats and unlisted/missing entries and never extracts files. Do not treat successful verification as a medical or legal verdict.

## Encrypted database backup

```bash
# Deployment configuration/master key already supplied through protected environment.
python -m openautopsyflow.cli verify-audit
python -m openautopsyflow.cli backup /secure/backups/casework-2026-09-05.oafbackup
```

Backup uses SQLite’s backup API for a consistent database snapshot, verifies local audit chains and encrypts the snapshot before writing an exclusively created destination with restrictive file mode. Existing backup filenames are not overwritten. The recorded SHA-256 is useful for transfer checks, not an independent authenticity claim. Store the master key separately from backups, verify access controls and test restore routinely. A compromised key or host defeats these controls.

## Restore drill

Stop the destination service. Keep the source deployment untouched. Select a new empty directory, supply the original master secret through a protected channel, and run:

```bash
export OAF_DATA_DIR=/secure/restored-oaf
python -m openautopsyflow.cli restore /secure/backups/casework-2026-09-05.oafbackup
python -m openautopsyflow.cli verify-audit
```

Restore refuses an existing database or nonempty destination, validates authenticated encryption, SQLite integrity, schema version and audit chains, then records recovery and revokes all restored sessions. This avoids resurrecting old login sessions. The implementation does not overwrite a running deployment. Verify case counts, original evidence/PDF digests, role assignments and selected report histories before directing users to the recovered instance.

The release tests exercise normal recovery, wrong keys, tampered ciphertext, nonempty destinations and session revocation on synthetic stores. Disaster scenarios involving compromised signing keys, corrupted storage devices, very large databases or operator error need a separately rehearsed institutional recovery plan.
