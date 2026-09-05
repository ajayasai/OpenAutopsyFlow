import base64
import hashlib
import secrets
import sqlite3
import time
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from openautopsyflow.store import Settings,Store
from openautopsyflow.security import totp_code,verify_totp,password_ok
from conftest import case,add_record,draft,TEST_PASSWORD


def test_no_default_accounts_or_public_case_data(env):
    client=TestClient(env.app)
    assert client.get('/api/config').status_code==200
    assert client.get('/api/cases').status_code==401
    assert client.get('/api/users').status_code==401
    assert client.get('/api/schema').status_code==401
    assert client.get('/docs').status_code==404
    assert client.get('/openapi.json').status_code==404


def test_login_logout_and_revocation(env):
    client=TestClient(env.app)
    r=client.post('/api/login',json={'username':'examiner','password':TEST_PASSWORD})
    assert r.status_code==200
    assert 'httponly' in r.headers['set-cookie'].lower()
    assert 'samesite=strict' in r.headers['set-cookie'].lower()
    assert 'oaf_session' not in r.json()
    csrf=r.json()['csrf']
    assert client.post('/api/logout').status_code==403
    old=client.cookies.get('oaf_session')
    assert client.post('/api/logout',headers={'X-CSRF-Token':csrf}).status_code==200
    client.cookies.set('oaf_session',old)
    assert client.get('/api/me').status_code==401


def test_login_rate_limit_persists_failed_attempts(env):
    client=TestClient(env.app)
    for _ in range(8):
        assert client.post('/api/login',json={'username':'examiner','password':'wrong'}).status_code==401
    assert client.post('/api/login',json={'username':'examiner','password':TEST_PASSWORD}).status_code==429


def test_unknown_account_and_incorrect_password_same_error(env):
    client=TestClient(env.app)
    a=client.post('/api/login',json={'username':'missing','password':'wrong'})
    b=client.post('/api/login',json={'username':'examiner','password':'wrong'})
    assert a.status_code==b.status_code==401
    assert a.json()==b.json()


def test_origin_csrf_and_host_restrictions(env):
    client=env.clients['examiner']
    assert client.post('/api/logout',headers={'Origin':'https://untrusted.example'}).status_code==403
    assert client.post('/api/logout',headers={'X-CSRF-Token':'incorrect'}).status_code==403
    assert client.get('/healthz',headers={'Host':'untrusted.example'}).status_code==400


def test_security_headers_and_csp(env):
    response=env.clients['examiner'].get('/')
    assert response.headers['cache-control']=='no-store'
    assert response.headers['x-frame-options']=='DENY'
    assert response.headers['x-content-type-options']=='nosniff'
    assert "script-src 'self'" in response.headers['content-security-policy']
    assert 'unsafe-inline' not in response.headers['content-security-policy']


@pytest.mark.parametrize('endpoint',['','/records/not-real/history','/evidence/not-real/content','/audit'])
def test_case_acl_blocks_cross_case_reads(env,endpoint):
    ident=case(env)
    assert env.clients['outsider'].get('/api/cases/'+ident+endpoint).status_code==404


def test_cross_case_writes_are_denied(env):
    ident=case(env)
    c=env.clients['examiner'].get('/api/cases/'+ident).json()
    response=env.clients['outsider'].post('/api/cases/'+ident+'/records',json={'revision':c['revision'],'kind':'task','label':'Injected','data':{'text':'Not permitted'}})
    assert response.status_code==404


def test_admin_does_not_bypass_case_membership(env):
    client=env.clients['outsider']
    r=client.post('/api/cases',json={'case_no':'OUTSIDE','examination_date':'2026-09-01','requesting_authority':'Synthetic','examiner':'Other'})
    ident=r.json()['id']
    assert env.clients['examiner'].get('/api/cases/'+ident).status_code==404


@pytest.mark.parametrize('role',['auditor','reviewer','coordinator'])
def test_only_examiner_can_record_examination_findings(env,role):
    ident=case(env)
    c=env.clients[role].get('/api/cases/'+ident).json()
    response=env.clients[role].post(f'/api/cases/{ident}/records',json={'revision':c['revision'],'kind':'injury','label':'1','data':{'number':1,'text':'Forbidden'}})
    assert response.status_code==403


def test_coordinator_can_manage_tasks(env):
    ident=case(env)
    assert add_record(env,ident,'task','Follow up',{'text':'Synthetic task'},role='coordinator')


def test_readonly_cannot_export_or_change_members(env):
    ident=case(env)
    c=env.clients['auditor'].get('/api/cases/'+ident).json()
    assert env.clients['auditor'].post(f'/api/cases/{ident}/export').status_code==403
    response=env.clients['auditor'].post(f'/api/cases/{ident}/members',json={'revision':c['revision'],'user_id':env.users['outsider']['id'],'role':'examiner'})
    assert response.status_code==403


def test_expired_session_rejected(env):
    with env.store.transaction() as db:db.execute('UPDATE sessions SET expires=?',(time.time()-1,))
    assert env.clients['examiner'].get('/api/me').status_code==401


def test_idle_session_rejected(env):
    with env.store.transaction() as db:db.execute('UPDATE sessions SET last_seen=?',(time.time()-1900,))
    assert env.clients['examiner'].get('/api/me').status_code==401


def test_disabled_user_loses_all_sessions(env):
    response=env.clients['examiner'].post('/api/users/'+env.users['reviewer']['id']+'/disable')
    assert response.status_code==200
    assert env.clients['reviewer'].get('/api/me').status_code==401
    assert env.clients['examiner'].post('/api/users/'+env.users['examiner']['id']+'/disable').status_code==403


def test_password_change_preserves_whitespace_and_revokes_sessions(env):
    new='  whitespace-is-part-of-this-password  '
    response=env.clients['examiner'].post('/api/password',json={'old_password':TEST_PASSWORD,'new_password':new})
    assert response.status_code==200
    assert env.clients['examiner'].get('/api/me').status_code==401
    c=TestClient(env.app)
    assert c.post('/api/login',json={'username':'examiner','password':new.strip()}).status_code==401
    assert c.post('/api/login',json={'username':'examiner','password':new}).status_code==200


def test_password_hash_parameters(env):
    with env.store.read() as db:
        value=db.execute("SELECT password_hash FROM users WHERE username='examiner'").fetchone()[0]
    assert '$argon2id$' in value and 'm=65536,t=3,p=2' in value
    assert TEST_PASSWORD not in value
    assert password_ok(value,TEST_PASSWORD)


def test_totp_known_vector_and_replay():
    secret=b'12345678901234567890'
    assert totp_code(secret,1)=='287082'  # RFC 6238 SHA-1 vector truncated to six digits.
    assert verify_totp(secret,'287082',-1,timestamp=59)==1
    assert verify_totp(secret,'287082',1,timestamp=59) is None
    assert verify_totp(secret,'invalid',-1,timestamp=59) is None


def test_totp_required_and_replay_rejected_at_login(env):
    secret=secrets.token_bytes(20);ident=env.users['reviewer']['id']
    with env.store.transaction() as db:
        db.execute('UPDATE users SET totp=? WHERE id=?',(env.store.seal(secret,f'totp:{ident}'),ident))
    client=TestClient(env.app)
    assert client.post('/api/login',json={'username':'reviewer','password':TEST_PASSWORD}).status_code==401
    code=totp_code(secret,int(time.time()//30))
    payload={'username':'reviewer','password':TEST_PASSWORD,'otp':code}
    assert client.post('/api/login',json=payload).status_code==200
    assert client.post('/api/login',json=payload).status_code==401


@pytest.mark.parametrize('kwargs',[{'key':b'bad'}, {'demo':False,'secure_cookie':False},
                                  {'hosts':('*',)}, {'origins':('*',)},
                                  {'demo':False,'secure_cookie':True,'origins':('http://untrusted',)}])
def test_unsafe_configuration_fails_closed(env,kwargs):
    with pytest.raises(ValueError):replace(env.settings,**kwargs)


def test_wrong_master_key_is_not_silently_accepted(env):
    with pytest.raises(ValueError,match='Wrong master key'):Store(replace(env.settings,key=secrets.token_bytes(32)))


def test_request_body_cap_including_untrusted_content_length(env):
    client=env.clients['examiner']
    raw=b'x'*(env.settings.max_upload+65537)
    assert client.post('/api/users',content=raw,headers={'content-type':'application/json'}).status_code==413


def test_search_cannot_escape_parameterization(env):
    case(env)
    response=env.clients['examiner'].get('/api/cases',params={'q':"' OR 1=1 --"})
    assert response.status_code==200 and response.json()['items']==[]
