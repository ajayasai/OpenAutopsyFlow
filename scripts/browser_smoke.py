#!/usr/bin/env python3
"""Offline Chromium UI + real in-process ASGI checks on disposable synthetic data.

No browser networking is used. This deliberately does NOT validate reverse proxy,
TLS, real browser cookie delivery, or CSP enforcement. Test those on staging.
"""
from __future__ import annotations
import argparse
import base64
import json
import re
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from openautopsyflow.api import create_app
from openautopsyflow.cli import seed_demo
from openautopsyflow.store import Settings

BRIDGE_JS = """window.fetch=async function(url,options={}) {
 let form=null,body=options.body??null;
 if(body instanceof FormData){form=[];for(const [name,value] of body){
   if(value instanceof File){const bytes=new Uint8Array(await value.arrayBuffer());
     let raw='';for(const n of bytes)raw+=String.fromCharCode(n);
     form.push({name,filename:value.name,type:value.type,data:btoa(raw)});
   }else form.push({name,value});}body=null;}
 const r=await window._oafRequest(String(url),options.method||'GET',options.headers||{},body,form);
 return new Response(Uint8Array.from(atob(r.data),x=>x.charCodeAt(0)),{status:r.status,headers:r.headers});
};"""


def run(output: Path, chromium: str | None = None):
    output.mkdir(parents=True, exist_ok=True)
    checks, requests, errors = [], [], []
    with tempfile.TemporaryDirectory(prefix='oaf-ui-') as temporary:
        settings = Settings(Path(temporary), secrets.token_bytes(32), True, False,
                            ('testserver',), ('http://testserver',))
        app = create_app(settings)
        credentials = seed_demo(app.state.store)
        client = TestClient(app, headers={'Origin': 'http://testserver'})

        def bridge(url, method, headers, body, form):
            if not url.startswith('/api/'):
                raise ValueError('Offline harness only permits local application API calls')
            kwargs = {'headers': headers}
            if form is not None:
                kwargs['data'] = {x['name']: x['value'] for x in form if 'value' in x}
                kwargs['files'] = {x['name']: (x['filename'], base64.b64decode(x['data']), x['type'])
                                   for x in form if 'data' in x}
            elif body is not None:
                kwargs['content'] = body
            response = client.request(method, url, **kwargs)
            requests.append({'method': method, 'url': url, 'status': response.status_code})
            return {'status': response.status_code, 'headers': dict(response.headers),
                    'data': base64.b64encode(response.content).decode()}

        with sync_playwright() as playwright:
            executable = chromium or shutil.which('chromium') or shutil.which('chromium-browser')
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
            page = browser.new_page(viewport={'width': 1440, 'height': 1040})
            page.set_default_timeout(10000)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())
            page.expose_function('_oafRequest', bridge)
            html = (ROOT/'openautopsyflow/static/index.html').read_text()
            html = re.sub(r'<link[^>]+>', '', html)
            html = re.sub(r'<script[^>]*></script>', '', html)
            page.set_content(html)
            page.add_style_tag(content=(ROOT/'openautopsyflow/static/app.css').read_text())
            page.add_script_tag(content=BRIDGE_JS)
            page.add_script_tag(content=(ROOT/'openautopsyflow/static/app.js').read_text())

            def click(action):
                page.locator(f'[data-action="{action}"]').click()

            def login(name):
                page.get_by_label('Username', exact=True).fill(name)
                page.get_by_label('Password', exact=True).fill(credentials[name])
                page.get_by_role('button', name='Sign in to workspace').click()

            login('examiner')
            page.wait_for_selector('.case-link')
            assert page.locator('.case-link').count() == 3
            checks.append('Login and assigned-case dashboard')
            page.screenshot(path=str(output/'dashboard.png'), full_page=True)
            page.locator('.case-link', has_text='0001').click()
            page.wait_for_selector('[data-action="case-tab"]')
            page.screenshot(path=str(output/'examination.png'), full_page=True)
            click('intake-history')
            assert page.get_by_role('heading', name='Intake amendment history').is_visible()
            assert 'Initial intake' in page.locator('#modal-content').inner_text()
            click('close')
            checks.append('Intake history dialog')

            page.locator('[data-action="case-tab"][data-id="reports"]').click()
            page.locator('a[href^="#/report"]').first.click()
            page.wait_for_selector('#section-opinion')
            assert 'Injury 4' in page.locator('.report-side').inner_text()
            checks.append('Missing injury reference displayed')
            page.screenshot(path=str(output/'report-traceability.png'), full_page=True)
            page.locator('#section-opinion').fill('SYNTHETIC UI TEST ONLY. No medical opinion is offered.')
            click('report-submit')
            assert 'Save your narrative changes' in page.locator('#toast').inner_text()
            checks.append('Unsaved narrative cannot be submitted')
            click('save-report')
            page.wait_for_function('state.dirty === false && state.report.version >= 3')
            checks.append('Draft save persists typed narrative')
            page.locator('a', has_text='Return to case').click()
            page.locator('[data-action="case-tab"][data-id="examination"]').click()
            page.locator('[data-action="new-record"][data-kind="injury"]').click()
            page.get_by_label('Injury number', exact=True).fill('4')
            page.get_by_label('Observed findings / description', exact=True).fill('SYNTHETIC UI fixture for reference resolution.')
            page.get_by_role('button', name='Save record', exact=True).click()
            page.wait_for_function('!document.querySelector("#modal").open')
            checks.append('Structured injury form saves through real API')
            page.locator('[data-action="case-tab"][data-id="reports"]').click()
            page.locator('a[href^="#/report"]').first.click()
            page.wait_for_selector('#section-opinion')
            assert 'out of date' in page.locator('#main').inner_text()
            checks.append('Source mutation visibly makes draft stale')
            click('report-refresh')
            page.wait_for_function('state.report.source_revision === state.case.revision')
            assert 'Missing Reference' not in page.locator('.report-side').inner_text()
            assert page.locator('#section-opinion').input_value() == 'SYNTHETIC UI TEST ONLY. No medical opinion is offered.'
            checks.append('Refresh resolves reference without overwriting opinion')
            for rationale in page.locator('[data-ack]').all():
                rationale.fill('Synthetic exercise: pending work remains explicitly documented, with no final medical opinion.')
            click('report-submit')
            page.wait_for_function('state.report.status === "in_review"')
            checks.append('Warning acknowledgements and submission')
            report_id = page.evaluate('state.report.id')
            click('logout')
            login('reviewer')
            page.wait_for_selector('[data-action="report-approve"]')
            assert not page.locator('[data-action="save-report"]').count()
            checks.append('Reviewer view does not allow narrative editing')
            click('new-comment')
            page.get_by_label('Comment', exact=True).fill('Synthetic reviewer comment requiring an explicit resolution.')
            page.get_by_label('Review effect', exact=True).select_option('true')
            page.get_by_role('button', name='Save record', exact=True).click()
            page.wait_for_function('!document.querySelector("#modal").open')
            click('report-approve')
            page.wait_for_function('document.querySelector("#toast").textContent.includes("blocking")')
            assert page.evaluate('state.report.status') == 'in_review'
            checks.append('Blocking reviewer comment prevents approval')
            click('resolve-comment')
            page.wait_for_function('state.report.comments.every(c=>c.resolved_at)')
            click('report-approve')
            page.wait_for_function('state.report.status === "approved"')
            click('report-issue')
            page.wait_for_function('state.report.status === "issued"')
            assert not page.locator('[data-action="report-return"]').count()
            checks.append('Resolve, independent approval and immutable issue')
            response = client.get('/api/reports/' + report_id + '/pdf')
            assert response.status_code == 200 and response.content.startswith(b'%PDF')
            (output/'synthetic-issued-report.pdf').write_bytes(response.content)
            checks.append('Preserved issued PDF retrieved from application')

            page.set_viewport_size({'width': 390, 'height': 844})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            page.screenshot(path=str(output/'mobile-report.png'), full_page=True)
            checks.append('390-pixel mobile layout has no document overflow')
            page.set_viewport_size({'width': 1440, 'height': 1040})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not errors, errors
            checks.append('Desktop layout and JavaScript runtime checks')
            browser.close()
        client.close()
    result = {'mode':'offline Chromium + in-process ASGI; not live-network browser E2E',
              'checks_passed':len(checks),'checks':checks,'javascript_errors':errors,
              'api_requests':len(requests),'expected_negative_responses':[
                  x for x in requests if x['status'] >= 400],
              'not_tested':['TLS/reverse proxy','browser cookie transport','real browser CSP enforcement']}
    (output/'browser-results.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/browser')
    parser.add_argument('--chromium', default=None)
    args = parser.parse_args()
    run(args.output, args.chromium)
