# Validation record — OpenAutopsyFlow 0.2.0

Recorded 5 September 2026. All fixtures and browser screenshots are synthetic. These results establish specific software behavior, not medical suitability or superiority over a commercial system.

## Observed local checks

| Check | Result |
|---|---|
| Backend, workflow, authorization, migration, checkpoint and publishing tests | **165 passed**, no failures or skips |
| Combined application statement/branch coverage | **86.81%**; 1,626 of 1,802 statements and 414 of 548 branches covered |
| New review workbench, offline Chromium/real-ASGI bridge | **10 checks passed**, zero JavaScript errors |
| Existing application offline browser regression | **16 checks passed**, zero JavaScript errors |
| Responsive workbench | No document-level horizontal overflow at 390 px and 1440 px; both rendered screenshots inspected |
| JavaScript syntax | Both `app.js` and `workbench.js` passed |

The environment used Python 3.13.5 and cached FastAPI 0.128.2, Starlette 0.50.0 and Cryptography 46.0.4. **This is not the patched production dependency lock.** Networked installs were unavailable locally; compatibility with the newer published lock and its vulnerability status must be read from hosted CI. Do not downgrade the runtime lock to reproduce the cached environment.

Machine-readable observations: [validation-0.2.json](validation-0.2.json). Earlier 0.1 observations remain separately documented in [VALIDATION.md](VALIDATION.md); they are not new measurements.

## Regressions added in this release

Tests exercise missing receipts; wrong case, reviewer, content digest, evidence hash or version; evidence not opened; duplicate attestation; resubmission invalidating old receipts; quarantine after review; reviewer disablement before issue; preservation of issued PDF bytes; original evidence rendering; exact historical narratives and source differences; immutable receipts and history; concurrent stale report writers; history pagination; source retirement; absent versus null fields; migration rollback including DDL; wrong-key rejection before schema changes; migration checksum mismatch; future-version refusal; honest legacy baselines; and checkpoint truncation, scoped-chain mismatch, forgery and locally rehashed history detection.

Receipt tests establish recorded acts by authenticated accounts. They cannot prove a reviewer understood an original or made a correct medical judgment. Per-case authorization tests are not a substitute for independent penetration testing.

## Hosted validation added

The checked-in workflow runs the patched dependency lock on Python **3.11, 3.12 and 3.13**, checks dependency consistency, enforces **85%** combined coverage, checks both JavaScript files and the source inventory, and runs the runtime vulnerability audit without an ignore list.

Two additional jobs exercise the actual review UI over loopback HTTPS in Chromium and build/start the non-root Docker image. The HTTPS harness checks Secure/HttpOnly/SameSite cookies, delivered CSP, same-origin original-image rendering, explicit review and issuance. It uses a temporary self-signed certificate with `ignore_https_errors=True`; it **does not validate public certificate trust or a production reverse proxy**. The Docker check is a build/startup check, not load or availability validation.

Consult the [current GitHub Actions results](https://github.com/ajayasai/OpenAutopsyFlow/actions) for the exact tested commit and job outcomes. Configuration alone is not evidence that a hosted job passed.

Local browser navigation to a server was blocked by this environment. Local browser results use an explicitly labelled offline ASGI bridge; they do not validate transport, TLS, cookie delivery or served CSP. Reproduce a real-network test on an allowed development host with:

```bash
python -m pip install -r requirements.lock -r requirements-dev.lock playwright==1.61.0
python -m pip install --no-deps -e .
python -m playwright install chromium
python scripts/workbench_smoke.py --mode live
```

Use `--mode offline` only for a separately labelled DOM/workflow check.

## Remaining validation gates

Independent security review, real departmental acceptance, jurisdiction-specific report validation, production key custody, recovery drills at realistic case volumes, sustained concurrent load, and commercial head-to-head evaluations remain outstanding. Database history and receipts are plaintext structured metadata; protected encrypted storage is still required. Local append-only triggers are not protection against a privileged database administrator.

See the [competitive evaluation protocol](COMPETITIVE_VALIDATION.md) rather than interpreting regression-test totals as a product ranking.
