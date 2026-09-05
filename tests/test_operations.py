"""Regression tests for paging, intake provenance and operational controls."""
import base64
import json
import secrets
import sqlite3
import subprocess
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
import pytest
from openautopsyflow import service as V, schemas as S
from openautopsyflow.api import create_app
from openautopsyflow.cli import backup_database, restore_database, seed_demo, main
from openautopsyflow.store import Store, Settings
from conftest import case, add_record, draft


def test_intake_history_preserves_old_and_new_values(env):
    cid = case(env)
    client = env.clients['examiner']
    before = client.get('/api/cases/' + cid).json()
    payload = {**before['case'], 'subject_reference': 'Corrected SYNTHETIC identifier',
               'reason': 'Correct a transcription error', 'revision': before['revision']}
    assert client.put('/api/cases/' + cid, json=payload).status_code == 200
    after = client.get('/api/cases/' + cid).json()
    assert len(after['intake_history']) == 2
    assert after['intake_history'][0]['data'] == before['case']
    assert after['intake_history'][1]['data']['subject_reference'] == payload['subject_reference']
    assert after['intake_history'][1]['reason'] == payload['reason']
    with pytest.raises(sqlite3.IntegrityError):
        with env.store.transaction() as db:
            db.execute('DELETE FROM case_history WHERE case_id=?', (cid,))


def test_case_access_revocation_takes_effect_for_existing_session(env):
    cid = case(env)
    rid = draft(env, cid)
    client = env.clients['examiner']
    revision = client.get('/api/cases/' + cid).json()['revision']
    target = env.users['reviewer']['id']
    result = client.post(f'/api/cases/{cid}/members/{target}/revoke',
                         json={'revision': revision, 'reason': 'Synthetic assignment ended'})
    assert result.status_code == 200
    assert env.clients['reviewer'].get('/api/cases/' + cid).status_code == 404
    assert env.clients['reviewer'].get('/api/reports/' + rid).status_code == 404
    assert env.clients['reviewer'].get('/api/cases').json()['total'] == 0


@pytest.mark.parametrize('actor,target', [('reviewer','auditor'), ('examiner','examiner'), ('outsider','auditor')])
def test_revoke_rejects_nonadmin_self_and_nonmember(env, actor, target):
    cid = case(env)
    revision = env.clients['examiner'].get('/api/cases/' + cid).json()['revision']
    result = env.clients[actor].post(f'/api/cases/{cid}/members/{env.users[target]["id"]}/revoke',
                                   json={'revision': revision, 'reason': 'Attempted invalid operation'})
    assert result.status_code in (403, 404)


def test_revoke_requires_fresh_revision_and_reason(env):
    cid = case(env)
    client = env.clients['examiner']
    target = env.users['reviewer']['id']
    assert client.post(f'/api/cases/{cid}/members/{target}/revoke', json={'revision':1,'reason':'Ended assignment'}).status_code == 409
    revision = client.get('/api/cases/' + cid).json()['revision']
    assert client.post(f'/api/cases/{cid}/members/{target}/revoke', json={'revision':revision,'reason':''}).status_code == 422


def test_sql_pagination_search_and_global_metrics(env):
    ids = []
    with env.store.transaction() as db:
        for index in range(65):
            cid = V.create_case(db, env.users['examiner'], S.CaseData(
                case_no=f'PAGE-{index:03}', examination_date=date(2026,9,1),
                requesting_authority='Synthetic authority', examiner='Synthetic',
                subject_reference='Ångström SYNTHETIC' if index == 0 else 'SYNTHETIC'))
            ids.append(cid)
        V.add_record(db, ids[0], env.users['examiner'], S.RecordCreate(
            revision=1, kind='task', label='Outstanding',
            data=S.RecordData(due_date=date.today()-timedelta(days=1))))
    client = env.clients['examiner']
    first, second = [client.get(f'/api/cases?page={p}').json() for p in (1,2)]
    assert first['total'] == 65 and len(first['items']) == 50 and len(second['items']) == 15
    assert not ({r['id'] for r in first['items']} & {r['id'] for r in second['items']})
    assert first['metrics']['pending'] == 1 and first['metrics']['overdue'] == 1
    assert client.get('/api/cases?q=ångSTRÖM').json()['total'] == 1
    pending = client.get('/api/cases?pending_only=true').json()
    assert pending['total'] == 1 and pending['items'][0]['id'] == ids[0]
    assert client.get('/api/cases?status=examination').json()['total'] == 64
    assert client.get('/api/cases?status=unknown').status_code == 422
    assert env.clients['outsider'].get('/api/cases?pending_only=true').json()['total'] == 0


def test_restored_backup_revokes_all_old_sessions(env, tmp_path):
    backup = tmp_path/'saved.oafbackup'
    backup_database(env.store, backup)
    settings = replace(env.settings, data_dir=tmp_path/'restored')
    restore_database(settings, backup)
    store = Store(settings)
    with store.read() as db:
        assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 5


def test_restore_refuses_nonempty_directory_even_without_database(env, tmp_path):
    backup = tmp_path/'saved.oafbackup'
    backup_database(env.store, backup)
    directory = tmp_path/'not-empty'; directory.mkdir()
    (directory/'casework.sqlite3-wal').write_bytes(b'UNRELATED TEST FILE')
    with pytest.raises(ValueError):
        restore_database(replace(env.settings, data_dir=directory), backup)
    assert (directory/'casework.sqlite3-wal').read_bytes() == b'UNRELATED TEST FILE'


@pytest.mark.parametrize('returncode,expected', [(0,'clean'), (1,'quarantined'), (2,'quarantined')])
def test_scanner_result_contract(env, monkeypatch, returncode, expected):
    monkeypatch.setattr(V.shutil, 'which', lambda name: '/test/clamav')
    monkeypatch.setattr(V.subprocess, 'run', lambda *args, **kw: SimpleNamespace(returncode=returncode))
    assert V.scan_upload(b'SYNTHETIC', replace(env.settings,demo=False,secure_cookie=True,
           origins=('https://testserver',),scanner='clamscan'))[0] == expected


def test_scanner_timeout_quarantines(env, monkeypatch):
    monkeypatch.setattr(V.shutil, 'which', lambda name: '/test/clamav')
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('clamscan', 60)
    monkeypatch.setattr(V.subprocess, 'run', timeout)
    settings = replace(env.settings,demo=False,secure_cookie=True,origins=('https://testserver',),scanner='clamscan')
    assert V.scan_upload(b'SYNTHETIC',settings) == ('quarantined','scanner-error')


def test_demo_seed_is_randomized_synthetic_and_never_overwrites(tmp_path):
    settings = Settings(tmp_path/'fresh',secrets.token_bytes(32),True,False,('testserver',),('http://testserver',))
    store = create_app(settings).state.store
    passwords = seed_demo(store)
    assert len(set(passwords.values())) == 4
    assert all(len(value) >= 20 for value in passwords.values())
    with store.read() as db:
        assert db.execute('SELECT COUNT(*) FROM cases').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM case_history').fetchone()[0] == 3
    with pytest.raises(ValueError, match='empty database'):
        seed_demo(store)
    with pytest.raises(ValueError, match='OAF_DEMO'):
        seed_demo(Store(replace(settings,demo=False,secure_cookie=True,origins=('https://testserver',))))


def test_settings_env_fail_closed_and_demo_key_reused(tmp_path,monkeypatch):
    monkeypatch.delenv('OAF_MASTER_KEY',raising=False)
    monkeypatch.setenv('OAF_DATA_DIR',str(tmp_path/'runtime'))
    monkeypatch.setenv('OAF_DEMO','0')
    with pytest.raises(ValueError,match='Set OAF_MASTER_KEY'):
        Settings.from_env()
    monkeypatch.setenv('OAF_DEMO','1')
    one,two = Settings.from_env(),Settings.from_env()
    assert one.key == two.key and len(one.key) == 32
    monkeypatch.setenv('OAF_MASTER_KEY',base64.b64encode(secrets.token_bytes(32)).decode())
    assert Settings.from_env().key != one.key


def test_cli_keygen_does_not_require_deployment_configuration(monkeypatch,capsys):
    monkeypatch.setattr('sys.argv',['openautopsyflow','keygen'])
    main()
    assert len(base64.b64decode(capsys.readouterr().out.strip(),validate=True)) == 32
