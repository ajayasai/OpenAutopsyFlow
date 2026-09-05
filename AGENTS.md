# Project instructions for coding agents

Build and test only with synthetic cases. Do not access live departmental databases or external patient records. Do not publish, deploy, change repository visibility or overwrite remote data without an explicit user request.

Core invariants: case membership is mandatory even for administrators; aggregate changes use revision comparison inside an immediate transaction; report edits require a report-version comparison; source changes invalidate pending approval; narrative refresh never rewrites opinion; historical report contributors cannot approve that report; issued PDFs/rows stay unchanged; supplements link to the latest issued version; originals/history/audit are not deleted by application workflows; warnings remain prompts for qualified human review.

Test commands:
```
python -m coverage run -m pytest
python -m coverage report --fail-under=80
node --check openautopsyflow/static/app.js
python scripts/browser_smoke.py
python scripts/release_manifest.py
python scripts/publish_github.py --dry-run
```

Browser harness is offline and bridges to the real local ASGI application; do not describe it as network/TLS/CSP validation. Record actual outcomes, never invent benchmark or CI success. Only plaintext source and explicitly synthetic screenshots/example PDFs may be released. Never copy font files, keys, credentials or runtime case stores.
