# Local validation record — OpenAutopsyFlow 0.1.0

Recorded **5 September 2026**. All test case material is synthetic. Results below are observed local results, not production assurance or vendor-comparison outcomes.

## Executed checks

| Check | Observed result |
|---|---|
| Backend/API/publishing-safety pytest suite | **116 passed; 0 failed; 0 errors; 0 skipped**, 14.30 seconds |
| Python application coverage, including CLI | **85.20% combined statement/branch coverage**; 1295/1455 statements covered |
| Offline Chromium + real ASGI workflow harness | **15 checks passed**, zero JavaScript errors, 41 API requests including expected rejection paths |
| Responsive layout | No document-level horizontal overflow at 1440 px desktop and 390 px mobile widths in that harness |
| Loopback HTTP launcher smoke | Passed health, generated-account login, session-authenticated case list, HTML/CSS/JS routes and security-header presence |
| Python editable package installation | Passed with existing dependencies and no build isolation |
| JavaScript syntax | `node --check openautopsyflow/static/app.js` passed |
| PDF rendering | Two-page synthetic issued report rendered and visually inspected; no clipping, overlap or broken glyphs observed |

The coverage scope is `openautopsyflow`, including `cli.py`. It does not count JavaScript or helper scripts. Publishing-safety tests and browser/launcher checks are reported separately rather than inflating coverage. CLI interactive administration has lower coverage and requires additional operational testing.

## What the tests exercise

Case membership and role checks, cross-case access attempts, CSRF/origin rejection, login throttling, Argon2 password handling, idle/absolute expiry, TOTP replay rejection, account/session revocation, conflicting revisions, simultaneous stale writers, immutable injury numbering and original evidence, intake/finding history, lab-result review, source-reference failures, stale report snapshots, human warning rationales, blocking comments, historical contributor separation, issued-version/PDF preservation and supplementary-report parent constraints.

The suite also exercises encryption/hash failures, signed ZIP verification with and without a trusted key, archive manipulation, inclusion of finding history/reviewer discussion/templates without authentication secrets, encrypted backup round trips, wrong keys, tampered backups, nonempty restore refusal, restored-session revocation, bounded uploads and source-only publisher safety rules. Scanner contract tests are mocked; they are not successful real malware scans.

## Module coverage

| Module | Statements | Combined coverage |
|---|---:|---:|
| `openautopsyflow/__init__.py` | 1 | 100.0% |
| `openautopsyflow/api.py` | 343 | 86.6% |
| `openautopsyflow/cli.py` | 209 | 56.1% |
| `openautopsyflow/documents.py` | 102 | 87.0% |
| `openautopsyflow/rules.py` | 44 | 90.3% |
| `openautopsyflow/schemas.py` | 114 | 98.3% |
| `openautopsyflow/security.py` | 130 | 93.7% |
| `openautopsyflow/service.py` | 371 | 89.2% |
| `openautopsyflow/store.py` | 141 | 97.6% |

Machine-readable data: [test-results.json](test-results.json). UI harness outcomes: [browser-results.json](screenshots/browser-results.json). The original full ZIP contains the synthetic issued PDF; regenerate it with `scripts/browser_smoke.py` in this source-only GitHub edition.

## Performance observation

A sequential warm-cache in-process ASGI test created **1,000 synthetic cases and 3,000 tasks**, with **no photographs or reports**, then measured 30 requests per route. Case-list median was **28.95 ms**, empirical p95 **64.71 ms**; pending-only median **29.13 ms**. See [benchmark-results.json](benchmark-results.json) and `scripts/benchmark.py` for the exact method.

These are local request timings, not user-perceived network latency, concurrency capacity, production storage limits, a service-level commitment or evidence of beating a commercial product. The small sample and shared runtime can produce noisy tails.

## Important work not performed

No hosted GitHub Actions run, Docker build, fresh networked dependency install, current vulnerability scan, real ClamAV operation, independent penetration test, clinical/departmental acceptance study, Windows/macOS runtime check or live browser TLS/cookie/CSP deployment test was performed. Browser navigation to localhost was blocked by the environment; the offline browser harness uses an explicit in-process API bridge and does not claim to validate network/browser security delivery.

At the original build, GitHub publication had not occurred. The user subsequently created the public repository, and source publication proceeded through the existing-repository connector. The new-repository CLI publisher was not used for that upload. Local tests were rerun successfully before publication; hosted CI outcomes must be read separately from GitHub Actions. See [publication details](PUBLISHING.md).

Use synthetic data until the outstanding validation and operational gates are completed.
