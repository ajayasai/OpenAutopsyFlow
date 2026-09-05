# Deployment runbook

## Release gate

This is an initial research/development release. Real deployment needs a qualified departmental owner, validated local forms and workflows, security review, reliable encrypted storage/backups, access governance, operational monitoring, incident response and applicable institutional/legal review. No jurisdictional compliance is asserted.

The local validation ran on Linux/Python 3.13.5. Docker configuration and CI target versions are supplied but were not built/run in this environment. Dependency installation from a clean networked host and a current vulnerability audit remain required.

## Dedicated non-demo configuration

Run under a dedicated unprivileged operating-system account. Keep runtime data outside the repository. Store the master secret in a proper secret manager or tightly protected file outside Git. Protect memory, swap and temporary directories as well as the database volume. The following illustrates configuration, not a complete hardened installation:

```bash
umask 077
python -m openautopsyflow.cli keygen > /secure/operator-managed/oaf-master.key
export OAF_MASTER_KEY="$(cat /secure/operator-managed/oaf-master.key)"
export OAF_DATA_DIR=/var/lib/openautopsyflow
export OAF_DEMO=0
export OAF_HOSTS=casework.example.invalid
export OAF_ORIGINS=https://casework.example.invalid
export OAF_SCANNER=/usr/bin/clamscan
python -m openautopsyflow.cli init
```

Create the directories securely first and replace the example domain. The CLI prompts for the first administrator’s password. Never put the key in a command-line argument, source file, screenshot or shell debug log. Losing it prevents recovery of protected artifacts. Rotating it is **not** an environment-variable change: the current store refuses a different key and has no rotation workflow.

Serve the backend only on a private/local interface behind a correctly configured HTTPS proxy. Enforce body-size, connection and request-rate limits at that proxy. Forwarded client IPs must come only from explicitly trusted proxies; do not trust arbitrary forwarding headers.

```bash
python -m uvicorn openautopsyflow.api:create_app --factory \
  --host 127.0.0.1 --port 8000 --no-proxy-headers
```

This example does not trust proxy headers, so per-IP throttling sees the proxy address. Configure a narrow forwarded-IP allow-list only after validating the proxy topology. Do not expose backend HTTP to users. Set proxy TLS/HSTS policy, secure host/origin configuration, access logs without request-body logging, and appropriate monitoring.

## Malware scanning

`OAF_SCANNER` must name a trusted, installed ClamAV-compatible executable. The process calls it without a shell, with a private temporary file and a 60-second timeout. Only exit code 0 clears quarantine. Exit 1, other failures, absence or timeout keep it quarantined. Maintain signatures and test with harmless industry-standard scanner test material in an isolated environment. No real ClamAV scan was performed during local validation; only the contract/failure paths were tested with mocks.

After scanner repair, `python -m openautopsyflow.cli rescan` processes quarantined originals and records an event. It does not bypass scan failure. Temporary plaintext scanner/backup material requires encrypted/protected temporary storage; secure deletion is not guaranteed by normal filesystem cleanup.

## Account operations

```bash
python -m openautopsyflow.cli user reviewer01 --name 'Assigned reviewer'
python -m openautopsyflow.cli mfa-enroll reviewer01
python -m openautopsyflow.cli reset-password reviewer01
python -m openautopsyflow.cli mfa-reset reviewer01 --confirm-console-recovery
```

The MFA enrollment secret is printed for controlled registration in a TOTP authenticator. Protect it and verify enrollment. Console reset is recovery, not a self-service workflow. Restrict host access and record operational authorization. Assign case roles separately in the workspace. Revocation prevents subsequent authorized requests; in-flight work needs incident handling.

## PDF text

The default renderer fails closed for text outside its supported basic Latin-1 path rather than silently substituting unsupported glyphs. `OAF_PDF_FONT` can point to an appropriately licensed local TrueType font; the renderer checks glyph availability. Complex-script shaping, bidirectional layout and language-specific legal forms have not been validated. A font file alone does not guarantee correct Tamil or other complex-script output. No fonts are distributed here.

## Docker demonstration

```bash
docker compose build
docker compose run --rm app python -m openautopsyflow.cli demo
docker compose up
```

Save the random demo passwords privately. The sample Compose file binds only localhost, uses a non-root service, a read-only root filesystem and a dedicated demo volume. It deliberately enables insecure demo behavior and is **not a production deployment template**. The image does not bundle ClamAV or a TLS proxy. Pin/review image digests and scan the built image before a real deployment. Docker was not available for local build validation.

Before every release: run tests; resolve current dependency vulnerabilities; review migrations; test backup/restore in isolation; perform live-browser cookie/CSRF/CSP/proxy tests; verify no real records or keys entered the release tree. A checksum manifest is not a substitute for these steps.
