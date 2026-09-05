# Contributing

Use synthetic fixtures only. Preserve clinical authorship and evidence provenance. Do not add medical diagnosis, cause/manner inference or automated approval.

Set up a virtual environment, install `requirements.lock`, `requirements-dev.lock`, and the project in editable mode. Run `python -m coverage run -m pytest`, `python -m coverage report --fail-under=80`, and `node --check openautopsyflow/static/app.js`. UI work should also run `python scripts/browser_smoke.py` after installing the optional `ui` extra and Chromium.

A change to a workflow invariant needs both a positive and a rejected-action regression test. Every new endpoint must prove case-scoped authorization, role restrictions, schema validation, concurrency handling and audit behavior. Tests that alter database triggers must use disposable stores only. Never claim a malicious privileged administrator cannot bypass local controls.

Report new limitations honestly. Include negative results and exact benchmark conditions. Do not describe commercial products as lacking a feature merely because their public documentation is silent.

After reviewing the final source diff, regenerate `SOURCE_MANIFEST.json` with `python scripts/release_manifest.py`. Then run `python scripts/publish_github.py --dry-run`. The manifest is a checksum inventory, not an independent signature or proof of authorship. Publication must remain explicit and must not include runtime data.

Schema version 1 has no upgrade migration system yet. Introduce explicit, reversible, backup-first migrations before shipping a schema change to deployed installations. Adding `CREATE TABLE IF NOT EXISTS` is not a general migration strategy.
