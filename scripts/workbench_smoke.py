#!/usr/bin/env python3
"""Synthetic review-workbench browser checks. Live HTTPS by default; offline is explicit.

Live mode uses a local self-signed test certificate and ignore_https_errors=True:
this tests browser HTTPS cookies/CSP, NOT certificate trust or a production proxy.
Offline mode uses an ASGI bridge and makes NO network/TLS/CSP-validation claim.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timedelta, timezone
import io
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from openautopsyflow.api import create_app
from openautopsyflow.cli import seed_demo
from openautopsyflow import schemas as S, service as V
from openautopsyflow.store import Settings, canonical, digest
from browser_smoke import BRIDGE_JS


def fixtures(settings, photo=False):
    app = create_app(settings)
    credentials = seed_demo(app.state.store)
    store = app.state.store
    with store.transaction() as db:
        examiner = dict(db.execute("SELECT id FROM users WHERE username='examiner'").fetchone())
        row = db.execute("SELECT id FROM reports WHERE status='in_review'").fetchone()
        rid = row['id']; report = V.report_row(db, rid); cid = report['case_id']
        before_version = report['version']
        V.transition(db, store, report, examiner, 'return', S.ReportAction(version=report['version']))
        raw = b'SYNTHETIC LAB RESULT. <script>window.xss_should_not_run=1</script> No clinical conclusion.'
        V.add_evidence(db, store, cid, examiner, V.case_row(db, cid)['revision'], 'lab_result', None,
                       'synthetic-lab.txt', 'text/plain', raw, ('clean', 'synthetic-test-fixture'))
        if photo:
            image = io.BytesIO(); Image.new('RGB', (32, 32)).save(image, format='PNG')
            finding = db.execute("SELECT id FROM records WHERE case_id=? AND kind='injury'", (cid,)).fetchone()[0]
            V.add_evidence(db, store, cid, examiner, V.case_row(db, cid)['revision'], 'photo', finding,
                           'synthetic-photo.png', 'image/png', image.getvalue(), ('clean', 'synthetic-test-fixture'))
        report = V.report_row(db, rid)
        V.transition(db, store, report, examiner, 'refresh', S.ReportAction(version=report['version']))
        report = V.report_row(db, rid)
        for section in report['sections']:
            if section['key'] == 'opinion':
                section['text'] = 'SYNTHETIC WORKBENCH REVISION. No medical opinion is offered. Pending work is documented.'
        V.edit_report(db, report, examiner, S.ReportUpdate(version=report['version'], sections=report['sections']))
        report = V.report_row(db, rid)
        acks = {i['id']: 'Synthetic testing rationale; pending materials remain explicitly unresolved.'
                for i in V.checks(db, report)['issues'] if i['severity'] == 'warning'}
        V.transition(db, store, report, examiner, 'submit', S.ReportAction(version=report['version'], acknowledgements=acks))
    return app, credentials, rid, before_version


def certificate(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'Synthetic loopback test')])
    stamp = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(stamp - timedelta(minutes=1))
            .not_valid_after(stamp + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / 'test-cert.pem', directory / 'test-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    key_path.chmod(0o600)
    return cert_path, key_path


def run(output: Path, mode='live', chromium=None):
    output.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    server = thread = sock = client = None
    with tempfile.TemporaryDirectory(prefix='oaf-workbench-') as temporary:
        directory = Path(temporary)
        if mode == 'live':
            sock = socket.socket(); sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]; origin = f'https://127.0.0.1:{port}'
        else:
            origin = 'https://testserver'
        settings = Settings(directory / 'data', secrets.token_bytes(32), True, True,
                            ('testserver', '127.0.0.1'), (origin,))
        app, credentials, rid, before = fixtures(settings, photo=mode == 'live')
        try:
            if mode == 'live':
                import uvicorn
                cert, key = certificate(directory)
                config = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error',
                                        access_log=False, proxy_headers=False, ssl_certfile=str(cert), ssl_keyfile=str(key))
                server = uvicorn.Server(config)
                thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
                thread.start()
                deadline = time.monotonic() + 15
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.05)
                if not server.started:
                    raise RuntimeError('Test HTTPS server did not start')
            else:
                client = TestClient(app, base_url=origin, headers={'Origin': origin})
                login = client.post('/api/login', json={'username': 'reviewer', 'password': credentials['reviewer']})
                assert login.status_code == 200, login.text
            with sync_playwright() as playwright:
                executable = chromium or shutil.which('chromium') or shutil.which('chromium-browser')
                browser = playwright.chromium.launch(headless=True, executable_path=executable)
                context = browser.new_context(viewport={'width': 1440, 'height': 1040}, ignore_https_errors=True)
                page = context.new_page(); page.set_default_timeout(15000)
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('dialog', lambda d: d.accept())
                if mode == 'live':
                    response = page.goto(origin + '/')
                    assert response.status == 200
                    page.get_by_label('Username', exact=True).fill('reviewer')
                    page.get_by_label('Password', exact=True).fill(credentials['reviewer'])
                    page.get_by_role('button', name='Sign in to workspace').click()
                    page.wait_for_selector('.case-link')
                    cookie = next(c for c in context.cookies() if c['name'] == 'oaf_session')
                    assert cookie['httpOnly'] and cookie['secure'] and cookie['sameSite'] == 'Strict'
                    assert 'oaf_session=' not in page.evaluate('document.cookie')
                    checks.append('Real HTTPS login: Secure, HttpOnly, SameSite=Strict cookie')
                    response = page.goto(origin + '/review?report=' + rid)
                    assert "script-src 'self'" in response.headers['content-security-policy']
                    page.evaluate("const s=document.createElement('script');s.textContent='window.inline_should_not_run=1';document.body.append(s)")
                    assert page.evaluate('typeof window.inline_should_not_run') == 'undefined'
                    checks.append('Delivered CSP blocks inline script execution')
                else:
                    def bridge(url, method, headers, body, form):
                        if not url.startswith('/api/') or form:
                            raise ValueError('Only synthetic application API requests are supported')
                        r = client.request(method, url, headers=headers, content=body)
                        return {'status': r.status_code, 'headers': dict(r.headers),
                                'data': base64.b64encode(r.content).decode()}
                    page.expose_function('_oafRequest', bridge)
                    html = (ROOT / 'openautopsyflow/static/workbench.html').read_text()
                    html = re.sub(r'<link[^>]+>', '', html)
                    html = re.sub(r'<script[^>]*></script>', '', html)
                    page.set_content(html)
                    page.add_style_tag(content=(ROOT / 'openautopsyflow/static/workbench.css').read_text())
                    page.add_script_tag(content=BRIDGE_JS)
                    page.add_script_tag(content=(ROOT / 'openautopsyflow/static/workbench.js').read_text())
                    page.wait_for_selector('[data-wb="case"]')
                    page.evaluate('(id)=>loadReport(id)', rid)
                page.wait_for_selector('.wb-attest')
                assert page.locator('[data-wb="approve"]').is_disabled()
                checks.append('Approval disabled while required review receipts are missing')
                page.locator('#wb-from').select_option(str(before))
                page.locator('[data-wb="compare"]').click()
                expect(page.locator('#wb-comparison')).to_contain_text('SYNTHETIC WORKBENCH REVISION', timeout=15000)
                checks.append('Preserved revisions compare exact before/after narrative')
                evidence_ids = page.locator('[data-evidence]').evaluate_all('(nodes)=>nodes.map(x=>x.dataset.evidence)')
                first = page.locator(f'[data-evidence="{evidence_ids[0]}"]')
                first.locator('textarea').fill('Synthetic human attestation attempted before original opening.')
                first.locator('input[type=checkbox]').check()
                first.get_by_role('button', name='Record evidence review').click()
                expect(page.locator('#wb-error')).to_contain_text('Open the original', timeout=15000)
                checks.append('Server rejects a receipt before this reviewer opens the original')
                for eid in evidence_ids:
                    article = page.locator(f'[data-evidence="{eid}"]')
                    article.get_by_role('button', name='Open original', exact=True).click()
                    page.wait_for_selector('#preview-' + eid + ' .wb-preview')
                    if article.locator('img').count():
                        article.locator('img').wait_for(state='visible')
                        expect(article.locator('img')).to_have_js_property('naturalWidth', 32, timeout=15000)
                        checks.append('Same-origin original image preview works under delivered CSP')
                    assert page.evaluate('typeof window.xss_should_not_run') == 'undefined'
                    article.locator('textarea').fill('Synthetic reviewer: I inspected this exact original against the report.')
                    article.locator('input[type=checkbox]').check()
                    article.get_by_role('button', name='Record evidence review').click()
                    expect(article.locator('.wb-badge')).to_have_text('Attested', timeout=15000)
                checks.append('All required originals explicitly attested through visible forms')
                checks.append('Evidence text is rendered inertly; embedded script text does not execute')
                assert page.locator('[data-wb="approve"]').is_enabled()
                page.screenshot(path=str(output / 'workbench-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                page.screenshot(path=str(output / 'workbench-mobile.png'), full_page=True)
                checks.append('390-pixel layout has no document-level horizontal overflow')
                page.set_viewport_size({'width': 1440, 'height': 1040})
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                checks.append('1440-pixel layout has no document-level horizontal overflow')
                page.locator('[data-wb="approve"]').click()
                page.wait_for_selector('[data-wb="issue"]')
                page.locator('[data-wb="issue"]').click()
                expect(page.locator('#wb-report > .wb-card').first.locator('.wb-badge').first).to_have_text('issued', timeout=15000)
                assert page.locator('[data-wb="issue"]').count() == 0
                assert page.locator('.wb-attest').count() == 0
                checks.append('Independent approval and issuance preserve receipts and freeze editing')
                if mode == 'live':
                    original_pdf = context.request.get(origin + f'/api/reports/{rid}/pdf').body()
                    later_pdf = context.request.get(origin + f'/api/reports/{rid}/pdf').body()
                else:
                    original_pdf = client.get(f'/api/reports/{rid}/pdf').content
                    later_pdf = client.get(f'/api/reports/{rid}/pdf').content
                assert original_pdf.startswith(b'%PDF') and digest(original_pdf) == digest(later_pdf)
                checks.append('Repeated issued-PDF download preserves exact bytes')
                assert errors == [], errors
                checks.append('Zero JavaScript runtime errors')
                browser.close()
        finally:
            if client:
                client.close()
            if server:
                server.should_exit = True
            if thread:
                thread.join(timeout=10)
            if sock:
                sock.close()
    result = {'mode': mode, 'checks_passed': len(checks), 'checks': checks, 'javascript_errors': errors,
              'test_data': 'Synthetic only; credentials and test TLS keys were temporary and are not exported.',
              'limitations': ['Not a clinical evaluation, penetration test, or vendor comparison.',
                              'Self-signed test certificate trust is bypassed; public-PKI trust is not tested.']
              if mode == 'live' else ['Offline ASGI bridge; no browser network, TLS, cookie transport, or delivered CSP validation.']}
    (output / 'workbench-results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/workbench')
    parser.add_argument('--mode', choices=['live', 'offline'], default='live')
    parser.add_argument('--chromium', default=None)
    args = parser.parse_args()
    run(args.output, args.mode, args.chromium)
