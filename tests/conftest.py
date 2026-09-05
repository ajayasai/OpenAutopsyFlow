import hashlib
import secrets
import shutil
import sqlite3
import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from openautopsyflow.api import create_app
from openautopsyflow.security import create_user
from openautopsyflow.store import Settings

TEST_PASSWORD = 'Only-a-synthetic-test-password-927!'


@pytest.fixture(scope='session')
def baseline(tmp_path_factory):
    directory=tmp_path_factory.mktemp('baseline')
    key=secrets.token_bytes(32)
    app=create_app(Settings(directory,key,True,False,('testserver',),('http://testserver',)))
    store=app.state.store
    users={}
    with store.transaction() as db:
        for name in ('examiner','reviewer','coordinator','auditor','outsider'):
            ident=create_user(db,name,'Synthetic '+name,TEST_PASSWORD,admin=name=='examiner')
            users[name]={'id':ident,'name':'Synthetic '+name,'admin':name=='examiner','username':name}
    destination=directory/'baseline.sqlite3'
    with store.read() as source:
        backup=sqlite3.connect(destination)
        source.backup(backup)
        backup.close()
    return key,destination,users


@pytest.fixture
def env(tmp_path,baseline):
    key,source,users=baseline
    directory=tmp_path/'data';directory.mkdir()
    shutil.copyfile(source,directory/'casework.sqlite3')
    settings=Settings(directory,key,True,False,('testserver',),('http://testserver',))
    app=create_app(settings)
    store=app.state.store
    clients={}
    with store.transaction() as db:
        for name,user in users.items():
            token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(),user['id'],csrf,time.time()+3600,time.time()))
            client=TestClient(app)
            client.cookies.set('oaf_session',token)
            client.headers.update({'X-CSRF-Token':csrf,'Origin':'http://testserver'})
            clients[name]=client
    result=SimpleNamespace(app=app,store=store,clients=clients,users=users,settings=settings)
    yield result
    for client in clients.values():client.close()


def case(env,number='TEST-001',members=True):
    client=env.clients['examiner']
    result=client.post('/api/cases',json={'case_no':number,'examination_date':'2026-09-01',
        'requesting_authority':'Synthetic teaching authority','examiner':'Synthetic Examiner',
        'subject_reference':'SYNTHETIC TEST ONLY'})
    assert result.status_code==201,result.text
    ident=result.json()['id']
    if members:
        for role in ('reviewer','coordinator','auditor'):
            c=client.get('/api/cases/'+ident).json()
            response=client.post(f'/api/cases/{ident}/members',json={'revision':c['revision'],'user_id':env.users[role]['id'],'role':role})
            assert response.status_code==200,response.text
    return ident


def add_record(env,case_id,kind='injury',label='1',data=None,role='examiner'):
    client=env.clients[role]
    current=client.get('/api/cases/'+case_id).json()
    response=client.post(f'/api/cases/{case_id}/records',json={'revision':current['revision'],'kind':kind,'label':label,
            'data':data if data is not None else {'number':1,'text':'Synthetic finding','length_mm':5}})
    assert response.status_code==201,response.text
    return response.json()['id']


def draft(env,case_id,opinion=True):
    client=env.clients['examiner']
    c=client.get('/api/cases/'+case_id).json()
    template=client.get('/api/templates').json()[0]['id']
    response=client.post(f'/api/cases/{case_id}/reports',json={'revision':c['revision'],'template_id':template})
    assert response.status_code==201,response.text
    ident=response.json()['id']
    if opinion:
        r=client.get('/api/reports/'+ident).json()
        for section in r['sections']:
            if section['key']=='opinion':section['text']='Synthetic testing statement only. This is not a medical opinion.'
        result=client.put('/api/reports/'+ident,json={'version':r['version'],'sections':r['sections']})
        assert result.status_code==200,result.text
    return ident


def action(env,report_id,operation,role='examiner',acks=True):
    client=env.clients[role]
    r=client.get('/api/reports/'+report_id).json()
    payload={'version':r['version']}
    if acks:
        payload['acknowledgements']={i['id']:'Synthetic testing rationale: reviewed and explicitly documented as unresolved.' for i in r['checks']['issues'] if i['severity']=='warning'}
    return client.post(f'/api/reports/{report_id}/actions/{operation}',json=payload)


def issued(env,case_id):
    ident=draft(env,case_id)
    assert action(env,ident,'submit').status_code==200
    assert action(env,ident,'approve','reviewer').status_code==200
    response=action(env,ident,'issue')
    assert response.status_code==200,response.text
    return ident
