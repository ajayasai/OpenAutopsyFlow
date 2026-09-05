import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import pytest
from fastapi import HTTPException
from openautopsyflow import service as V
from openautopsyflow.store import digest,canonical,audit_events,verify_audit
from conftest import case,add_record,draft,issued,action


def report(env,ident):return env.clients['examiner'].get('/api/reports/'+ident).json()
def current(env,ident):return env.clients['examiner'].get('/api/cases/'+ident).json()


def test_case_duplicate_and_case_search(env):
    case(env)
    response=env.clients['examiner'].post('/api/cases',json={'case_no':'TEST-001','examination_date':'2026-09-01','requesting_authority':'Synthetic','examiner':'Synthetic'})
    assert response.status_code==409
    assert env.clients['examiner'].get('/api/cases?q=TEST').json()['total']==1
    assert env.clients['outsider'].get('/api/cases').json()['total']==0


def test_stale_case_write_returns_conflict_and_keeps_data(env):
    ident=case(env);before=current(env,ident)
    add_record(env,ident)
    response=env.clients['examiner'].put('/api/cases/'+ident,json={**before['case'],'case_no':'WRONG','revision':before['revision'],'reason':'Stale update'})
    assert response.status_code==409
    assert current(env,ident)['case']['case_no']=='TEST-001'


def test_parallel_revision_guard_allows_exactly_one_writer(env):
    ident=case(env);revision=current(env,ident)['revision']
    def mutate():
        try:
            with env.store.transaction() as db:
                V.check_revision(V.case_row(db,ident),revision)
                V.touch(db,ident)
            return 200
        except HTTPException as e:return e.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:mutate(),range(2)))
    assert sorted(results)==[200,409]


def test_measurement_units_range_and_extra_fields_validated(env):
    ident=case(env);c=current(env,ident)
    response=env.clients['examiner'].post(f'/api/cases/{ident}/records',json={'revision':c['revision'],'kind':'organ','label':'Synthetic organ','data':{'weight_g':-1}})
    assert response.status_code==422
    response=env.clients['examiner'].post(f'/api/cases/{ident}/records',json={'revision':c['revision'],'kind':'organ','label':'Synthetic organ','data':{'weight_kg':1}})
    assert response.status_code==422


def test_injury_number_is_required_unique_and_stable(env):
    ident=case(env);rid=add_record(env,ident);c=current(env,ident)
    response=env.clients['examiner'].post(f'/api/cases/{ident}/records',json={'revision':c['revision'],'kind':'injury','label':'Other','data':{'number':1}})
    assert response.status_code==409
    response=env.clients['examiner'].post(f'/api/cases/{ident}/records',json={'revision':c['revision'],'kind':'injury','label':'Other','data':{}})
    assert response.status_code==422
    data=c['records'][0]['data'];data['number']=4
    response=env.clients['examiner'].put(f'/api/cases/{ident}/records/{rid}',json={'revision':c['revision'],'data':data,'reason':'Try renumbering'})
    assert response.status_code==422


def test_amendments_preserve_previous_findings(env):
    ident=case(env);rid=add_record(env,ident);c=current(env,ident);data=c['records'][0]['data']
    old=data['text'];data['text']='Corrected synthetic description'
    response=env.clients['examiner'].put(f'/api/cases/{ident}/records/{rid}',json={'revision':c['revision'],'data':data,'reason':'Correct a transcription error'})
    assert response.status_code==200
    history=env.clients['examiner'].get(f'/api/cases/{ident}/records/{rid}/history').json()
    assert len(history)==2 and history[0]['data']['text']==old
    assert history[1]['data']['text']=='Corrected synthetic description'
    with pytest.raises(sqlite3.IntegrityError):
        with env.store.transaction() as db:db.execute('DELETE FROM record_history WHERE record_id=?',(rid,))


def test_missing_injury_reference_detected_in_plain_text(env):
    ident=case(env);rid=draft(env,ident);r=report(env,rid)
    r['sections'][-1]['text']='Injury 4 is intentionally not present in this synthetic record.'
    assert env.clients['examiner'].put('/api/reports/'+rid,json={'version':r['version'],'sections':r['sections']}).status_code==200
    result=report(env,rid)
    assert any(i['code']=='missing_reference' and i['entity']=='injury:4' for i in result['checks']['issues'])
    assert action(env,rid,'submit').status_code==409


@pytest.mark.parametrize('kind',['record','evidence'])
def test_explicit_missing_references_are_structural_blockers(env,kind):
    ident=case(env);rid=draft(env,ident);r=report(env,rid)
    r['sections'][-1]['text']=f'Synthetic reference [[{kind}:missing-id]]'
    env.clients['examiner'].put('/api/reports/'+rid,json={'version':r['version'],'sections':r['sections']})
    assert action(env,rid,'submit').status_code==409


def test_blank_opinion_not_automatically_filled(env):
    ident=case(env);rid=draft(env,ident,opinion=False)
    r=report(env,rid)
    assert r['sections'][-1]['text']==''
    assert any(i['code']=='required_section' for i in r['checks']['issues'])
    assert action(env,rid,'submit').status_code==409


def test_warning_requires_specific_human_acknowledgement(env):
    ident=case(env);add_record(env,ident);rid=draft(env,ident)
    assert action(env,rid,'submit',acks=False).status_code==409
    assert action(env,rid,'submit').status_code==200
    assert report(env,rid)['acknowledgements']


def test_only_one_unissued_report_per_case(env):
    ident=case(env);draft(env,ident)
    c=current(env,ident);template=env.clients['examiner'].get('/api/templates').json()[0]['id']
    response=env.clients['examiner'].post(f'/api/cases/{ident}/reports',json={'revision':c['revision'],'template_id':template})
    assert response.status_code==409


def test_stale_draft_refresh_preserves_typed_narrative(env):
    ident=case(env);rid=draft(env,ident);before=report(env,rid)
    add_record(env,ident,'task','New request',{'text':'New synthetic material'})
    assert action(env,rid,'submit').status_code==409
    assert report(env,rid)['changes_since_snapshot']['records']
    assert action(env,rid,'refresh').status_code==200
    refreshed=report(env,rid)
    assert refreshed['sections']==before['sections']
    assert refreshed['source_revision']>before['source_revision']
    assert refreshed['acknowledgements']=={}


def test_in_review_narrative_cannot_be_changed(env):
    ident=case(env);rid=draft(env,ident);action(env,rid,'submit');r=report(env,rid)
    assert env.clients['examiner'].put('/api/reports/'+rid,json={'version':r['version'],'sections':r['sections']}).status_code==409


def test_self_approval_denied_even_after_role_reassignment(env):
    ident=case(env);rid=draft(env,ident);action(env,rid,'submit')
    # Administrative role reassignment is not a route around author/contributor separation.
    with env.store.transaction() as db:db.execute("UPDATE members SET role='reviewer' WHERE case_id=? AND user_id=?",(ident,env.users['examiner']['id']))
    assert action(env,rid,'approve').status_code==403


def test_past_contributor_cannot_approve_when_not_last_editor(env):
    ident=case(env);rid=draft(env,ident)
    with env.store.transaction() as db:db.execute("UPDATE members SET role='examiner' WHERE case_id=? AND user_id=?",(ident,env.users['reviewer']['id']))
    r=report(env,rid)
    assert env.clients['reviewer'].put('/api/reports/'+rid,json={'version':r['version'],'sections':r['sections']}).status_code==200
    r=report(env,rid)
    assert env.clients['examiner'].put('/api/reports/'+rid,json={'version':r['version'],'sections':r['sections']}).status_code==200
    with env.store.transaction() as db:db.execute("UPDATE members SET role='reviewer' WHERE case_id=? AND user_id=?",(ident,env.users['reviewer']['id']))
    assert action(env,rid,'submit').status_code==200
    assert action(env,rid,'approve','reviewer').status_code==403


def test_blocking_comment_must_be_resolved_by_reviewer(env):
    ident=case(env);rid=draft(env,ident);action(env,rid,'submit')
    c=env.clients['reviewer'].post(f'/api/reports/{rid}/comments',json={'body':'Clarify the synthetic record reference.','blocking':True})
    assert c.status_code==201;cid=c.json()['id']
    assert action(env,rid,'approve','reviewer').status_code==409
    assert env.clients['examiner'].post(f'/api/reports/{rid}/comments/{cid}/resolve').status_code==403
    assert env.clients['reviewer'].post(f'/api/reports/{rid}/comments/{cid}/resolve').status_code==200
    assert action(env,rid,'approve','reviewer').status_code==200


def test_stale_approval_cannot_be_issued(env):
    ident=case(env);rid=draft(env,ident);action(env,rid,'submit');action(env,rid,'approve','reviewer')
    add_record(env,ident,'task','New material',{'text':'Changed after approval'})
    assert action(env,rid,'issue').status_code==409
    assert action(env,rid,'return').status_code==200
    r=report(env,rid)
    assert r['status']=='draft' and r['reviewer'] is None and r['approved_digest'] is None


def test_report_revision_conflict(env):
    ident=case(env);rid=draft(env,ident);r=report(env,rid)
    response=env.clients['examiner'].put('/api/reports/'+rid,json={'version':r['version']-1,'sections':r['sections']})
    assert response.status_code==409


def test_template_cannot_be_reordered_during_edit(env):
    ident=case(env);rid=draft(env,ident);r=report(env,rid)
    response=env.clients['examiner'].put('/api/reports/'+rid,json={'version':r['version'],'sections':r['sections'][::-1]})
    assert response.status_code==422


def test_template_versions_are_append_only(env):
    payload={'name':'Synthetic template','sections':[{'key':'opinion','title':'Human opinion','required':True}]}
    a=env.clients['examiner'].post('/api/templates',json=payload)
    b=env.clients['examiner'].post('/api/templates',json=payload)
    assert a.status_code==b.status_code==201
    assert a.json()['version']==1 and b.json()['version']==2
    with pytest.raises(sqlite3.IntegrityError):
        with env.store.transaction() as db:db.execute('DELETE FROM templates WHERE id=?',(a.json()['id'],))


def test_issued_report_pdf_immutable_and_byte_identical(env):
    ident=case(env);rid=issued(env,ident);client=env.clients['examiner']
    first=client.get(f'/api/reports/{rid}/pdf');second=client.get(f'/api/reports/{rid}/pdf')
    assert first.status_code==200 and first.content.startswith(b'%PDF-')
    assert first.content==second.content
    r=report(env,rid)
    assert digest(first.content)==r['pdf_sha256']
    assert 'pdf_ciphertext' not in r
    assert action(env,rid,'return').status_code==409
    with pytest.raises(sqlite3.IntegrityError):
        with env.store.transaction() as db:db.execute("UPDATE reports SET status='draft' WHERE id=?",(rid,))
    with pytest.raises(sqlite3.IntegrityError):
        with env.store.transaction() as db:db.execute('DELETE FROM reports WHERE id=?',(rid,))


def test_supplementary_report_preserves_previously_issued_version(env):
    ident=case(env);initial=issued(env,ident);original=report(env,initial)
    add_record(env,ident,'task','Additional evidence review',{'text':'Synthetic new material'})
    c=current(env,ident);template=env.clients['examiner'].get('/api/templates').json()[0]['id']
    payload={'revision':c['revision'],'template_id':template,'kind':'supplementary','parent_id':initial}
    response=env.clients['examiner'].post(f'/api/cases/{ident}/reports',json=payload)
    assert response.status_code==201
    supplementary=report(env,response.json()['id'])
    assert supplementary['number']==2 and supplementary['parent_id']==initial
    assert report(env,initial)['snapshot']==original['snapshot']
    assert report(env,initial)['checks']['case_changed_since_issue']


def test_supplement_requires_latest_issued_parent(env):
    ident=case(env);issued(env,ident);c=current(env,ident)
    template=env.clients['examiner'].get('/api/templates').json()[0]['id']
    response=env.clients['examiner'].post(f'/api/cases/{ident}/reports',json={'revision':c['revision'],'template_id':template})
    assert response.status_code==422


def test_retired_finding_reference_detected_after_refresh(env):
    ident=case(env);record_id=add_record(env,ident);rid=draft(env,ident)
    c=current(env,ident);record=c['records'][0]
    response=env.clients['examiner'].put(f'/api/cases/{ident}/records/{record_id}',json={'revision':c['revision'],'data':record['data'],'active':False,'reason':'Erroneous synthetic entry retained in history'})
    assert response.status_code==200
    action(env,rid,'refresh')
    assert any(i['code']=='missing_reference' for i in report(env,rid)['checks']['issues'])


def test_custody_chronology_and_independent_countersignature(env):
    ident=case(env);specimen=add_record(env,ident,'specimen','SPEC-1',{'text':'Synthetic specimen','custodian':'Desk A'})
    c=current(env,ident)
    payload={'revision':c['revision'],'specimen_id':specimen,'from_custodian':'Desk A','to_custodian':'Desk B',
             'seal':'TEST-01 intact','purpose':'Synthetic documented handover','occurred_at':datetime.now(timezone.utc).isoformat()}
    r=env.clients['examiner'].post(f'/api/cases/{ident}/custody',json=payload)
    assert r.status_code==201,r.text;transfer=r.json()['id'];c=current(env,ident)
    assert env.clients['examiner'].post(f'/api/cases/{ident}/custody/{transfer}/accept',json={'revision':c['revision']}).status_code==403
    assert env.clients['reviewer'].post(f'/api/cases/{ident}/custody/{transfer}/accept',json={'revision':c['revision']}).status_code==200
    c=current(env,ident);payload.update(revision=c['revision'],from_custodian='Desk B',to_custodian='Desk C',occurred_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
    assert env.clients['examiner'].post(f'/api/cases/{ident}/custody',json=payload).status_code==422


def test_custody_rejects_naive_future_and_wrong_holder(env):
    ident=case(env);specimen=add_record(env,ident,'specimen','SPEC-1',{'custodian':'Desk A'});c=current(env,ident)
    payload={'revision':c['revision'],'specimen_id':specimen,'from_custodian':'Wrong','to_custodian':'Desk B','seal':'TEST','purpose':'Synthetic handover','occurred_at':datetime.now(timezone.utc).isoformat()}
    assert env.clients['examiner'].post(f'/api/cases/{ident}/custody',json=payload).status_code==422
    payload.update(from_custodian='Desk A',occurred_at='2026-09-01T10:00:00')
    assert env.clients['examiner'].post(f'/api/cases/{ident}/custody',json=payload).status_code==422
    payload['occurred_at']=(datetime.now(timezone.utc)+timedelta(days=3)).isoformat()
    assert env.clients['examiner'].post(f'/api/cases/{ident}/custody',json=payload).status_code==422


def test_audit_chain_and_append_only_guards(env):
    ident=case(env);add_record(env,ident);log=env.clients['examiner'].get(f'/api/cases/{ident}/audit').json()
    assert log['verified'] and verify_audit(log['events'])
    modified=[dict(e) for e in log['events']];modified[0]['action']='tampered'
    assert not verify_audit(modified)
    with pytest.raises(sqlite3.IntegrityError):
        with env.store.transaction() as db:db.execute('DELETE FROM audit WHERE scope=?',(ident,))
    with pytest.raises(sqlite3.IntegrityError):
        with env.store.transaction() as db:db.execute("UPDATE audit SET action='x' WHERE scope=?",(ident,))


def test_report_access_denied_without_membership(env):
    ident=case(env);rid=draft(env,ident)
    assert env.clients['outsider'].get('/api/reports/'+rid).status_code==404
    assert env.clients['outsider'].get('/api/reports/'+rid+'/pdf').status_code==404
