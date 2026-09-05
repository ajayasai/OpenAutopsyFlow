"""Synthetic end-to-end review and provenance invariants, including adversarial paths."""
import copy
import io
import json
import secrets
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from cryptography.exceptions import InvalidSignature
from fastapi.testclient import TestClient
from openautopsyflow import migrations as M, review as R, service as V
from openautopsyflow.documents import verify_bundle
from openautopsyflow.store import Store, audit, canonical, digest
from conftest import case, add_record, draft, action, issued


def original(env, cid, kind='lab_result', finding_id=''):
    client = env.clients['examiner']
    c = client.get('/api/cases/' + cid).json()
    response = client.post(f'/api/cases/{cid}/evidence', data={
        'revision': c['revision'], 'kind': kind, 'finding_id': finding_id},
        files={'file': ('synthetic.txt', b'Synthetic evidence ONLY. <script>never_execute()</script>', 'text/plain')})
    assert response.status_code == 201, response.text
    return response.json()['id']


def prepared(env, kind='lab_result', linked=False):
    cid = case(env)
    record_id = add_record(env, cid) if linked else ''
    eid = original(env, cid, kind, record_id)
    rid = draft(env, cid)
    assert action(env, rid, 'submit').status_code == 200
    return cid, rid, eid


def receipt_payload(env, rid, eid, role='reviewer'):
    w = env.clients[role].get(f'/api/reports/{rid}/workbench').json()
    e = next(e for e in w['review']['required'] if e['id'] == eid)
    return {'version': w['report']['version'], 'evidence_id': eid,
            'evidence_sha256': e['sha256'], 'basis_digest': w['review']['basis_digest'],
            'statement': 'Synthetic review attestation after reading the exact original.', 'acknowledged': True}


def attest(env, rid, eid, role='reviewer'):
    client = env.clients[role]
    opened = client.get(f'/api/reports/{rid}/review-evidence/{eid}')
    assert opened.status_code == 200, opened.text
    response = client.post(f'/api/reports/{rid}/review-receipts', json=receipt_payload(env, rid, eid, role))
    assert response.status_code == 201, response.text
    return response


def test_full_receipt_approval_issue_and_export(env):
    cid, rid, eid = prepared(env)
    failure = action(env, rid, 'approve', 'reviewer')
    assert failure.status_code == 409
    assert failure.json()['detail']['code'] == 'review_receipts_required'
    attest(env, rid, eid)
    assert action(env, rid, 'approve', 'reviewer').status_code == 200
    assert action(env, rid, 'issue', 'coordinator').status_code == 200
    client = env.clients['reviewer']
    before = client.get(f'/api/reports/{rid}/pdf').content
    add_record(env, cid, 'task', 'New synthetic request', {'text': 'Additional request'})
    assert client.get(f'/api/reports/{rid}/pdf').content == before
    workbench = client.get(f'/api/reports/{rid}/workbench').json()
    assert workbench['review']['remaining'] == 0
    assert workbench['checks']['case_changed_since_issue'] is True
    bundle = client.post(f'/api/cases/{cid}/export?include_evidence=true').content
    assert verify_bundle(bundle, env.store.public_key())['integrity_verified']
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        history = json.loads(z.read('report-history.json'))
        receipts = json.loads(z.read('review-receipts.json'))
        assert history[-1]['data']['status'] == 'issued'
        assert receipts[0]['evidence_id'] == eid
        assert receipts[0]['reviewer_id'] == env.users['reviewer']['id']
        assert b'ciphertext' not in z.read('report-history.json')
        assert b'password_hash' not in z.read('report-history.json')


def test_open_original_required_before_attestation(env):
    cid, rid, eid = prepared(env)
    p = receipt_payload(env, rid, eid)
    response = env.clients['reviewer'].post(f'/api/reports/{rid}/review-receipts', json=p)
    assert response.status_code == 409 and 'Open the original' in response.text


@pytest.mark.parametrize('role', ['examiner', 'coordinator', 'auditor', 'outsider'])
def test_non_independent_roles_cannot_attest(env, role):
    cid, rid, eid = prepared(env)
    p = receipt_payload(env, rid, eid)
    response = env.clients[role].post(f'/api/reports/{rid}/review-receipts', json=p)
    assert response.status_code in (403, 404)


@pytest.mark.parametrize('change', [
    {'basis_digest': 'a' * 64}, {'evidence_sha256': 'b' * 64}, {'version': 1},
    {'evidence_id': 'not-in-this-report'}, {'acknowledged': False}, {'statement': 'too short'},
    {'statement': 'x' * 2001}, {'version': 0}, {'evidence_sha256': 'xyz'}, {'unknown': 'field'},
])
def test_receipts_reject_mismatch_or_invalid_inputs(env, change):
    cid, rid, eid = prepared(env)
    reviewer = env.clients['reviewer']
    assert reviewer.get(f'/api/reports/{rid}/review-evidence/{eid}').status_code == 200
    p = receipt_payload(env, rid, eid)
    p.update(change)
    response = reviewer.post(f'/api/reports/{rid}/review-receipts', json=p)
    assert response.status_code in (409, 422), response.text
    with env.store.read() as db:
        assert db.execute('SELECT COUNT(*) FROM review_receipts').fetchone()[0] == 0


def test_attestation_does_not_make_source_stale(env):
    cid, rid, eid = prepared(env)
    before = env.clients['examiner'].get('/api/cases/' + cid).json()['revision']
    attest(env, rid, eid)
    assert env.clients['examiner'].get('/api/cases/' + cid).json()['revision'] == before
    w = env.clients['reviewer'].get(f'/api/reports/{rid}/workbench').json()
    assert w['review']['remaining'] == 0
    assert not any(i['code'] == 'stale_snapshot' for i in w['checks']['issues'])


def test_duplicate_receipt_is_rejected_not_replaced(env):
    cid, rid, eid = prepared(env)
    attest(env, rid, eid)
    p = receipt_payload(env, rid, eid)
    p['statement'] = 'A different synthetic statement must not replace the old one.'
    assert env.clients['reviewer'].post(f'/api/reports/{rid}/review-receipts', json=p).status_code == 409
    with env.store.read() as db:
        assert db.execute('SELECT COUNT(*) FROM review_receipts').fetchone()[0] == 1


def test_return_resubmit_invalidates_old_receipts_even_without_edits(env):
    cid, rid, eid = prepared(env)
    attest(env, rid, eid)
    assert action(env, rid, 'return', 'reviewer').status_code == 200
    assert action(env, rid, 'submit').status_code == 200
    assert action(env, rid, 'approve', 'reviewer').status_code == 409
    # The old file-open audit event also cannot be reused for a new round.
    p = receipt_payload(env, rid, eid)
    assert env.clients['reviewer'].post(f'/api/reports/{rid}/review-receipts', json=p).status_code == 409
    attest(env, rid, eid)
    assert action(env, rid, 'approve', 'reviewer').status_code == 200


def test_attestation_on_stale_sources_is_rejected(env):
    cid, rid, eid = prepared(env)
    reviewer = env.clients['reviewer']
    reviewer.get(f'/api/reports/{rid}/review-evidence/{eid}')
    payload = receipt_payload(env, rid, eid)
    add_record(env, cid, 'task', 'A new source task', {})
    assert reviewer.post(f'/api/reports/{rid}/review-receipts', json=payload).status_code == 409


def test_receipts_are_per_reviewer_not_shared(env):
    cid, rid, eid = prepared(env)
    c = env.clients['examiner'].get('/api/cases/' + cid).json()
    assert env.clients['examiner'].post(f'/api/cases/{cid}/members', json={
        'revision': c['revision'], 'user_id': env.users['auditor']['id'], 'role': 'reviewer'}).status_code == 200
    assert action(env, rid, 'return', 'reviewer').status_code == 200
    assert action(env, rid, 'refresh').status_code == 200
    assert action(env, rid, 'submit').status_code == 200
    attest(env, rid, eid, 'auditor')
    assert action(env, rid, 'approve', 'reviewer').status_code == 409
    assert action(env, rid, 'approve', 'auditor').status_code == 200


@pytest.mark.parametrize('operation', ['approve', 'issue'])
def test_disable_approver_blocks_transition(env, operation):
    cid = case(env)
    rid = draft(env, cid)
    assert action(env, rid, 'submit').status_code == 200
    if operation == 'issue':
        assert action(env, rid, 'approve', 'reviewer').status_code == 200
    with env.store.transaction() as db:
        db.execute('UPDATE users SET active=0 WHERE id=?', (env.users['reviewer']['id'],))
    response = action(env, rid, operation, 'reviewer' if operation == 'approve' else 'coordinator') if operation == 'issue' else env.clients['reviewer'].get(f'/api/reports/{rid}/workbench')
    assert response.status_code in (401, 403)


def test_linked_nonlab_evidence_also_requires_receipt(env):
    cid, rid, eid = prepared(env, 'document', True)
    assert action(env, rid, 'approve', 'reviewer').status_code == 409
    attest(env, rid, eid)
    assert action(env, rid, 'approve', 'reviewer').status_code == 200


def test_unreferenced_nonlab_evidence_is_not_automatically_relevant(env):
    cid, rid, eid = prepared(env, 'document')
    w = env.clients['reviewer'].get(f'/api/reports/{rid}/workbench').json()
    assert w['review']['required'] == []
    assert action(env, rid, 'approve', 'reviewer').status_code == 200
    assert env.clients['reviewer'].get(f'/api/reports/{rid}/review-evidence/{eid}').status_code == 404


def test_quarantined_original_cannot_be_attested(env):
    cid, rid, eid = prepared(env)
    reviewer = env.clients['reviewer']
    reviewer.get(f'/api/reports/{rid}/review-evidence/{eid}')
    p = receipt_payload(env, rid, eid)
    with env.store.transaction() as db:
        db.execute("UPDATE evidence SET scan_status='quarantined' WHERE id=?", (eid,))
    assert reviewer.post(f'/api/reports/{rid}/review-receipts', json=p).status_code == 423
    assert reviewer.get(f'/api/reports/{rid}/review-evidence/{eid}').status_code == 423


@pytest.mark.parametrize('suffix', ['workbench', 'history', 'history/1', 'comparison?from_version=1&to_version=2'])
def test_review_routes_are_case_scoped_even_for_admin(env, suffix):
    cid = case(env)
    rid = draft(env, cid)
    with env.store.transaction() as db:
        db.execute('UPDATE users SET admin=1 WHERE id=?', (env.users['outsider']['id'],))
    assert env.clients['outsider'].get(f'/api/reports/{rid}/{suffix}').status_code == 404
    with TestClient(env.app) as anonymous:
        assert anonymous.get(f'/api/reports/{rid}/{suffix}').status_code == 401


def test_cross_case_originals_and_checkpoints_are_denied(env):
    cid, rid, eid = prepared(env)
    other = case(env, 'OTHER')
    oid = original(env, other)
    assert env.clients['reviewer'].get(f'/api/reports/{rid}/review-evidence/{oid}').status_code == 404
    assert env.clients['outsider'].post(f'/api/cases/{cid}/audit-checkpoint').status_code == 404


def test_every_draft_revision_is_retained_and_diff_is_exact(env):
    cid = case(env)
    rid = draft(env, cid)
    client = env.clients['examiner']
    old = client.get('/api/reports/' + rid).json()
    sections = copy.deepcopy(old['sections'])
    sections[-1]['text'] = 'New synthetic wording <img src=x onerror=bad()>.'
    assert client.put('/api/reports/' + rid, json={'version': old['version'], 'sections': sections}).status_code == 200
    prior = client.get(f'/api/reports/{rid}/history/{old["version"]}').json()
    assert prior['report']['sections'] == old['sections']
    assert prior['capture_kind'] == 'live'
    result = client.get(f'/api/reports/{rid}/comparison?from_version={old["version"]}&to_version={old["version"]+1}').json()
    assert result['sections'] == [{'key': 'opinion', 'before': old['sections'][-1], 'after': sections[-1]}]
    assert result['sources']['change_count'] == 0
    assert client.get(f'/api/reports/{rid}/history/9999').status_code == 404
    assert client.get(f'/api/reports/{rid}/history?page=0').status_code == 422


def test_concurrent_report_writers_leave_no_partial_history(env):
    cid = case(env)
    rid = draft(env, cid)
    r = env.clients['examiner'].get('/api/reports/' + rid).json()
    def write(n):
        with TestClient(env.app) as client:
            client.cookies.update(env.clients['examiner'].cookies)
            client.headers.update(env.clients['examiner'].headers)
            sections = copy.deepcopy(r['sections'])
            sections[-1]['text'] = 'Synthetic writer ' + str(n)
            return client.put('/api/reports/' + rid, json={'version': r['version'], 'sections': sections}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == [200, 409]
    h = env.clients['examiner'].get(f'/api/reports/{rid}/history').json()
    assert h['total'] == r['version'] + 1


@pytest.mark.parametrize('table', ['report_history', 'review_receipts'])
@pytest.mark.parametrize('operation', ['UPDATE', 'DELETE'])
def test_review_provenance_cannot_be_rewritten_through_sql_triggers(env, table, operation):
    cid, rid, eid = prepared(env)
    attest(env, rid, eid)
    with pytest.raises(sqlite3.IntegrityError, match='append-only'):
        with env.store.transaction() as db:
            sql = f'DELETE FROM {table}' if operation == 'DELETE' else f"UPDATE {table} SET {'data' if table=='report_history' else 'statement'}='rewrite'"
            db.execute(sql)


def test_source_diff_exposes_exact_amendment_and_retirement(env):
    cid = case(env)
    rec = add_record(env, cid)
    rid = draft(env, cid)
    client = env.clients['examiner']
    c = client.get('/api/cases/' + cid).json()
    record = next(r for r in c['records'] if r['id'] == rec)
    data = record['data'] | {'length_mm': 8, 'text': 'Reasoned synthetic amendment'}
    assert client.put(f'/api/cases/{cid}/records/{rec}', json={
        'revision': c['revision'], 'data': data, 'reason': 'Synthetic correction', 'active': False}).status_code == 200
    w = client.get(f'/api/reports/{rid}/workbench').json()
    changed = w['source_changes']['records'][0]
    assert changed['change'] == 'retired'
    field = next(f for f in changed['fields'] if f['path'] == '/data/length_mm')
    assert field['before'] == 5 and field['after'] == 8
    # The link still describes the frozen snapshot, not the newly retired live record.
    assert w['links'][0]['status'] == 'resolved'
    assert action(env, rid, 'refresh').status_code == 200
    w = client.get(f'/api/reports/{rid}/workbench').json()
    assert w['links'][0]['status'] == 'retired'
    assert any(i['code'] == 'missing_reference' for i in w['checks']['issues'])


def test_field_diff_distinguishes_absent_null_and_escapes_json_pointers():
    assert R.field_changes({}, {'a/b~': None}) == [{
        'path': '/a~1b~0', 'before_present': False, 'after_present': True, 'before': None, 'after': None}]
    assert R.field_changes({'x': 1}, {})[0]['after_present'] is False
    assert R.field_changes({'x': 1}, {'x': 1}) == []


def test_checkpoint_verifies_prefix_and_detects_truncation_rewrite(env):
    cid = case(env)
    c = env.clients['auditor'].post(f'/api/cases/{cid}/audit-checkpoint')
    assert c.status_code == 200
    checkpoint = c.json()
    with env.store.transaction() as db:
        audit(db, cid, env.users['examiner']['id'], 'synthetic.later_event')
        events = V.audit_events(db, cid)
    result = R.verify_checkpoint(checkpoint, events, env.store.public_key())
    assert result['unanchored_later_events'] == 1
    with pytest.raises(ValueError, match='truncated'):
        R.verify_checkpoint(checkpoint, events[:checkpoint['payload']['count']-1], env.store.public_key())
    changed = copy.deepcopy(events)
    changed[0]['action'] = 'rewritten'
    previous = '0' * 64
    for event in changed:
        event['previous'] = previous
        event.pop('hash')
        event['hash'] = digest(event)
        previous = event['hash']
    with pytest.raises(ValueError, match='diverges'):
        R.verify_checkpoint(checkpoint, changed, env.store.public_key())


def test_checkpoint_rejects_wrong_key_signature_or_scope(env):
    cid = case(env)
    checkpoint = env.clients['auditor'].post(f'/api/cases/{cid}/audit-checkpoint').json()
    with env.store.read() as db:
        events = V.audit_events(db, cid)
    with pytest.raises(ValueError, match='key'):
        R.verify_checkpoint(checkpoint, events, 'bad')
    corrupted = copy.deepcopy(checkpoint)
    corrupted['payload']['head'] = 'a' * 64
    with pytest.raises(InvalidSignature):
        R.verify_checkpoint(corrupted, events, env.store.public_key())
    wrong_events = copy.deepcopy(events)
    for event in wrong_events:
        event['scope'] = 'wrong-case'
    with pytest.raises(ValueError):
        R.make_checkpoint(env.store, wrong_events, cid)


def legacy_store(env):
    """Create a real version-1 database using the checked-in original schema."""
    path = env.settings.data_dir.parent / 'legacy'
    path.mkdir()
    settings = replace(env.settings, data_dir=path)
    with env.store.read() as db:
        marker = db.execute("SELECT value FROM meta WHERE key='key_check'").fetchone()[0]
    db = sqlite3.connect(path / 'casework.sqlite3', isolation_level=None)
    db.executescript((__import__('pathlib').Path(M.__file__).with_name('schema.sql')).read_text())
    db.execute("INSERT INTO meta VALUES ('key_check',?)", (marker,))
    db.close()
    return settings


def test_migration_is_idempotent_and_key_checked_first(env):
    settings = legacy_store(env)
    old = settings.data_dir / 'casework.sqlite3'
    before = old.read_bytes()
    with pytest.raises(ValueError, match='Wrong master key'):
        Store(replace(settings, key=secrets.token_bytes(32)))
    assert old.read_bytes() == before
    first = Store(settings)
    with first.read() as db:
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '2'
        initial_rows = [tuple(r) for r in db.execute('SELECT * FROM schema_migrations')]
    second = Store(settings)
    with second.read() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM schema_migrations')] == initial_rows


def test_failed_migration_rolls_back_all_ddl(env, monkeypatch):
    settings = legacy_store(env)
    monkeypatch.setitem(M.MIGRATIONS, 2, 'CREATE TABLE should_rollback(id); SELECT missing_column FROM meta;')
    with pytest.raises(sqlite3.OperationalError):
        Store(settings)
    with sqlite3.connect(settings.data_dir / 'casework.sqlite3') as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('should_rollback','schema_migrations')").fetchall()
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '1'


def test_checksum_and_future_version_fail_closed(env):
    with env.store.transaction() as db:
        db.execute("UPDATE schema_migrations SET sha256='changed'")
    with pytest.raises(ValueError, match='checksum'):
        Store(env.settings)
    with env.store.transaction() as db:
        db.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    with pytest.raises(ValueError, match='Unsupported schema'):
        Store(env.settings)


def test_legacy_baseline_only_no_invented_prior_versions(env):
    cid = case(env)
    rid = issued(env, cid)
    settings = legacy_store(env)
    # Copy the original version-1 tables only. No revision history is invented.
    with env.store.read() as source, sqlite3.connect(settings.data_dir / 'casework.sqlite3') as dest:
        for table in ('users', 'cases', 'members', 'templates', 'reports'):
            rows = source.execute('SELECT * FROM ' + table).fetchall()
            dest.executemany(f'INSERT INTO {table} VALUES ({",".join("?" for _ in rows[0])})', [tuple(x) for x in rows])
    migrated = Store(settings)
    with migrated.read() as db:
        h = R.report_history(db, rid)
        assert h['total'] == 1
        assert h['items'][0]['capture_kind'] == 'legacy_baseline'
        report = V.report_row(db, rid)
        assert R.historical_report(db, rid, report['version'])['report']['pdf_sha256'] == report['pdf_sha256']
        assert migrated.unseal(report['pdf_ciphertext'], f'report:{cid}:{rid}').startswith(b'%PDF')


def test_checkpoint_cli_runs_and_fails_on_wrong_anchor(env, tmp_path, monkeypatch, capsys):
    cid = case(env)
    checkpoint = env.clients['auditor'].post(f'/api/cases/{cid}/audit-checkpoint').json()
    with env.store.read() as db:
        events = V.audit_events(db, cid)
    cp, ev, key = [tmp_path / x for x in ('checkpoint.json', 'audit.json', 'public-key.txt')]
    cp.write_text(canonical(checkpoint)); ev.write_text(canonical(events)); key.write_text(env.store.public_key())
    monkeypatch.setattr('sys.argv', ['review', str(cp), str(ev), '--trusted-key-file', str(key)])
    R.main()
    assert 'checkpoint_verified' in capsys.readouterr().out
    key.write_text('wrong-key')
    with pytest.raises(SystemExit) as exc:
        R.main()
    assert exc.value.code == 1


def test_new_quarantine_blocks_previously_receipted_approval(env):
    cid, rid, eid = prepared(env)
    attest(env, rid, eid)
    with env.store.transaction() as db:
        db.execute("UPDATE evidence SET scan_status='quarantined' WHERE id=?", (eid,))
    assert action(env, rid, 'approve', 'reviewer').status_code == 409


def test_review_original_inline_policy_and_digest_header(env):
    cid, rid, eid = prepared(env)
    reviewer = env.clients['reviewer']
    response = reviewer.get(f'/api/reports/{rid}/review-evidence/{eid}?inline=true')
    assert response.headers['content-disposition'].startswith('attachment')
    assert response.headers['x-evidence-sha256'] == digest(response.content)


def test_report_history_paginates_without_losing_versions(env):
    from openautopsyflow.schemas import ReportUpdate
    cid = case(env)
    rid = draft(env, cid)
    with env.store.transaction() as db:
        for n in range(55):
            report = V.report_row(db, rid)
            report['sections'][-1]['text'] = 'Synthetic iteration ' + str(n)
            V.edit_report(db, report, env.users['examiner'], ReportUpdate(version=report['version'], sections=report['sections']))
    first = env.clients['auditor'].get(f'/api/reports/{rid}/history').json()
    second = env.clients['auditor'].get(f'/api/reports/{rid}/history?page=2').json()
    assert first['total'] == 57
    assert len(first['items']) == 50 and len(second['items']) == 7
    assert set(r['version'] for r in first['items']).isdisjoint(r['version'] for r in second['items'])
