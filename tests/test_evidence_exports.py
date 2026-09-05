import base64
import io
import json
import secrets
import sqlite3
import zipfile
from dataclasses import replace
from datetime import date,timedelta
from pathlib import Path
import pytest
from PIL import Image
from fastapi import HTTPException
from fastapi.testclient import TestClient
from cryptography.exceptions import InvalidTag,InvalidSignature
from openautopsyflow import service as V
from openautopsyflow.api import create_app
from openautopsyflow.cli import backup_database,restore_database
from openautopsyflow.documents import verify_bundle,signed_bundle,render_pdf
from openautopsyflow.store import Store,digest
from conftest import case,add_record,draft,issued,action


def upload(env,case_id,raw=b'SYNTHETIC EVIDENCE CANARY 985158',filename='synthetic.txt',kind='document',finding_id='',client=None):
    client=client or env.clients['examiner']
    c=client.get('/api/cases/'+case_id).json()
    return client.post(f'/api/cases/{case_id}/evidence',data={'revision':c['revision'],'kind':kind,'finding_id':finding_id},files={'file':(filename,raw)})


def test_original_evidence_encrypted_and_bytes_preserved(env):
    cid=case(env);rid=add_record(env,cid);raw=b'SYNTHETIC ENCRYPTION CANARY 28371904213'
    response=upload(env,cid,raw,finding_id=rid)
    assert response.status_code==201,response.text;eid=response.json()['id']
    result=env.clients['examiner'].get(f'/api/cases/{cid}/evidence/{eid}/content')
    assert result.status_code==200 and result.content==raw
    with env.store.read() as db:
        e=db.execute('SELECT * FROM evidence WHERE id=?',(eid,)).fetchone()
        assert e['sha256']==digest(raw)
        assert raw not in e['ciphertext']
        assert 'SYNTHETIC-DEMO-BYPASS' in e['scan_engine']
    assert raw not in env.store.path.read_bytes()


def test_evidence_original_cannot_be_replaced_or_deleted(env):
    cid=case(env);eid=upload(env,cid).json()['id']
    for sql in ('DELETE FROM evidence WHERE id=?',"UPDATE evidence SET filename='changed.txt' WHERE id=?"):
        with pytest.raises(sqlite3.IntegrityError):
            with env.store.transaction() as db:db.execute(sql,(eid,))


def test_evidence_integrity_tampering_is_detected(env):
    cid=case(env);eid=upload(env,cid).json()['id']
    with env.store.transaction() as db:
        db.execute('DROP TRIGGER evidence_no_replace')
        db.execute('UPDATE evidence SET ciphertext=? WHERE id=?',(secrets.token_bytes(80),eid))
    assert env.clients['examiner'].get(f'/api/cases/{cid}/evidence/{eid}/content').status_code==409


def test_cross_case_evidence_links_and_downloads_rejected(env):
    a=case(env,'CASE-A');b=case(env,'CASE-B');rid=add_record(env,a)
    assert upload(env,b,finding_id=rid).status_code==422
    eid=upload(env,a).json()['id']
    assert env.clients['examiner'].get(f'/api/cases/{b}/evidence/{eid}/content').status_code==404
    assert env.clients['outsider'].get(f'/api/cases/{a}/evidence/{eid}/content').status_code==404


@pytest.mark.parametrize('filename,raw,kind',[
    ('script.svg',b'<svg onload="alert(1)"></svg>','photo'),
    ('script.html',b'<script>evil</script>','document'),
    ('fake.jpg',b'not a picture','photo'),
    ('fake.pdf',b'not a PDF','document'),
    ('binary.txt',b'\xff\xfe','document'),
    ('binary.txt',b'a\x00b','document'),
    ('report.pdf',b'%PDF-1.4 dummy','photo'),
    ('empty.txt',b'','document'),
])
def test_restricted_file_types(env,filename,raw,kind):
    cid=case(env)
    assert upload(env,cid,raw,filename,kind).status_code in (413,422)


def test_valid_image_and_mismatched_extension(env):
    cid=case(env);buffer=io.BytesIO();Image.new('RGB',(8,8)).save(buffer,format='PNG');raw=buffer.getvalue()
    result=upload(env,cid,raw,'training.png','photo');assert result.status_code==201
    eid=result.json()['id'];response=env.clients['examiner'].get(f'/api/cases/{cid}/evidence/{eid}/content?inline=true')
    assert response.content==raw and response.headers['content-type']=='image/png'
    assert 'inline;' in response.headers['content-disposition']
    assert upload(env,cid,raw,'wrong.jpg','photo').status_code==422


def test_filename_sanitization(env):
    cid=case(env);result=upload(env,cid,filename='../../sensitive/report.txt')
    assert result.status_code==201
    e=env.clients['examiner'].get('/api/cases/'+cid).json()['evidence'][0]
    assert e['filename']=='report.txt'


def test_document_cannot_be_served_inline(env):
    cid=case(env);eid=upload(env,cid,raw=b'%PDF-1.4\nSynthetic invalid-but-magic-checked fixture',filename='report.pdf').json()['id']
    response=env.clients['examiner'].get(f'/api/cases/{cid}/evidence/{eid}/content?inline=true')
    assert 'attachment;' in response.headers['content-disposition']


def test_real_mode_without_scanner_quarantines_and_denies_download_review(env):
    cid=case(env)
    settings=replace(env.settings,demo=False,secure_cookie=True,origins=('https://testserver',))
    app=create_app(settings);client=TestClient(app,base_url='https://testserver')
    client.cookies.update(env.clients['examiner'].cookies)
    client.headers.update({'X-CSRF-Token':env.clients['examiner'].headers['X-CSRF-Token'],'Origin':'https://testserver'})
    result=upload(env,cid,client=client)
    assert result.status_code==201 and result.json()['scan_status']=='quarantined'
    eid=result.json()['id'];c=client.get('/api/cases/'+cid).json()
    assert client.get(f'/api/cases/{cid}/evidence/{eid}/content').status_code==423
    assert client.post(f'/api/cases/{cid}/evidence/{eid}/review',json={'revision':c['revision']}).status_code==423


def test_new_toxicology_result_prompts_human_review(env):
    cid=case(env);parent=issued(env,cid)
    eid=upload(env,cid,raw=b'Synthetic new toxicology result: no real laboratory finding.',filename='toxicology.txt',kind='lab_result').json()['id']
    client=env.clients['examiner'];c=client.get('/api/cases/'+cid).json()
    template=client.get('/api/templates').json()[0]['id']
    rid=client.post(f'/api/cases/{cid}/reports',json={'revision':c['revision'],'template_id':template,'kind':'supplementary','parent_id':parent}).json()['id']
    r=client.get('/api/reports/'+rid).json()
    assert any(i['code']=='unreviewed_lab_result' and i['entity']==eid for i in r['checks']['issues'])
    response=client.post(f'/api/cases/{cid}/evidence/{eid}/review',json={'revision':c['revision']})
    assert response.status_code==200
    assert action(env,rid,'refresh').status_code==200
    assert not any(i['code']=='unreviewed_lab_result' for i in client.get('/api/reports/'+rid).json()['checks']['issues'])


def test_lab_completion_requires_linked_clean_reviewed_result(env):
    cid=case(env);specimen=add_record(env,cid,'specimen','SPEC-1',{'custodian':'Synthetic desk'})
    lab=add_record(env,cid,'lab','Toxicology',{'specimen_id':specimen,'status':'pending'})
    client=env.clients['examiner'];c=client.get('/api/cases/'+cid).json()
    data=next(r['data'] for r in c['records'] if r['id']==lab);data['status']='complete'
    def update(c,d):return client.put(f'/api/cases/{cid}/records/{lab}',json={'revision':c['revision'],'data':d,'reason':'Synthetic test completion'})
    assert update(c,data).status_code==422
    eid=upload(env,cid,filename='tox.txt',kind='lab_result').json()['id'];c=client.get('/api/cases/'+cid).json();data['evidence_id']=eid
    assert update(c,data).status_code==422
    assert client.post(f'/api/cases/{cid}/evidence/{eid}/review',json={'revision':c['revision']}).status_code==200
    c=client.get('/api/cases/'+cid).json()
    assert update(c,data).status_code==200


def test_export_signed_and_independently_verifiable(env):
    cid=case(env);eid=upload(env,cid).json()['id'];issued(env,cid)
    response=env.clients['examiner'].post(f'/api/cases/{cid}/export?include_evidence=true')
    assert response.status_code==200
    verified=verify_bundle(response.content,env.store.public_key())
    assert verified['integrity_verified'] and verified['identity_anchored']
    assert verified['files_verified']>=5
    assert verify_bundle(response.content)['identity_anchored'] is False
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert manifest['metadata']['contains_sensitive_case_data']
        assert 'audit.json' in manifest['files']
        assert any(eid in name for name in manifest['files'])


def test_bundle_untrusted_key_rejected(env):
    data=signed_bundle(env.store,{'test.txt':b'synthetic'}, {})
    with pytest.raises(ValueError,match='trusted key'):verify_bundle(data,base64.b64encode(secrets.token_bytes(32)).decode())


@pytest.mark.parametrize('mutation',['content','signature','extra','missing','duplicate','path'])
def test_bundle_tampering_and_archive_attacks_detected(env,mutation):
    data=signed_bundle(env.store,{'test.txt':b'synthetic'}, {})
    with zipfile.ZipFile(io.BytesIO(data)) as z:files={name:z.read(name) for name in z.namelist()}
    if mutation=='content':files['test.txt']=b'altered'
    elif mutation=='signature':files['manifest.ed25519']=base64.b64encode(secrets.token_bytes(64))
    elif mutation=='extra':files['extra.txt']=b'synthetic'
    elif mutation=='missing':del files['test.txt']
    elif mutation=='path':files['../unexpected']=b'synthetic'
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        for name,raw in files.items():z.writestr(name,raw)
        if mutation=='duplicate':
            with pytest.warns(UserWarning):z.writestr('test.txt',b'duplicate')
    with pytest.raises((ValueError,InvalidSignature)):verify_bundle(output.getvalue(),env.store.public_key())


def test_backup_roundtrip_and_wrong_key_rejected(env,tmp_path):
    cid=case(env);eid=upload(env,cid).json()['id'];issued(env,cid)
    target=tmp_path/'casework.oafbackup';result=backup_database(env.store,target)
    assert target.read_bytes().startswith(b'OAFB1') and result['sha256']==digest(target.read_bytes())
    restored_settings=replace(env.settings,data_dir=tmp_path/'restored')
    restore_database(restored_settings,target)
    restored=Store(restored_settings)
    with restored.read() as db:
        assert db.execute('SELECT COUNT(*) FROM cases').fetchone()[0]==1
        evidence=dict(db.execute('SELECT * FROM evidence WHERE id=?',(eid,)).fetchone())
        assert V.evidence_bytes(restored,evidence)==b'SYNTHETIC EVIDENCE CANARY 985158'
    with pytest.raises(ValueError,match='already contains'):restore_database(restored_settings,target)
    with pytest.raises(InvalidTag):restore_database(replace(env.settings,data_dir=tmp_path/'wrong',key=secrets.token_bytes(32)),target)
    with pytest.raises(ValueError,match='already exists'):backup_database(env.store,target)


def test_encrypted_backup_tampering_rejected(env,tmp_path):
    target=tmp_path/'backup.oafbackup';backup_database(env.store,target)
    raw=bytearray(target.read_bytes());raw[-1]^=1;target.write_bytes(raw)
    with pytest.raises(InvalidTag):restore_database(replace(env.settings,data_dir=tmp_path/'restore'),target)


def test_pdf_escapes_markup_and_rejects_unsupported_glyphs(env):
    cid=case(env);rid=draft(env,cid);r=env.clients['examiner'].get('/api/reports/'+rid).json()
    r['sections'][-1]['text']='Synthetic <script>alert(1)</script> & escaped markup'
    raw=render_pdf(r,env.settings)
    assert raw.startswith(b'%PDF-')
    r['sections'][-1]['text']='தமிழ்'
    with pytest.raises(ValueError,match='OAF_PDF_FONT'):render_pdf(r,env.settings)


def test_pdf_large_narrative_paginates(env):
    cid=case(env);rid=draft(env,cid);r=env.clients['examiner'].get('/api/reports/'+rid).json()
    r['sections'][-1]['text']='Synthetic long narrative, no clinical content.\n'*600
    raw=render_pdf(r,env.settings)
    assert raw.startswith(b'%PDF-') and len(raw)>3000


def test_export_preserves_finding_history_discussion_and_template_without_secrets(env):
    cid=case(env);rid=add_record(env,cid)
    client=env.clients['examiner']
    before=client.get('/api/cases/'+cid).json()
    data=dict(before['records'][0]['data']);data['text']='Revised synthetic observation for export test'
    assert client.put(f'/api/cases/{cid}/records/{rid}',json={'revision':before['revision'],'data':data,'reason':'Synthetic correction'}).status_code==200
    report_id=draft(env,cid)
    assert env.clients['reviewer'].post(f'/api/reports/{report_id}/comments',json={'body':'Synthetic review discussion','blocking':False}).status_code==201
    response=client.post(f'/api/cases/{cid}/export')
    assert response.status_code==200
    assert verify_bundle(response.content,env.store.public_key())['identity_anchored']
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        history=json.loads(z.read('record-history.json'))
        assert len(history)==2 and history[0]['data']['text']=='Synthetic finding'
        assert history[1]['data']['text']==data['text']
        assert json.loads(z.read('review-discussion.json'))[0]['body']=='Synthetic review discussion'
        assert len(json.loads(z.read('templates.json')))==1
        assert len(json.loads(z.read('assignments.json')))==4
        assert b'password_hash' not in z.read('assignments.json') and b'csrf' not in z.read('assignments.json')
