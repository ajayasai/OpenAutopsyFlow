from __future__ import annotations
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import __version__, schemas as S, service as V
from .documents import render_pdf
from .security import RequestGuard, authenticate, create_user, login, password_ok, PASSWORDS
from .store import Settings, Store, audit, audit_events, canonical, digest, now, uid, verify_audit

User = Annotated[dict, Depends(authenticate)]
DEFAULT_SECTIONS = [
    {'key': 'circumstances', 'title': 'Circumstances and authority', 'required': False},
    {'key': 'external', 'title': 'External examination', 'required': False},
    {'key': 'internal', 'title': 'Internal examination', 'required': False},
    {'key': 'organs', 'title': 'Organ measurements', 'required': False},
    {'key': 'injuries', 'title': 'Numbered injuries', 'required': False},
    {'key': 'specimens', 'title': 'Specimens and investigations', 'required': False},
    {'key': 'opinion', 'title': 'Examiner opinion and limitations', 'required': True},
]


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    store = Store(settings)
    with store.transaction() as db:
        if not db.execute('SELECT id FROM templates LIMIT 1').fetchone():
            ident = uid()
            db.execute('INSERT INTO templates VALUES (?,?,?,?,?)',
                       (ident, 'General autopsy - local validation required', 1, canonical(DEFAULT_SECTIONS), now()))
            audit(db, 'system', 'system', 'template.bootstrapped', ident)
    app = FastAPI(title='OpenAutopsyFlow', version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.hosts), www_redirect=False)
    app.add_middleware(RequestGuard, settings=settings)

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request, exc):
        return JSONResponse({'detail': 'Conflicting identifier, existing active draft, or immutable record. Reload and check the entry.'}, status_code=409)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy(request, exc):
        return JSONResponse({'detail': 'Storage operation could not complete; no partial transaction was committed.'}, status_code=503)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'version': __version__}

    @app.get('/api/config')
    def config():
        return {'name': 'OpenAutopsyFlow', 'version': __version__, 'demo': settings.demo,
                'max_upload_bytes': settings.max_upload, 'public_key': store.public_key(),
                'disclaimer': 'Workflow support only; not validated for clinical or medicolegal deployment.'}

    @app.post('/api/login')
    def sign_in(payload: S.Login, request: Request):
        token, csrf, user = login(store, payload.username, payload.password, payload.otp,
                                  request.client.host if request.client else 'unknown')
        response = JSONResponse({'user': user, 'csrf': csrf})
        response.set_cookie('oaf_session', token, httponly=True, secure=settings.secure_cookie,
                            samesite='strict', max_age=settings.session_hours * 3600, path='/')
        return response

    @app.get('/api/me')
    def me(user: User):
        return {k:v for k,v in user.items() if k != 'token_hash'}

    @app.post('/api/logout')
    def logout(user: User):
        with store.transaction() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (user['token_hash'],))
            audit(db, 'system', user['id'], 'logout')
        response = JSONResponse({'ok': True})
        response.delete_cookie('oaf_session', path='/', secure=settings.secure_cookie, httponly=True, samesite='strict')
        return response

    @app.post('/api/password')
    def change_password(payload: S.PasswordChange, user: User):
        with store.transaction() as db:
            current = V.row_one(db, 'users', user['id'])
            if not password_ok(current['password_hash'], payload.old_password):
                V.fail('Current password is incorrect', 403)
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (PASSWORDS.hash(payload.new_password), user['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
            audit(db, 'system', user['id'], 'password.changed_sessions_revoked')
        return {'ok': True, 'message': 'Sign in again with your new password'}

    @app.get('/api/schema')
    def schema(user: User):
        return app.openapi()

    @app.get('/api/users')
    def users(user: User):
        if not user['admin']:
            V.fail('Administrator only', 403)
        with store.read() as db:
            return [dict(r) for r in db.execute('SELECT id,username,name,admin,active FROM users ORDER BY username')]

    @app.post('/api/users', status_code=201)
    def add_user(payload: S.UserCreate, user: User):
        if not user['admin']:
            V.fail('Administrator only', 403)
        with store.transaction() as db:
            ident = create_user(db, payload.username, payload.name, payload.password, payload.admin)
            audit(db, 'system', user['id'], 'user.provisioned', ident)
        return {'id': ident}

    @app.post('/api/users/{user_id}/disable')
    def disable_user(user_id: str, user: User):
        if not user['admin'] or user_id == user['id']:
            V.fail('An administrator may disable another account only', 403)
        with store.transaction() as db:
            V.row_one(db, 'users', user_id)
            db.execute('UPDATE users SET active=0 WHERE id=?', (user_id,))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            audit(db, 'system', user['id'], 'user.disabled', user_id)
        return {'ok': True}

    @app.get('/api/templates')
    def templates(user: User):
        with store.read() as db:
            return [{**dict(r), 'sections': json.loads(r['sections'])} for r in
                    db.execute('SELECT * FROM templates ORDER BY name,version DESC')]

    @app.post('/api/templates', status_code=201)
    def template_create(payload: S.TemplateCreate, user: User):
        if not user['admin']:
            V.fail('Administrator only', 403)
        with store.transaction() as db:
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM templates WHERE name=?', (payload.name,)).fetchone()[0]
            ident = uid()
            db.execute('INSERT INTO templates VALUES (?,?,?,?,?)',
                       (ident, payload.name, version, canonical([s.model_dump() for s in payload.sections]), now()))
            audit(db, 'system', user['id'], 'template.created', ident, {'version': version})
        return {'id': ident, 'version': version}

    @app.get('/api/cases')
    def list_cases(user: User, q: str = '', status: str = '', page: int = 1, pending_only: bool = False):
        if len(q) > 200 or page < 1 or page > 100000:
            V.fail('Invalid search', 422)
        if status not in ('', 'examination', 'pending', 'draft', 'in_review', 'approved', 'issued'):
            V.fail('Unknown workflow status', 422)
        # Filter and aggregate in the database; never load all findings to paginate cases.
        sql = """
        WITH visible AS (
          SELECT c.*,m.role FROM cases c JOIN members m ON c.id=m.case_id
          WHERE m.user_id=? AND instr(oaf_casefold(c.case_no || ' ' ||
            json_extract(c.data,'$.subject_reference') || ' ' ||
            json_extract(c.data,'$.requesting_authority')),?) > 0
        ), work AS (
          SELECT r.case_id,
            SUM(CASE WHEN r.kind IN ('lab','task') AND json_extract(r.data,'$.status')
                NOT IN ('complete','reviewed','cancelled') THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN r.kind IN ('lab','task') AND json_extract(r.data,'$.status')
                NOT IN ('complete','reviewed','cancelled') AND json_extract(r.data,'$.due_date') < ?
                THEN 1 ELSE 0 END) AS overdue_count,
            SUM(CASE WHEN r.kind='injury' THEN 1 ELSE 0 END) AS injury_count
          FROM records r JOIN visible v ON r.case_id=v.id WHERE r.active=1 GROUP BY r.case_id
        ), latest AS (
          SELECT r.case_id,r.status,ROW_NUMBER() OVER (PARTITION BY r.case_id ORDER BY r.number DESC) AS n
          FROM reports r JOIN visible v ON v.id=r.case_id
        ), summary AS (
          SELECT v.*,COALESCE(w.pending_count,0) AS pending_count,
            COALESCE(w.overdue_count,0) AS overdue_count,COALESCE(w.injury_count,0) AS injury_count,
            COALESCE(l.status,CASE WHEN w.pending_count > 0 THEN 'pending' ELSE 'examination' END) AS status
          FROM visible v LEFT JOIN work w ON v.id=w.case_id
          LEFT JOIN latest l ON v.id=l.case_id AND l.n=1
        ), filtered AS (
          SELECT * FROM summary WHERE (?='' OR status=?) AND (?=0 OR pending_count>0 OR status='in_review')
        )
        """
        params = (user['id'], q.casefold(), date.today().isoformat(), status, status, int(pending_only))
        with store.read() as db:
            metrics = dict(db.execute(sql + """SELECT COUNT(*) AS cases,
                COALESCE(SUM(pending_count),0) AS pending,
                COALESCE(SUM(overdue_count),0) AS overdue,
                COALESCE(SUM(CASE WHEN status='in_review' THEN 1 ELSE 0 END),0) AS in_review
                FROM filtered""", params).fetchone())
            rows = db.execute(sql + 'SELECT * FROM filtered ORDER BY updated_at DESC,id LIMIT 50 OFFSET ?',
                              (*params, (page-1)*50)).fetchall()
            items = [{**json.loads(r['data']), **{k:r[k] for k in
                      ('id','revision','role','status','pending_count','overdue_count','injury_count','updated_at')}} for r in rows]
            return {'total':metrics['cases'], 'page':page, 'metrics':metrics, 'items':items}

    @app.post('/api/cases', status_code=201)
    def case_create(payload: S.CaseData, user: User):
        with store.transaction() as db:
            return {'id': V.create_case(db, user, payload)}

    @app.get('/api/cases/{case_id}')
    def case_detail(case_id: str, user: User):
        with store.transaction() as db:
            role = V.access(db, case_id, user)
            c = V.case_row(db, case_id)
            members = [dict(m) for m in db.execute('SELECT m.user_id,m.role,u.username,u.name FROM members m JOIN users u ON m.user_id=u.id WHERE case_id=?', (case_id,))]
            reports = [V.report_public(V.report_row(db, r['id'])) for r in db.execute('SELECT id FROM reports WHERE case_id=? ORDER BY number DESC', (case_id,))]
            audit(db, case_id, user['id'], 'case.viewed', case_id)
            return {**c, **V.snapshot(db, case_id), 'role': role, 'members': members, 'reports': reports}

    @app.put('/api/cases/{case_id}')
    def case_update(case_id: str, payload: S.CaseUpdate, user: User):
        with store.transaction() as db:
            V.access(db, case_id, user, V.WRITERS)
            c = V.case_row(db, case_id)
            V.check_revision(c, payload.revision)
            data = payload.model_dump(mode='json', exclude={'revision','reason'})
            db.execute('UPDATE cases SET case_no=?,data=? WHERE id=?', (data['case_no'], canonical(data), case_id))
            V.touch(db, case_id)
            db.execute('INSERT INTO case_history VALUES (?,?,?,?,?,?,?)',
                       (uid(), case_id, c['revision']+1, canonical(data), user['id'], payload.reason, now()))
            audit(db, case_id, user['id'], 'case.amended', case_id, {'reason':payload.reason, 'data_hash':digest(data)})
        return {'ok':True}

    @app.post('/api/cases/{case_id}/members')
    def member_add(case_id: str, payload: S.Member, user: User):
        with store.transaction() as db:
            V.access(db, case_id, user)
            if not user['admin']:
                V.fail('Only a case-member administrator may change assignments', 403)
            c = V.case_row(db, case_id)
            V.check_revision(c, payload.revision)
            target = V.row_one(db, 'users', payload.user_id)
            if not target['active']:
                V.fail('Cannot assign a disabled account',422)
            db.execute('INSERT INTO members VALUES (?,?,?) ON CONFLICT(case_id,user_id) DO UPDATE SET role=excluded.role',
                       (case_id,payload.user_id,payload.role))
            V.touch(db, case_id)
            audit(db, case_id, user['id'], 'membership.assigned', payload.user_id, {'role':payload.role})
        return {'ok':True}

    @app.post('/api/cases/{case_id}/members/{target_id}/revoke')
    def member_revoke(case_id: str, target_id: str, payload: S.RevokeMember, user: User):
        with store.transaction() as db:
            V.access(db, case_id, user)
            if not user['admin'] or target_id == user['id']:
                V.fail('A case-member administrator may revoke another member only', 403)
            V.check_revision(V.case_row(db, case_id), payload.revision)
            existing = db.execute('SELECT role FROM members WHERE case_id=? AND user_id=?',
                                  (case_id, target_id)).fetchone()
            if not existing:
                V.fail('Case membership not found', 404)
            if existing['role'] == 'examiner' and db.execute("SELECT COUNT(*) FROM members m JOIN users u ON u.id=m.user_id WHERE m.case_id=? AND m.role='examiner' AND u.active=1", (case_id,)).fetchone()[0] <= 1:
                V.fail('Retain at least one active assigned examiner')
            db.execute('DELETE FROM members WHERE case_id=? AND user_id=?', (case_id, target_id))
            V.touch(db, case_id)
            audit(db, case_id, user['id'], 'membership.revoked', target_id, {'reason':payload.reason})
        return {'ok': True}

    @app.post('/api/cases/{case_id}/records', status_code=201)
    def record_create(case_id: str, payload: S.RecordCreate, user: User):
        with store.transaction() as db:
            return {'id': V.add_record(db,case_id,user,payload)}

    @app.put('/api/cases/{case_id}/records/{record_id}')
    def record_update(case_id: str,record_id: str,payload: S.RecordUpdate,user: User):
        with store.transaction() as db:
            V.update_record(db,case_id,record_id,user,payload)
        return {'ok':True}

    @app.get('/api/cases/{case_id}/records/{record_id}/history')
    def history(case_id: str,record_id: str,user: User):
        with store.transaction() as db:
            V.access(db,case_id,user)
            record = V.row_one(db,'records',record_id)
            if record['case_id'] != case_id:
                V.fail('Not found or not permitted',404)
            audit(db, case_id, user['id'], 'record.history_viewed', record_id)
            return [{**dict(r),'data':json.loads(r['data'])} for r in db.execute('SELECT * FROM record_history WHERE record_id=? ORDER BY version',(record_id,))]

    @app.post('/api/cases/{case_id}/evidence', status_code=201)
    async def upload(case_id: str,user: User, revision: Annotated[int,Form()],
                     kind: Annotated[Literal['photo','requisition','document','lab_result'],Form()],
                     file: Annotated[UploadFile,File()], finding_id: Annotated[str,Form()]=''):
        # Authorize before reading or scanning bytes. Re-check within final transaction.
        with store.read() as db:
            V.access(db,case_id,user,V.WRITERS)
            V.check_revision(V.case_row(db,case_id),revision)
        try:
            raw = await file.read(settings.max_upload+1)
        finally:
            await file.close()
        filename,mime = V.inspect_upload(raw,file.filename or 'document',kind,settings)
        from starlette.concurrency import run_in_threadpool
        scan = await run_in_threadpool(V.scan_upload,raw,settings)
        with store.transaction() as db:
            ident = V.add_evidence(db,store,case_id,user,revision,kind,finding_id or None,filename,mime,raw,scan)
        return {'id':ident,'scan_status':scan[0]}

    @app.get('/api/cases/{case_id}/evidence/{evidence_id}/content')
    def download(case_id: str,evidence_id: str,user: User,inline: bool=False):
        with store.transaction() as db:
            V.access(db,case_id,user)
            e = V.row_one(db,'evidence',evidence_id)
            if e['case_id'] != case_id:
                V.fail('Not found or not permitted',404)
            raw = V.evidence_bytes(store,e)
            audit(db,case_id,user['id'],'evidence.downloaded',evidence_id,{'sha256':e['sha256']})
        disposition = 'inline' if inline and e['mime'] in ('image/png','image/jpeg') else 'attachment'
        return Response(raw,media_type=e['mime'],headers={'Content-Disposition':f'{disposition}; filename="{e["filename"]}"'})

    @app.post('/api/cases/{case_id}/evidence/{evidence_id}/review')
    def review_evidence(case_id: str,evidence_id: str,payload: S.Revision,user: User):
        with store.transaction() as db:
            V.access(db,case_id,user,V.CLINICAL)
            V.check_revision(V.case_row(db,case_id),payload.revision)
            e = V.row_one(db,'evidence',evidence_id)
            if e['case_id'] != case_id:
                V.fail('Not found or not permitted',404)
            if e['scan_status'] != 'clean':
                V.fail('Quarantined evidence cannot be marked reviewed',423)
            if e['reviewed_at']:
                V.fail('Evidence review is already recorded')
            db.execute('UPDATE evidence SET reviewed_by=?,reviewed_at=? WHERE id=?',(user['id'],now(),evidence_id))
            V.touch(db,case_id)
            audit(db,case_id,user['id'],'evidence.reviewed',evidence_id)
        return {'ok':True}

    @app.post('/api/cases/{case_id}/custody',status_code=201)
    def transfer(case_id: str,payload: S.CustodyCreate,user: User):
        with store.transaction() as db:
            return {'id':V.custody_transfer(db,case_id,user,payload)}

    @app.post('/api/cases/{case_id}/custody/{transfer_id}/accept')
    def accept_transfer(case_id: str,transfer_id: str,payload: S.Revision,user: User):
        with store.transaction() as db:
            V.access(db,case_id,user,V.WRITERS + ('reviewer',))
            V.check_revision(V.case_row(db,case_id),payload.revision)
            transfer = V.row_one(db,'custody',transfer_id)
            if transfer['case_id'] != case_id:
                V.fail('Not found or not permitted',404)
            if transfer['actor'] == user['id'] or transfer['accepted_at']:
                V.fail('A different assigned staff member must countersign once',403)
            db.execute('UPDATE custody SET accepted_by=?,accepted_at=? WHERE id=?',(user['id'],now(),transfer_id))
            V.touch(db,case_id)
            audit(db,case_id,user['id'],'custody.countersigned',transfer_id)
        return {'ok':True}

    @app.post('/api/cases/{case_id}/reports',status_code=201)
    def report_create(case_id: str,payload: S.ReportCreate,user: User):
        with store.transaction() as db:
            return {'id':V.make_report(db,case_id,user,payload)}

    @app.get('/api/reports/{report_id}')
    def report_get(report_id: str,user: User):
        with store.transaction() as db:
            r = V.report_row(db,report_id)
            V.access(db,r['case_id'],user)
            audit(db,r['case_id'],user['id'],'report.viewed',report_id)
            comments = [dict(c) for c in db.execute('SELECT c.*,u.name FROM comments c JOIN users u ON c.actor=u.id WHERE report_id=? ORDER BY at',(report_id,))]
            current = V.snapshot(db,r['case_id'])
            old_records = {x['id']:x for x in r['snapshot']['records']}
            new_records = {x['id']:x for x in current['records']}
            changes = [k for k in sorted(set(old_records)|set(new_records)) if old_records.get(k) != new_records.get(k)]
            old_evidence = {x['id']:x for x in r['snapshot']['evidence']}
            new_evidence = {x['id']:x for x in current['evidence']}
            return {**V.report_public(r),'checks':V.checks(db,r),'comments':comments,
                    'changes_since_snapshot':{'records':changes,
                      'evidence':[k for k in sorted(set(old_evidence)|set(new_evidence)) if old_evidence.get(k)!=new_evidence.get(k)],
                      'case_metadata_changed':current['case']!=r['snapshot']['case'],
                      'custody_changed':current['custody']!=r['snapshot']['custody']}}

    @app.put('/api/reports/{report_id}')
    def report_edit(report_id: str,payload: S.ReportUpdate,user: User):
        with store.transaction() as db:
            V.edit_report(db,V.report_row(db,report_id),user,payload)
        return {'ok':True}

    @app.post('/api/reports/{report_id}/actions/{action}')
    def report_transition(report_id: str,action: Literal['refresh','return','submit','approve','issue'],payload: S.ReportAction,user: User):
        with store.transaction() as db:
            result = V.transition(db,store,V.report_row(db,report_id),user,action,payload)
        return {'ok':True,**result}

    @app.get('/api/reports/{report_id}/pdf')
    def pdf_download(report_id: str,user: User):
        with store.transaction() as db:
            r = V.report_row(db,report_id)
            V.access(db,r['case_id'],user)
            if r['status']=='issued':
                raw=store.unseal(r['pdf_ciphertext'],f"report:{r['case_id']}:{r['id']}")
                if digest(raw)!=r['pdf_sha256']:
                    V.fail('Issued PDF failed integrity validation')
            else:
                raw=render_pdf(r,settings)
            audit(db,r['case_id'],user['id'],'report.pdf_downloaded',report_id,{'sha256':digest(raw),'status':r['status']})
        return Response(raw,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="report-{r["number"]}-{r["status"]}.pdf"'})

    @app.post('/api/reports/{report_id}/comments',status_code=201)
    def add_comment(report_id: str,payload: S.CommentCreate,user: User):
        with store.transaction() as db:
            r=V.report_row(db,report_id)
            role=V.access(db,r['case_id'],user,V.CLINICAL)
            if r['status']=='issued':
                V.fail('Use a supplementary draft for post-issue discussion')
            if payload.blocking and role!='reviewer':
                V.fail('Only reviewers can mark comments blocking',403)
            ident=uid()
            db.execute('INSERT INTO comments VALUES (?,?,?,?,?,?,?,?)',(ident,report_id,user['id'],payload.body,int(payload.blocking),now(),None,None))
            audit(db,r['case_id'],user['id'],'report.comment_added',ident,{'report_id':report_id,'blocking':payload.blocking})
        return {'id':ident}

    @app.post('/api/reports/{report_id}/comments/{comment_id}/resolve')
    def resolve(report_id: str,comment_id: str,user: User):
        with store.transaction() as db:
            r=V.report_row(db,report_id)
            V.access(db,r['case_id'],user,('reviewer',))
            if r['status']=='issued':
                V.fail('Issued report review history is frozen')
            c=V.row_one(db,'comments',comment_id)
            if c['report_id']!=report_id:
                V.fail('Not found or not permitted',404)
            if c['resolved_at']:
                V.fail('Comment is already resolved')
            db.execute('UPDATE comments SET resolved_by=?,resolved_at=? WHERE id=?',(user['id'],now(),comment_id))
            audit(db,r['case_id'],user['id'],'report.comment_resolved',comment_id)
        return {'ok':True}

    @app.get('/api/cases/{case_id}/audit')
    def get_audit(case_id: str,user: User):
        with store.transaction() as db:
            V.access(db,case_id,user)
            audit(db,case_id,user['id'],'audit.viewed',case_id)
            events=audit_events(db,case_id)
            return {'verified':verify_audit(events),'count':len(events),'head':events[-1]['hash'] if events else None,
                    'events':events,'limitation':'Local hash chains do not detect privileged rewriting or tail truncation without a separately retained trusted checkpoint.'}

    @app.post('/api/cases/{case_id}/export')
    def export(case_id: str,user: User,include_evidence: bool=False):
        with store.transaction() as db:
            raw=V.export_case(db,store,case_id,user,include_evidence)
        return Response(raw,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="case-{case_id}.zip"'})

    static=Path(__file__).with_name('static')
    @app.get('/')
    def home():
        return FileResponse(static/'index.html')
    app.mount('/static',StaticFiles(directory=static),name='static')
    from .review_api import router as review_router
    app.include_router(review_router(store))
    return app
