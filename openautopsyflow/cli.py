"""Local administration. Secrets are entered interactively, never embedded in source."""
from __future__ import annotations
import argparse
import base64
import getpass
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from pathlib import Path
from datetime import date, timedelta
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .api import create_app
from .security import create_user, PASSWORDS
from .store import Settings, Store, audit, audit_events, canonical, digest, now, verify_audit
from . import schemas as S, service as V
from .documents import verify_bundle


def backup_database(store: Store, output: Path):
    if output.exists():
        raise ValueError('Backup destination already exists; refusing overwrite')
    with store.transaction() as db:
        scopes = [r[0] for r in db.execute('SELECT DISTINCT scope FROM audit')]
        if not all(verify_audit(audit_events(db, scope)) for scope in scopes):
            raise ValueError('Audit verification failed; backup refused')
        audit(db, 'system', 'console', 'database.backup_requested')
    with tempfile.TemporaryDirectory(prefix='oaf-backup-') as temp:
        path=Path(temp)/'snapshot.sqlite3'
        with store.read() as source:
            dest=sqlite3.connect(path)
            try:
                source.backup(dest)
            finally:
                dest.close()
        raw=path.read_bytes()
    encrypted=b'OAFB1'+store.seal(raw,'backup:v1')
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(encrypted)
    return {'bytes':len(encrypted),'sha256':digest(encrypted)}


def restore_database(settings: Settings, source: Path):
    destination=settings.data_dir/'casework.sqlite3'
    if destination.exists() or (settings.data_dir.exists() and any(x.name != '.demo-key' for x in settings.data_dir.iterdir())):
        raise ValueError('Restore target already contains a database; choose a new empty data directory')
    data=source.read_bytes()
    if not data.startswith(b'OAFB1'):
        raise ValueError('Not an OpenAutopsyFlow encrypted backup')
    payload=data[5:]
    raw=AESGCM(settings.key).decrypt(payload[:12],payload[12:],b'backup:v1')
    with tempfile.TemporaryDirectory(prefix='oaf-restore-') as temp:
        path=Path(temp)/'snapshot.sqlite3'
        path.write_bytes(raw)
        path.chmod(0o600)
        db=sqlite3.connect(path)
        db.row_factory=sqlite3.Row
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('SQLite integrity check failed')
            if db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]!='1':
                raise ValueError('Unsupported backup schema')
            for row in db.execute('SELECT DISTINCT scope FROM audit'):
                if not verify_audit(audit_events(db,row[0])):
                    raise ValueError('Backup audit chain failed validation')
        finally:
            db.close()
        settings.data_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:
            f.write(raw)
    restored=Store(settings)
    with restored.transaction() as db:
        db.execute('DELETE FROM sessions')
        audit(db,'system','console','database.restored_sessions_revoked')


def seed_demo(store: Store):
    if not store.settings.demo:
        raise ValueError('Synthetic fixtures require OAF_DEMO=1')
    with store.read() as db:
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise ValueError('Demo setup requires an empty database; existing records will not be modified')
    passwords={name:secrets.token_urlsafe(18) for name in ('examiner','reviewer','coordinator','auditor')}
    people={}
    with store.transaction() as db:
        for name,password in passwords.items():
            people[name]={'id':create_user(db,name,'Demo '+name.title(),password,admin=name=='examiner')}
        template=db.execute('SELECT id FROM templates LIMIT 1').fetchone()[0]
        today=date.today()
        for index,description in enumerate(('SYNTHETIC · Training record A','SYNTHETIC · Training record B','SYNTHETIC · Training record C'),1):
            examiner=people['examiner']
            case_id=V.create_case(db,examiner,S.CaseData(case_no=f'DEMO-{today.year}-{index:04d}',
                examination_date=today-timedelta(days=index),requesting_authority='Synthetic teaching authority',
                examiner='Demo Examiner',subject_reference=description,identification='provisional',
                priority='urgent' if index==2 else 'routine',due_date=today+timedelta(days=7),
                notes='Entirely fictional software-testing fixture. No real person, medical finding or legal opinion.'))
            for name in ('reviewer','coordinator','auditor'):
                db.execute('INSERT INTO members VALUES (?,?,?)',(case_id,people[name]['id'],name))
            def add(kind,label,**data):
                revision=V.case_row(db,case_id)['revision']
                return V.add_record(db,case_id,examiner,S.RecordCreate(revision=revision,kind=kind,label=label,data=S.RecordData(**data)))
            add('external','External examination',text='Synthetic training entry: an external examination record is available for review.')
            add('internal','Internal examination',text='Synthetic training entry: internal findings are documented separately from examiner opinion.')
            add('organ','Example organ measurement',text='Fictional measurement for unit-handling tests only.',region='Training organ',weight_g=350)
            injury=add('injury','1',number=1,text='Synthetic superficial mark for testing linked injury documentation; not a clinical example.',region='Training region',length_mm=12,width_mm=3,laterality='left')
            specimen=add('specimen','SPEC-DEMO-01',text='Synthetic laboratory specimen placeholder.',container='Training container',preservative='Not applicable: no biological material',seal='DEMO-SEAL-01',custodian='Demo laboratory desk',volume_ml=10)
            add('lab','Toxicology result pending',text='Synthetic request; no real test ordered.',specimen_id=specimen,assignee='Demo laboratory',due_date=today-timedelta(days=1) if index==2 else today+timedelta(days=3))
            add('task','Request supporting records',text='Obtain a fictional requisition used in this training exercise.',assignee='Demo Coordinator',due_date=today+timedelta(days=2))
            raw=b'SYNTHETIC EVIDENCE ONLY. Linked training finding. No real case information.\n'
            revision=V.case_row(db,case_id)['revision']
            V.add_evidence(db,store,case_id,examiner,revision,'document',injury,'synthetic-finding.txt','text/plain',raw,('clean','SYNTHETIC-DEMO-BYPASS-NOT-A-MALWARE-SCAN'))
            report=V.make_report(db,case_id,examiner,S.ReportCreate(revision=V.case_row(db,case_id)['revision'],template_id=template))
            r=V.report_row(db,report)
            for section in r['sections']:
                if section['key']=='opinion':
                    section['text']='SYNTHETIC TRAINING ONLY. No medical opinion is offered. Supporting investigations remain pending in this software demonstration.'
            if index==1:
                r['sections'][4]['text']+='\n\nInjury 4 is referenced here intentionally to demonstrate a missing-record prompt.'
            V.edit_report(db,r,examiner,S.ReportUpdate(version=r['version'],sections=r['sections']))
            if index==3:
                r=V.report_row(db,report)
                acknowledgements={i['id']:'Synthetic exercise: pending materials are explicitly documented; no final medical opinion is offered.' for i in V.checks(db,r)['issues'] if i['severity']=='warning'}
                V.transition(db,store,r,examiner,'submit',S.ReportAction(version=r['version'],acknowledgements=acknowledgements))
    return passwords


def main():
    parser=argparse.ArgumentParser(description='OpenAutopsyFlow local administration')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('keygen',help='Generate a master key; store outside the repository')
    sub.add_parser('init',help='Initialize an empty deployment and create the first administrator')
    sub.add_parser('demo',help='Create synthetic fixtures with randomly generated passwords (empty database only)')
    p=sub.add_parser('user',help='Create a local account with an interactively entered password')
    p.add_argument('username');p.add_argument('--name',required=True);p.add_argument('--admin',action='store_true')
    p=sub.add_parser('reset-password');p.add_argument('username')
    p=sub.add_parser('mfa-enroll');p.add_argument('username')
    p=sub.add_parser('mfa-reset');p.add_argument('username');p.add_argument('--confirm-console-recovery',action='store_true',required=True)
    sub.add_parser('verify-audit')
    p=sub.add_parser('backup');p.add_argument('output',type=Path)
    p=sub.add_parser('restore');p.add_argument('source',type=Path)
    p=sub.add_parser('verify-bundle');p.add_argument('source',type=Path);p.add_argument('--trusted-key',type=Path)
    sub.add_parser('public-key')
    sub.add_parser('rescan',help='Run the configured malware scanner on quarantined evidence')
    args=parser.parse_args()
    if args.command=='keygen':
        print(base64.b64encode(secrets.token_bytes(32)).decode());return
    if args.command=='verify-bundle':
        key=args.trusted_key.read_text().strip() if args.trusted_key else None
        print(json.dumps(verify_bundle(args.source.read_bytes(),key),indent=2));return
    settings=Settings.from_env()
    if args.command=='restore':
        restore_database(settings,args.source);print('Restored into a previously empty data directory.');return
    app=create_app(settings);store=app.state.store
    if args.command=='demo':
        credentials=seed_demo(store)
        print('SYNTHETIC DEMO ONLY. Passwords are generated locally, not fixed defaults.')
        for username,password in credentials.items():
            print(f'{username}: {password}')
        return
    if args.command=='backup':
        print(json.dumps(backup_database(store,args.output),indent=2));return
    if args.command=='public-key':
        print(store.public_key());return
    if args.command=='verify-audit':
        with store.read() as db:
            results={r[0]:verify_audit(audit_events(db,r[0])) for r in db.execute('SELECT DISTINCT scope FROM audit')}
        print(json.dumps(results,indent=2))
        if not all(results.values()):
            raise SystemExit(1)
        return
    if args.command=='rescan':
        if settings.demo:
            raise ValueError('Rescanning is for a non-demo deployment with an actual malware scanner')
        with store.read() as db:
            candidates=[dict(r) for r in db.execute("SELECT * FROM evidence WHERE scan_status='quarantined'")]
        for e in candidates:
            raw=store.unseal(e['ciphertext'],f"evidence:{e['case_id']}:{e['id']}")
            if digest(raw)!=e['sha256']:
                raise ValueError('Evidence hash mismatch; stop and investigate')
            status,engine=V.scan_upload(raw,settings)
            with store.transaction() as db:
                db.execute('UPDATE evidence SET scan_status=?,scan_engine=? WHERE id=?',(status,engine,e['id']))
                V.touch(db,e['case_id'])
                audit(db,e['case_id'],'console','evidence.rescanned',e['id'],{'status':status,'engine':engine})
        print(f'Rescanned {len(candidates)} quarantined entries.');return
    with store.transaction() as db:
        if args.command=='init':
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise ValueError('Accounts already exist; use the user command')
            username=input('Administrator username: ').strip()
            name=input('Display name: ').strip()
            data=S.UserCreate(username=username,name=name,password=getpass.getpass('Password (14+ characters): '),admin=True)
            create_user(db,data.username,data.name,data.password,True)
            print('Administrator initialized. No existing cases were modified.')
        elif args.command=='user':
            data=S.UserCreate(username=args.username,name=args.name,password=getpass.getpass('Password (14+ characters): '),admin=args.admin)
            create_user(db,data.username,data.name,data.password,data.admin)
            print('Account created. Assign case-specific access in the workspace.')
        else:
            user=db.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE',(args.username,)).fetchone()
            if not user:
                raise ValueError('Unknown username')
            if args.command=='reset-password':
                password=getpass.getpass('New password (14+ characters): ')
                if not 14<=len(password)<=256:
                    raise ValueError('Password must be 14 to 256 characters')
                db.execute('UPDATE users SET password_hash=? WHERE id=?',(PASSWORDS.hash(password),user['id']))
            elif args.command=='mfa-enroll':
                if user['totp']:
                    raise ValueError('MFA already enrolled; console recovery is required to replace it')
                secret=secrets.token_bytes(20)
                db.execute('UPDATE users SET totp=?,last_totp=-1 WHERE id=?',(store.seal(secret,f"totp:{user['id']}"),user['id']))
                print('Add this secret to an authenticator (TOTP, SHA-1, 6 digits, 30 seconds):')
                print(base64.b32encode(secret).decode())
                print('Keep the enrollment secret private. Verify login before closing your administration session.')
            elif args.command=='mfa-reset':
                db.execute('UPDATE users SET totp=NULL,last_totp=-1 WHERE id=?',(user['id'],))
            db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
            audit(db,'system','console','account.'+args.command,user['id'])


if __name__=='__main__':
    main()
