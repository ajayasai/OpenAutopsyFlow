"""Authenticated review-workbench routes, sharing the existing case permissions."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response
from pydantic import Field
from . import review as R, service as V
from .schemas import Strict
from .security import authenticate
from .store import audit, audit_events, canonical

User = Annotated[dict, Depends(authenticate)]


class Attestation(Strict):
    version: int = Field(ge=1)
    evidence_id: str = Field(min_length=1, max_length=80)
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    basis_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    statement: str = Field(min_length=10, max_length=2000)
    acknowledged: Literal[True]


def router(store):
    routes = APIRouter()

    def allowed(db, report_id, user):
        report = V.report_row(db, report_id)
        V.access(db, report['case_id'], user)
        return report

    @routes.get('/review', include_in_schema=False)
    def workbench_page():
        # Only a static shell is public; every data route is authenticated.
        return FileResponse(Path(__file__).with_name('static') / 'workbench.html')

    @routes.get('/api/reports/{report_id}/workbench')
    def workbench(report_id: str, user: User):
        with store.transaction() as db:
            report = allowed(db, report_id, user)
            role = V.access(db, report['case_id'], user)
            current = V.snapshot(db, report['case_id'])
            independent = False
            if role == 'reviewer':
                from fastapi import HTTPException
                try:
                    R.independent_reviewer(db, report, user)
                    independent = True
                except HTTPException:
                    pass
            audit(db, report['case_id'], user['id'], 'report.workbench_viewed', report_id)
            return {'report': V.report_public(report), 'role': role, 'independent_reviewer': independent,
                    'checks': V.checks(db, report), 'links': R.references(report),
                    'comments': [dict(c) for c in db.execute(
                        'SELECT c.*,u.name FROM comments c JOIN users u ON u.id=c.actor '
                        'WHERE report_id=? ORDER BY at,id', (report_id,))],
                    'source_changes': R.snapshot_changes(report['snapshot'], current),
                    'current_case_revision': V.case_row(db, report['case_id'])['revision'],
                    'review': R.review_state(db, report, report['reviewer'] if report['status'] in ('approved', 'issued')
                                            else user['id']),
                    'history': R.report_history(db, report_id)}

    @routes.get('/api/reports/{report_id}/history')
    def history(report_id: str, user: User, page: int = Query(default=1, ge=1, le=100000)):
        with store.transaction() as db:
            report = allowed(db, report_id, user)
            audit(db, report['case_id'], user['id'], 'report.history_viewed', report_id, {'page': page})
            return R.report_history(db, report_id, page)

    @routes.get('/api/reports/{report_id}/history/{version}')
    def revision(report_id: str, version: int, user: User):
        with store.transaction() as db:
            report = allowed(db, report_id, user)
            result = R.historical_report(db, report_id, version)
            audit(db, report['case_id'], user['id'], 'report.revision_viewed', report_id, {'version': version})
            return result

    @routes.get('/api/reports/{report_id}/comparison')
    def comparison(report_id: str, user: User, from_version: int = Query(ge=1), to_version: int = Query(ge=1)):
        with store.transaction() as db:
            report = allowed(db, report_id, user)
            before = R.historical_report(db, report_id, from_version)
            after = R.historical_report(db, report_id, to_version)
            result = R.compare_reports(before['report'], after['report'])
            audit(db, report['case_id'], user['id'], 'report.revisions_compared', report_id,
                  {'from_version': from_version, 'to_version': to_version})
            return result

    @routes.get('/api/reports/{report_id}/review-evidence/{evidence_id}')
    def original(report_id: str, evidence_id: str, user: User, inline: bool = False):
        with store.transaction() as db:
            report = allowed(db, report_id, user)
            target = next((e for e in R.required_evidence(report) if e['id'] == evidence_id), None)
            if not target:
                V.fail('No such evidence source in this report', 404)
            original = V.row_one(db, 'evidence', evidence_id)
            if original['case_id'] != report['case_id'] or original['sha256'] != target['sha256']:
                V.fail('Evidence no longer matches the report snapshot')
            raw = V.evidence_bytes(store, original)
            audit(db, report['case_id'], user['id'], 'report.evidence_opened', report_id,
                  {'evidence_id': evidence_id, 'sha256': target['sha256'],
                   'report_version': report['version'], 'basis_digest': V.report_digest(report)})
        disposition = 'inline' if inline and original['mime'] in ('image/jpeg', 'image/png') else 'attachment'
        return Response(raw, media_type=original['mime'], headers={
            'Content-Disposition': f'{disposition}; filename="{original["filename"]}"',
            'X-Evidence-SHA256': original['sha256']})

    @routes.post('/api/reports/{report_id}/review-receipts', status_code=201)
    def receipt(report_id: str, payload: Attestation, user: User):
        with store.transaction() as db:
            report = allowed(db, report_id, user)
            return {'id': R.attest(db, report, user, payload)}

    @routes.post('/api/cases/{case_id}/audit-checkpoint')
    def checkpoint(case_id: str, user: User):
        with store.transaction() as db:
            V.access(db, case_id, user)
            audit(db, case_id, user['id'], 'audit.checkpoint_exported', case_id)
            checkpoint = R.make_checkpoint(store, audit_events(db, case_id), case_id)
        return Response(canonical(checkpoint), media_type='application/json', headers={
            'Content-Disposition': f'attachment; filename="audit-checkpoint-{case_id}.json"'})

    return routes
