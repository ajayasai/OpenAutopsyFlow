# Repository publication and source integrity

Public repository: **[ajayasai/OpenAutopsyFlow](https://github.com/ajayasai/OpenAutopsyFlow)**, branch `main`.

The user created the public repository, and the existing-repository connector was used to upload the application source, tests and documentation. No unrelated repository was modified. This is source publication, not a live deployment.

## GitHub edition

The runnable application, all backend tests, scripts, dependency locks and documentation are included. Generated screenshots, the sample issued PDF and the static OpenAPI snapshot from the original full ZIP are not committed. Regenerate synthetic screenshots and a sample PDF with `python scripts/browser_smoke.py`; read the running API schema through authenticated `/api/schema`. `SOURCE_MANIFEST.json` inventories the files actually published in this edition.

## Verify and contribute

```bash
python scripts/publish_github.py --dry-run
python -m pytest
```

The dry-run verifies file hashes, its source allow-list, obvious token/private-key patterns and runtime-file exclusions without contacting GitHub. After reviewing legitimate source changes and passing tests, regenerate the inventory with `python scripts/release_manifest.py` and inspect the diff. A checksum inventory is not a signed external attestation and cannot determine whether an arbitrary narrative contains private information.

Use normal reviewed Git commits and pull requests to update this repository. Keep credentials on your machine; never commit or paste tokens, runtime databases or real case records into public issues, chat or source files.

## Optional creation of a different repository

`scripts/publish_github.py` retains its original new-repository mode for a separately named project. It requires Git, a locally authenticated GitHub CLI and your configured Git author identity. It refuses an existing repository or a mismatched authenticated owner, stages only manifest-listed source, and does not force-push or change visibility.

```bash
# Only for a NEW repository that does not already exist:
gh auth login
python scripts/publish_github.py --owner YOUR_ACCOUNT --name NEW_REPOSITORY_NAME
```

Do not run the non-dry-run command against `ajayasai/OpenAutopsyFlow`: this repository already exists. That script's actual new-repository creation/push path was not used for this publication; its local safety checks were tested.

Private vulnerability reporting, branch protection and hosted Actions approvals are separate repository settings. Their availability or completion must be verified independently; publication alone does not establish them.
