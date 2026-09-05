"""Evidence-specific independent review, revision comparisons and audit checkpoints.

All medical interpretation remains with the examiner/reviewer. Receipts record a
human attestation, not proof of comprehension or medical correctness.
"""
from __future__ import annotations

import base64
import json
from typing import Any
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .rules import TOKEN, NATURAL_INJURY
from .store import audit, canonical, digest, now, uid, verify_audit

CHECKPOINT_DOMAIN = b'OpenAutopsyFlow/audit-checkpoint/v1\x00'
_MISSING = object()


def field_changes(before: Any, after: Any, path: str = '') -> list[dict]:
    """JSON-pointer field differences, preserving absent versus explicit null."""
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(set(before) | set(after)):
            pointer = path + '/' + key.replace('~', '~0').replace('/', '~1')
            changes.extend(field_changes(before.get(key, _MISSING), after.get(key, _MISSING), pointer))
        return changes
    if before is not _MISSING and after is not _MISSING and before == after:
        return []
    return [{'path': path or '/', 'before_present': before is not _MISSING,
             'after_present': after is not _MISSING,
             'before': None if before is _MISSING else before,
             'after': None if after is _MISSING else after}]


def snapshot_changes(before: dict, after: dict) -> dict:
    result = {'case': field_changes(before['case'], after['case'])}
    for collection in ('records', 'evidence', 'custody'):
        old = {r['id']: r for r in before.get(collection, [])}
        new = {r['id']: r for r in after.get(collection, [])}
        changes = []
        for ident in sorted(set(old) | set(new)):
            a, b = old.get(ident), new.get(ident)
            if a == b:
                continue
            # Show meaningful fields without burying them in timestamp changes.
            def useful(row):
                if row is None:
                    return _MISSING
                return {k: v for k, v in row.items()
                        if k not in ('created_at', 'updated_at', 'version', 'ciphertext')}
            fields = field_changes(useful(a), useful(b))
            if not fields:
                continue
            change = 'added' if a is None else 'removed' if b is None else 'amended'
            if collection == 'records' and a and b:
                if a['active'] and not b['active']:
                    change = 'retired'
                elif not a['active'] and b['active']:
                    change = 'reactivated'
            changes.append({'id': ident, 'label': (b or a).get('label', (b or a).get('filename', ident)),
                            'change': change, 'before_version': a.get('version') if a else None,
                            'after_version': b.get('version') if b else None, 'fields': fields})
        result[collection] = changes
    result['change_count'] = sum(len(v) for v in result.values() if isinstance(v, list))
    return result


def references(report: dict) -> list[dict]:
    """Resolve report links against the frozen report snapshot, never live data."""
    snap = report['snapshot']
    records = {r['id']: r for r in snap['records']}
    injuries = {str(r['data']['number']): r for r in records.values() if r['kind'] == 'injury'}
    evidence = {e['id']: e for e in snap['evidence']}
    by_finding: dict[str, list[dict]] = {}
    for evidence_item in evidence.values():
        by_finding.setdefault(evidence_item['finding_id'], []).append(evidence_item)
    links = []
    for section in report['sections']:
        tokens = set(TOKEN.findall(section['text']))
        tokens |= {('injury', n) for n in NATURAL_INJURY.findall(section['text'])}
        for kind, ident in sorted(tokens):
            target = {'record': records, 'injury': injuries, 'evidence': evidence}[kind].get(ident)
            status = 'missing' if target is None else 'resolved'
            if target and kind != 'evidence' and not target['active']:
                status = 'retired'
            if target and kind == 'evidence' and target['scan_status'] != 'clean':
                status = 'quarantined'
            supports = []
            if target and kind != 'evidence':
                supports = by_finding.get(target['id'], [])
            elif target:
                supports = [target]
            links.append({'section': section['key'], 'section_title': section['title'],
                          'kind': kind, 'reference': ident, 'source_id': target['id'] if target else None,
                          'status': status, 'source': target,
                          'evidence': [{k: e[k] for k in ('id', 'filename', 'sha256', 'scan_status', 'reviewed_by')}
                                       for e in supports]})
    return links


def required_evidence(report: dict) -> list[dict]:
    """Require originals for lab results and explicit/directly supporting links."""
    reasons: dict[str, set[str]] = {}
    for e in report['snapshot']['evidence']:
        if e['kind'] == 'lab_result':
            reasons.setdefault(e['id'], set()).add('laboratory result in report snapshot')
    for link in references(report):
        for e in link['evidence']:
            reasons.setdefault(e['id'], set()).add('source for section ' + link['section'])
    return [{**e, 'required_because': sorted(reasons[e['id']])}
            for e in report['snapshot']['evidence'] if e['id'] in reasons]


def review_version(report: dict) -> int:
    # Each workflow transition advances exactly one report version. A return to
    # draft and resubmission therefore starts a new review round even without edits.
    offset = {'approved': 1, 'issued': 2}.get(report['status'], 0)
    return report['version'] - offset


def review_state(db, report: dict, reviewer_id: str) -> dict:
    from .service import report_digest
    basis = report_digest(report)
    legacy_issued = report['status'] == 'issued' and db.execute(
        "SELECT 1 FROM report_history WHERE report_id=? AND version=? AND capture_kind='legacy_baseline'",
        (report['id'], report['version'])).fetchone() is not None
    version = review_version(report)
    receipts = [dict(r) for r in db.execute(
        'SELECT * FROM review_receipts WHERE report_id=? AND report_version=? AND reviewer_id=? '
        'AND basis_digest=? ORDER BY created_at,id', (report['id'], version, reviewer_id, basis))]
    completed = {r['evidence_id']: r for r in receipts}
    required = []
    for e in required_evidence(report):
        receipt = completed.get(e['id'])
        required.append({k: e[k] for k in ('id', 'filename', 'sha256', 'kind', 'scan_status', 'required_because')}
                        | {'receipt': receipt if receipt and receipt['evidence_sha256'] == e['sha256'] else None})
    return {'basis_digest': basis, 'report_version': version, 'required': required,
            'remaining': sum(e['receipt'] is None for e in required), 'reviewer_id': reviewer_id,
            'legacy_issued': legacy_issued,
            'disclaimer': 'An attestation records human review, not proof that a medical opinion is correct.'}


def independent_reviewer(db, report: dict, user: dict) -> None:
    from .service import access, fail
    access(db, report['case_id'], user, ('reviewer',))
    active = db.execute('SELECT active FROM users WHERE id=?', (user['id'],)).fetchone()
    contributor = db.execute(
        "SELECT 1 FROM audit WHERE scope=? AND entity=? AND actor=? AND action IN "
        "('report.edited','report.snapshot_refreshed') LIMIT 1",
        (report['case_id'], report['id'], user['id'])).fetchone()
    if not active or not active['active'] or contributor or user['id'] in (report['author'], report['last_editor']):
        fail('An active independent assigned reviewer is required', 403)


def attest(db, report: dict, user: dict, payload) -> str:
    from .service import case_row, check_revision, fail, report_digest, row_one, version_check
    independent_reviewer(db, report, user)
    version_check(report, payload.version)
    if report['status'] != 'in_review':
        fail('Evidence attestations are recorded only during the current in-review round')
    check_revision(case_row(db, report['case_id']), report['source_revision'])
    basis = report_digest(report)
    if payload.basis_digest != basis:
        fail('Report content changed; reload before attesting')
    target = next((e for e in required_evidence(report) if e['id'] == payload.evidence_id), None)
    if not target:
        fail('This evidence is not a review source for this report', 422)
    original = row_one(db, 'evidence', target['id'])
    if original['case_id'] != report['case_id'] or original['scan_status'] != 'clean':
        fail('The original must be clean and belong to this case', 423)
    if payload.evidence_sha256 != target['sha256'] or original['sha256'] != target['sha256']:
        fail('Evidence digest changed; attestation refused')
    opened = db.execute(
        "SELECT 1 FROM audit WHERE scope=? AND actor=? AND action='report.evidence_opened' AND entity=? "
        "AND json_extract(details,'$.evidence_id')=? AND json_extract(details,'$.report_version')=? "
        "AND json_extract(details,'$.basis_digest')=? AND json_extract(details,'$.sha256')=? LIMIT 1",
        (report['case_id'], user['id'], report['id'], target['id'], report['version'], basis, target['sha256'])
    ).fetchone()
    if not opened:
        fail('Open the original through this report workbench before recording its review')
    ident = uid()
    db.execute('INSERT INTO review_receipts VALUES (?,?,?,?,?,?,?,?,?)',
               (ident, report['id'], report['version'], basis, target['id'], target['sha256'],
                user['id'], payload.statement, now()))
    audit(db, report['case_id'], user['id'], 'report.evidence_attested', report['id'],
          {'receipt_id': ident, 'evidence_id': target['id'], 'basis_digest': basis,
           'report_version': report['version'], 'sha256': target['sha256']})
    return ident


def enforce_review(db, report: dict, reviewer_id: str) -> None:
    from fastapi import HTTPException
    from .service import fail
    independent_reviewer(db, report, {'id': reviewer_id})
    for evidence in required_evidence(report):
        current = db.execute('SELECT case_id,sha256,scan_status FROM evidence WHERE id=?', (evidence['id'],)).fetchone()
        if not current or current['case_id'] != report['case_id'] or current['sha256'] != evidence['sha256'] or current['scan_status'] != 'clean':
            fail('A required original no longer matches its clean reviewed snapshot; re-review is required')
    missing = [e for e in review_state(db, report, reviewer_id)['required'] if not e['receipt']]
    if missing:
        raise HTTPException(409, {'message': 'The approving reviewer must attest to each required original '
                                 'for this report version in the review workbench.',
                                 'code': 'review_receipts_required', 'evidence_ids': [e['id'] for e in missing]})


def report_history(db, report_id: str, page: int = 1) -> dict:
    total = db.execute('SELECT COUNT(*) FROM report_history WHERE report_id=?', (report_id,)).fetchone()[0]
    rows = db.execute("SELECT version,capture_kind,captured_at,json_extract(data,'$.status') AS status,"
                      "json_extract(data,'$.source_revision') AS source_revision,"
                      "json_extract(data,'$.last_editor') AS last_editor FROM report_history "
                      'WHERE report_id=? ORDER BY version DESC LIMIT 50 OFFSET ?', (report_id, (page - 1) * 50))
    return {'total': total, 'page': page, 'items': [dict(r) for r in rows]}


def historical_report(db, report_id: str, version: int) -> dict:
    from .service import fail
    row = db.execute('SELECT * FROM report_history WHERE report_id=? AND version=?', (report_id, version)).fetchone()
    if not row:
        fail('This report revision was not captured', 404)
    return {'report': json.loads(row['data']), 'capture_kind': row['capture_kind'], 'captured_at': row['captured_at']}


def compare_reports(before: dict, after: dict) -> dict:
    a = {s['key']: s for s in before['sections']}
    b = {s['key']: s for s in after['sections']}
    return {'from_version': before['version'], 'to_version': after['version'],
            'sections': [{'key': key, 'before': a.get(key), 'after': b.get(key)}
                         for key in sorted(set(a) | set(b)) if a.get(key) != b.get(key)],
            'sources': snapshot_changes(before['snapshot'], after['snapshot']),
            'acknowledgements': field_changes(before['acknowledgements'], after['acknowledgements']),
            'status': {'before': before['status'], 'after': after['status']},
            'disclaimer': 'Differences describe recorded content only; no medical interpretation is inferred.'}


def make_checkpoint(store, events: list[dict], case_id: str) -> dict:
    if not events or not verify_audit(events) or any(e['scope'] != case_id for e in events):
        raise ValueError('Cannot checkpoint an invalid or mismatched audit chain')
    payload = {'format': 'openautopsyflow-audit-checkpoint-v1', 'case_id': case_id,
               'count': len(events), 'head': events[-1]['hash'], 'created_at': now()}
    signature = store.signer.sign(CHECKPOINT_DOMAIN + canonical(payload).encode())
    return {'payload': payload, 'public_key': store.public_key(), 'signature': base64.b64encode(signature).decode()}


def verify_checkpoint(checkpoint: dict, events: list[dict], trusted_key: str) -> dict:
    """Pin an independently retained key AND checkpoint; validate a later chain."""
    key = trusted_key.strip()
    if not key or checkpoint['public_key'] != key:
        raise ValueError('Checkpoint key differs from the independently trusted key')
    p = checkpoint['payload']
    Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True)).verify(
        base64.b64decode(checkpoint['signature'], validate=True), CHECKPOINT_DOMAIN + canonical(p).encode())
    if p.get('format') != 'openautopsyflow-audit-checkpoint-v1' or type(p.get('count')) is not int or p['count'] < 1:
        raise ValueError('Invalid checkpoint format')
    if len(events) < p['count']:
        raise ValueError('Audit chain was truncated or is older than the retained checkpoint')
    if not verify_audit(events) or any(e['scope'] != p['case_id'] for e in events):
        raise ValueError('Audit chain integrity or scope mismatch')
    if events[p['count'] - 1]['hash'] != p['head']:
        raise ValueError('Audit chain diverges from the independently retained checkpoint')
    return {'checkpoint_verified': True, 'anchored_events': p['count'],
            'unanchored_later_events': len(events) - p['count'],
            'warning': 'Later events have local chain consistency only. This does not establish trusted time '
                       'or medical correctness; retain the checkpoint outside the application.'}


def main() -> None:
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Verify an audit chain against an independently retained checkpoint')
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('audit', type=Path, help='audit.json from a case export')
    parser.add_argument('--trusted-key-file', type=Path, required=True)
    args = parser.parse_args()
    try:
        for path, limit in ((args.checkpoint, 65536), (args.audit, 64 * 1024 * 1024), (args.trusted_key_file, 1024)):
            if path.stat().st_size > limit:
                raise ValueError('Input exceeds verifier size limit')
        print(json.dumps(verify_checkpoint(json.loads(args.checkpoint.read_text()),
                                          json.loads(args.audit.read_text()), args.trusted_key_file.read_text()), indent=2))
    except Exception as exc:
        parser.exit(1, f'Checkpoint verification failed: {type(exc).__name__}\n')


if __name__ == '__main__':
    main()
