"""Transactional business rules. All aggregate writes require a case revision token."""
from __future__ import annotations
import io
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from fastapi import HTTPException
from .documents import render_pdf, signed_bundle
from .rules import traceability
from .store import audit, audit_events, canonical, digest, now, uid, verify_audit

WRITERS = ('examiner', 'coordinator')
CLINICAL = ('examiner', 'reviewer')


def fail(message, status=409):
    raise HTTPException(status, message)


def row_one(db, table, ident):
    # table is an internal constant, never caller-controlled.
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (ident,)).fetchone()
    if row is None:
        fail('Not found or not permitted', 404)
    return dict(row)


def access(db, case_id, user, roles=None):
    member = db.execute('SELECT role FROM members WHERE case_id=? AND user_id=?',
                        (case_id, user['id'])).fetchone()
    if not member:
        fail('Not found or not permitted', 404)
    if roles and member['role'] not in roles:
        fail('Your case role does not permit this action', 403)
    return member['role']


def case_row(db, case_id):
    case = row_one(db, 'cases', case_id)
    case['data'] = json.loads(case['data'])
    return case


def check_revision(case, revision):
    if case['revision'] != revision:
        fail('This case changed in another session. Reload before saving (no changes were written).')


def touch(db, case_id):
    db.execute('UPDATE cases SET revision=revision+1,updated_at=? WHERE id=?', (now(), case_id))


def decode_record(row):
    r = dict(row)
    r['data'] = json.loads(r['data'])
    r['active'] = bool(r['active'])
    return r


def evidence_meta(row):
    return {k: row[k] for k in row.keys() if k != 'ciphertext'}


def snapshot(db, case_id):
    c = case_row(db, case_id)
    return {'case': c['data'], 'case_id': case_id,
            'intake_history': [{**dict(h), 'data': json.loads(h['data'])} for h in db.execute('SELECT * FROM case_history WHERE case_id=? ORDER BY revision', (case_id,))],
            'records': [decode_record(r) for r in db.execute('SELECT * FROM records WHERE case_id=? ORDER BY kind,label,id', (case_id,))],
            'evidence': [evidence_meta(e) for e in db.execute('SELECT * FROM evidence WHERE case_id=? ORDER BY created_at,id', (case_id,))],
            'custody': [dict(r) for r in db.execute('SELECT * FROM custody WHERE case_id=? ORDER BY recorded_at,id', (case_id,))]}


def record_history(db, record, actor, reason):
    db.execute('INSERT INTO record_history VALUES (?,?,?,?,?,?,?,?)',
               (uid(), record['id'], record['version'], canonical(record['data']), int(record['active']),
                actor, reason, now()))


def validate_record_links(db, case_id, kind, data):
    if data.get('specimen_id'):
        specimen = row_one(db, 'records', data['specimen_id'])
        if specimen['case_id'] != case_id or specimen['kind'] != 'specimen' or not specimen['active']:
            fail('The selected specimen must be active and belong to this case', 422)
    if data.get('evidence_id'):
        e = row_one(db, 'evidence', data['evidence_id'])
        if e['case_id'] != case_id:
            fail('The selected evidence must belong to this case', 422)
        if kind == 'lab':
            if e['kind'] != 'lab_result':
                fail('A laboratory request must link to laboratory-result evidence', 422)
            if data['status'] == 'reviewed' and (not e['reviewed_by'] or e['scan_status'] != 'clean'):
                fail('Mark the clean laboratory-result evidence reviewed first', 422)
    if kind == 'lab' and data['status'] in ('received', 'reviewed', 'complete') and not data.get('evidence_id'):
        fail('Received/reviewed/completed lab work requires a linked laboratory-result document', 422)
    if kind == 'lab' and data['status'] == 'complete':
        e = row_one(db, 'evidence', data['evidence_id'])
        if not e['reviewed_by'] or e['scan_status'] != 'clean':
            fail('Completed lab work requires a clean, reviewed result', 422)


def create_case(db, user, payload):
    case_id, timestamp = uid(), now()
    data = payload.model_dump(mode='json')
    db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)',
               (case_id, data['case_no'], canonical(data), 1, timestamp, timestamp))
    db.execute('INSERT INTO members VALUES (?,?,?)', (case_id, user['id'], 'examiner'))
    db.execute('INSERT INTO case_history VALUES (?,?,?,?,?,?,?)',
               (uid(), case_id, 1, canonical(data), user['id'], 'Initial intake', timestamp))
    audit(db, case_id, user['id'], 'case.created', case_id, {'revision': 1, 'data_hash': digest(data)})
    return case_id


def add_record(db, case_id, user, payload):
    access(db, case_id, user, WRITERS)
    c = case_row(db, case_id)
    check_revision(c, payload.revision)
    data = payload.data.model_dump(mode='json')
    validate_record_links(db, case_id, payload.kind, data)
    if payload.kind not in ('task', 'lab', 'specimen') and access(db, case_id, user) != 'examiner':
        fail('Only an assigned examiner can record examination findings', 403)
    label = str(data['number']) if payload.kind == 'injury' else payload.label
    ident, timestamp = uid(), now()
    db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)',
               (ident, case_id, payload.kind, label, canonical(data), 1, 1, timestamp, timestamp))
    record_history(db, {'id': ident, 'version': 1, 'data': data, 'active': True}, user['id'], payload.reason)
    touch(db, case_id)
    audit(db, case_id, user['id'], 'record.created', ident,
          {'kind': payload.kind, 'revision': c['revision']+1, 'data_hash': digest(data)})
    return ident


def update_record(db, case_id, record_id, user, payload):
    access(db, case_id, user, WRITERS)
    record = decode_record(row_one(db, 'records', record_id))
    if record['case_id'] != case_id:
        fail('Not found or not permitted', 404)
    if record['kind'] not in ('task', 'lab', 'specimen') and access(db, case_id, user) != 'examiner':
        fail('Only an assigned examiner can amend findings', 403)
    c = case_row(db, case_id)
    check_revision(c, payload.revision)
    data = payload.data.model_dump(mode='json')
    validate_record_links(db, case_id, record['kind'], data)
    if record['kind'] == 'injury' and data['number'] != record['data']['number']:
        fail('Injury numbers are stable. Retire an erroneous entry with a reason instead of renumbering it.', 422)
    if record['kind'] == 'specimen' and data['custodian'] != record['data']['custodian']:
        fail('Use a custody transfer to change the recorded specimen custodian', 422)
    record.update(data=data, active=payload.active, version=record['version']+1)
    db.execute('UPDATE records SET data=?,version=?,active=?,updated_at=? WHERE id=?',
               (canonical(data), record['version'], int(record['active']), now(), record_id))
    record_history(db, record, user['id'], payload.reason)
    touch(db, case_id)
    audit(db, case_id, user['id'], 'record.amended', record_id,
          {'reason': payload.reason, 'active': payload.active, 'version': record['version'], 'data_hash': digest(data)})


def inspect_upload(raw: bytes, filename: str, kind: str, settings):
    if not raw or len(raw) > settings.max_upload:
        fail('Empty file or upload exceeds the configured limit', 413)
    filename = re.sub(r'[^a-zA-Z0-9._ -]', '_', Path(filename.replace('\\', '/')).name)[:128]
    if not filename.strip('.'):
        fail('Invalid filename', 422)
    extension = Path(filename).suffix.lower()
    if raw.startswith(b'%PDF-') and extension == '.pdf':
        if kind == 'photo':
            fail('Photographs must be JPEG or PNG', 422)
        mime = 'application/pdf'
    elif extension in ('.jpg', '.jpeg', '.png'):
        try:
            with Image.open(io.BytesIO(raw)) as im:
                if im.format not in ('JPEG', 'PNG') or im.width * im.height > 20_000_000:
                    fail('Only bounded JPEG/PNG photographs are supported', 422)
                if (im.format == 'JPEG') != (extension in ('.jpg', '.jpeg')):
                    fail('File extension does not match image content', 422)
                mime = 'image/jpeg' if im.format == 'JPEG' else 'image/png'
                im.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
            raise HTTPException(422, 'Invalid image content') from e
    elif extension == '.txt' and kind != 'photo':
        try:
            raw.decode('utf-8')
        except UnicodeDecodeError as e:
            raise HTTPException(422, 'Text evidence must be UTF-8') from e
        if b'\x00' in raw:
            fail('Binary content is not a text document', 422)
        mime = 'text/plain'
    else:
        fail('Allow-listed formats are PDF, JPEG, PNG and UTF-8 TXT; active HTML/SVG is not accepted', 422)
    return filename, mime


def scan_upload(raw, settings):
    if settings.demo:
        return 'clean', 'SYNTHETIC-DEMO-BYPASS-NOT-A-MALWARE-SCAN'
    if not settings.scanner or not shutil.which(settings.scanner):
        return 'quarantined', 'scanner-not-configured'
    with tempfile.TemporaryDirectory(prefix='oaf-scan-') as folder:
        candidate = Path(folder) / 'evidence'
        candidate.write_bytes(raw)
        candidate.chmod(0o600)
        try:
            result = subprocess.run([settings.scanner, '--no-summary', '--', str(candidate)],
                                    capture_output=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return 'quarantined', 'scanner-error'
        if result.returncode == 0:
            return 'clean', 'clamav-command-success'
        return 'quarantined', 'scanner-detection' if result.returncode == 1 else 'scanner-error'


def add_evidence(db, store, case_id, user, revision, kind, finding_id, filename, mime, raw, scan):
    access(db, case_id, user, WRITERS)
    c = case_row(db, case_id)
    check_revision(c, revision)
    if finding_id:
        record = row_one(db, 'records', finding_id)
        if record['case_id'] != case_id or not record['active']:
            fail('Link must refer to an active finding in this case', 422)
    ident = uid()
    encrypted = store.seal(raw, f'evidence:{case_id}:{ident}')
    db.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (ident, case_id, finding_id, kind, filename, mime, len(raw), digest(raw), encrypted,
                scan[0], scan[1], None, None, user['id'], now()))
    touch(db, case_id)
    audit(db, case_id, user['id'], 'evidence.received', ident,
          {'sha256': digest(raw), 'size': len(raw), 'scan_status': scan[0]})
    return ident


def evidence_bytes(store, evidence):
    if evidence['scan_status'] != 'clean':
        fail('Evidence is quarantined; a configured malware scanner must clear it first', 423)
    try:
        raw = store.unseal(evidence['ciphertext'], f"evidence:{evidence['case_id']}:{evidence['id']}")
    except Exception as e:
        raise HTTPException(409, 'Encrypted evidence failed integrity validation') from e
    if digest(raw) != evidence['sha256']:
        fail('Evidence hash does not match the recorded original')
    return raw


def custody_transfer(db, case_id, user, payload):
    access(db, case_id, user, WRITERS)
    c = case_row(db, case_id)
    check_revision(c, payload.revision)
    specimen = decode_record(row_one(db, 'records', payload.specimen_id))
    if specimen['case_id'] != case_id or specimen['kind'] != 'specimen' or not specimen['active']:
        fail('A transfer requires an active specimen from this case', 422)
    last = db.execute('SELECT * FROM custody WHERE specimen_id=? ORDER BY recorded_at DESC LIMIT 1',
                      (payload.specimen_id,)).fetchone()
    if last and not last['accepted_at']:
        fail('The preceding transfer is awaiting countersignature')
    holder = last['to_custodian'] if last else specimen['data']['custodian']
    if holder and holder != payload.from_custodian:
        fail('From-custodian does not match the last recorded holder', 422)
    when = payload.occurred_at.astimezone(timezone.utc)
    if when > datetime.now(timezone.utc) + timedelta(minutes=5):
        fail('A custody transfer cannot be dated in the future', 422)
    if last and when < datetime.fromisoformat(last['occurred_at']):
        fail('A transfer cannot predate the preceding transfer', 422)
    if payload.from_custodian == payload.to_custodian:
        fail('From-custodian and to-custodian must differ', 422)
    ident = uid()
    db.execute('INSERT INTO custody VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
               (ident, case_id, payload.specimen_id, payload.from_custodian, payload.to_custodian,
                payload.seal, payload.purpose, when.isoformat(), now(), user['id'], None, None))
    touch(db, case_id)
    audit(db, case_id, user['id'], 'custody.transfer_recorded', ident,
          {'specimen_id': payload.specimen_id, 'occurred_at': when.isoformat()})
    return ident


def report_row(db, report_id):
    report = row_one(db, 'reports', report_id)
    for key in ('snapshot', 'sections', 'acknowledgements'):
        report[key] = json.loads(report[key])
    return report


def report_public(report):
    return {k: v for k, v in report.items() if k != 'pdf_ciphertext'}


def report_digest(report):
    return digest({k: report[k] for k in ('id', 'case_id', 'number', 'kind', 'parent_id', 'template_id',
                                          'source_revision', 'snapshot', 'sections', 'acknowledgements',
                                          'author', 'last_editor')})


def required_sections(db, report):
    template = row_one(db, 'templates', report['template_id'])
    return [s['key'] for s in json.loads(template['sections']) if s['required']]


def checks(db, report):
    c = case_row(db, report['case_id'])
    result = traceability(report['snapshot'], report['sections'], report['source_revision'],
                          c['revision'], required_sections(db, report))
    if report['status'] == 'issued':
        result['case_changed_since_issue'] = report['source_revision'] != c['revision']
        result['issues'] = [i for i in result['issues'] if i['code'] != 'stale_snapshot']
    return result


def make_report(db, case_id, user, payload):
    access(db, case_id, user, ('examiner',))
    c = case_row(db, case_id)
    check_revision(c, payload.revision)
    template = row_one(db, 'templates', payload.template_id)
    prior = db.execute("SELECT * FROM reports WHERE case_id=? AND status='issued' ORDER BY number DESC LIMIT 1", (case_id,)).fetchone()
    if prior:
        if payload.kind != 'supplementary' or payload.parent_id != prior['id']:
            fail('After issue, use a supplementary report linked to the latest issued version', 422)
    elif payload.kind != 'initial' or payload.parent_id:
        fail('The first report must be initial with no parent', 422)
    number = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM reports WHERE case_id=?', (case_id,)).fetchone()[0]
    snap, sections = snapshot(db, case_id), []
    for section in json.loads(template['sections']):
        text = ''
        if section['key'] in ('external', 'internal', 'injuries', 'specimens', 'organs'):
            kind = {'injuries':'injury', 'specimens':'specimen', 'organs':'organ'}.get(section['key'], section['key'])
            entries = []
            for r in snap['records']:
                if r['kind'] == kind and r['active']:
                    link = f"[[injury:{r['data']['number']}]]" if kind == 'injury' else f"[[record:{r['id']}]]"
                    measurement = '; '.join(f'{field}: {r["data"][field]}' for field in
                                            ('length_mm', 'width_mm', 'depth_mm', 'weight_g', 'volume_ml')
                                            if r['data'].get(field) is not None)
                    entries.append(f"{r['label']} {link}\n{r['data']['text']}" + ('\n' + measurement if measurement else ''))
            text = '\n\n'.join(entries)
        sections.append({'key': section['key'], 'title': section['title'], 'text': text})
    ident = uid()
    db.execute('INSERT INTO reports(id,case_id,number,kind,parent_id,template_id,status,source_revision,'
               'snapshot,sections,author,last_editor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (ident, case_id, number, payload.kind, payload.parent_id, payload.template_id, 'draft',
                c['revision'], canonical(snap), canonical(sections), user['id'], user['id'], now()))
    audit(db, case_id, user['id'], 'report.draft_created', ident,
          {'number': number, 'kind': payload.kind, 'source_revision': c['revision']})
    return ident


def version_check(report, version):
    if report['version'] != version:
        fail('Report changed in another session. Reload before saving.')


def edit_report(db, report, user, payload):
    access(db, report['case_id'], user, ('examiner',))
    version_check(report, payload.version)
    if report['status'] != 'draft':
        fail('Only drafts can be edited. Return this report to draft first.')
    sections = [s.model_dump(mode='json') for s in payload.sections]
    template = json.loads(row_one(db, 'templates', report['template_id'])['sections'])
    if [(s['key'], s['title']) for s in sections] != [(s['key'], s['title']) for s in template]:
        fail('Sections must preserve the selected template keys, titles and order', 422)
    db.execute("UPDATE reports SET sections=?,version=version+1,last_editor=?,acknowledgements='{}' WHERE id=?",
               (canonical(sections), user['id'], report['id']))
    audit(db, report['case_id'], user['id'], 'report.edited', report['id'], {'sections_hash': digest(sections), 'report_version': report['version']+1})


def transition(db, store, report, user, action, payload):
    from .review import enforce_review
    case_id, ident = report['case_id'], report['id']
    role = access(db, case_id, user)
    version_check(report, payload.version)
    status = report['status']
    if status == 'issued':
        fail('Issued versions are immutable. Create a supplementary report instead.')
    c = case_row(db, case_id)
    if action == 'refresh':
        if role != 'examiner' or status != 'draft':
            fail('An examiner can refresh a draft only', 403)
        fresh = snapshot(db, case_id)
        old = report['snapshot']
        diff = {'old_revision': report['source_revision'], 'new_revision': c['revision'],
                'old_snapshot_hash': digest(old), 'new_snapshot_hash': digest(fresh), 'report_version': report['version']+1}
        db.execute("UPDATE reports SET snapshot=?,source_revision=?,version=version+1,"
                   "acknowledgements='{}',last_editor=? WHERE id=?",
                   (canonical(fresh), c['revision'], user['id'], ident))
        audit(db, case_id, user['id'], 'report.snapshot_refreshed', ident, diff)
        return diff
    if action == 'return':
        if role not in CLINICAL or status not in ('in_review', 'approved'):
            fail('Only examiner/reviewer can return an unissued reviewed report to draft', 403)
        db.execute("UPDATE reports SET status='draft',reviewer=NULL,approved_digest=NULL,approved_at=NULL,"
                   "acknowledgements='{}',version=version+1 WHERE id=?", (ident,))
        audit(db, case_id, user['id'], 'report.returned_to_draft', ident, {'report_version': report['version']+1})
        return {}
    result = checks(db, report)
    blockers = [i for i in result['issues'] if i['severity'] == 'blocker']
    if blockers:
        raise HTTPException(409, {'message': 'Resolve structural/workflow blockers first', 'issues': blockers})
    if action == 'submit':
        if role != 'examiner' or status != 'draft':
            fail('An examiner can submit a draft only', 403)
        acks = payload.acknowledgements
        warnings = [i for i in result['issues'] if i['severity'] == 'warning']
        if any(len(acks.get(i['id'], '').strip()) < 10 or len(acks.get(i['id'], '')) > 2000 for i in warnings):
            raise HTTPException(409, {'message': 'Every review prompt needs a specific rationale (10-2000 characters)', 'issues': warnings})
        valid_acks = {i['id']: acks[i['id']].strip() for i in warnings}
        db.execute("UPDATE reports SET status='in_review',acknowledgements=?,version=version+1 WHERE id=?",
                   (canonical(valid_acks), ident))
    elif action == 'approve':
        if role != 'reviewer' or status != 'in_review':
            fail('An assigned reviewer can approve an in-review report only', 403)
        contributor = db.execute("SELECT 1 FROM audit WHERE scope=? AND entity=? AND actor=? AND action IN ('report.edited','report.snapshot_refreshed') LIMIT 1", (case_id, ident, user['id'])).fetchone()
        if contributor or user['id'] in (report['author'], report['last_editor']):
            fail('Author and last editor cannot approve their own report', 403)
        unresolved = db.execute('SELECT COUNT(*) FROM comments WHERE report_id=? AND blocking=1 AND resolved_at IS NULL', (ident,)).fetchone()[0]
        if unresolved:
            fail('Resolve outstanding blocking reviewer comments first')
        if any(i['id'] not in report['acknowledgements'] for i in result['issues'] if i['severity'] == 'warning'):
            fail('Review prompts changed; return to draft and re-acknowledge')
        enforce_review(db, report, user['id'])
        db.execute("UPDATE reports SET status='approved',reviewer=?,approved_at=?,approved_digest=?,version=version+1 WHERE id=?",
                   (user['id'], now(), report_digest(report), ident))
    elif action == 'issue':
        if role not in WRITERS + ('reviewer',) or status != 'approved':
            fail('Only an approved report can be issued by assigned case staff', 403)
        if any(i['id'] not in report['acknowledgements'] for i in result['issues'] if i['severity'] == 'warning'):
            fail('Review prompts changed since approval; return to draft and re-review')
        if report_digest(report) != report['approved_digest']:
            fail('Approved content digest changed; issue refused')
        if db.execute('SELECT COUNT(*) FROM comments WHERE report_id=? AND blocking=1 AND resolved_at IS NULL', (ident,)).fetchone()[0]:
            fail('A blocking comment is unresolved')
        enforce_review(db, report, report['reviewer'])
        report.update(status='issued', issued_at=now(), issued_by=user['id'])
        pdf = render_pdf(report, store.settings)
        encrypted = store.seal(pdf, f'report:{case_id}:{ident}')
        db.execute("UPDATE reports SET status='issued',issued_at=?,issued_by=?,pdf_ciphertext=?,pdf_sha256=?,version=version+1 WHERE id=?",
                   (report['issued_at'], user['id'], encrypted, digest(pdf), ident))
    else:
        fail('Unknown report action', 404)
    audit(db, case_id, user['id'], 'report.' + action, ident,
          {'from_status': status, 'source_revision': report['source_revision'], 'report_version': report['version']+1})
    return {}


def export_case(db, store, case_id, user, include_evidence):
    access(db, case_id, user, CLINICAL)
    # The audit record is part of the export snapshot, not added after it.
    audit(db, case_id, user['id'], 'case.exported', case_id, {'include_evidence': include_evidence})
    events = audit_events(db, case_id)
    if not verify_audit(events):
        fail('Audit chain verification failed; export refused')
    files, total = {}, 0
    def append(path, blob):
        nonlocal total
        total += len(blob)
        if total > 128 * 1024 * 1024:
            fail('Export exceeds the 128 MiB single-node bundle limit', 413)
        files[path] = blob
    append('case.json', canonical(snapshot(db, case_id)).encode())
    append('audit.json', canonical(events).encode())
    history = [{**dict(h), 'data': json.loads(h['data'])} for h in db.execute(
        'SELECT h.* FROM record_history h JOIN records r ON h.record_id=r.id '
        'WHERE r.case_id=? ORDER BY h.record_id,h.version', (case_id,))]
    append('record-history.json', canonical(history).encode())
    discussion = [dict(c) for c in db.execute(
        'SELECT c.*,u.name FROM comments c JOIN reports r ON c.report_id=r.id '
        'JOIN users u ON c.actor=u.id WHERE r.case_id=? ORDER BY c.at,c.id', (case_id,))]
    append('review-discussion.json', canonical(discussion).encode())
    templates = [{**dict(t), 'sections': json.loads(t['sections'])} for t in db.execute(
        'SELECT DISTINCT t.* FROM templates t JOIN reports r ON r.template_id=t.id '
        'WHERE r.case_id=? ORDER BY t.name,t.version', (case_id,))]
    append('templates.json', canonical(templates).encode())
    assignments = [dict(m) for m in db.execute(
        'SELECT m.user_id,m.role,u.name,u.username,u.active FROM members m JOIN users u ON m.user_id=u.id '
        'WHERE m.case_id=? ORDER BY m.user_id', (case_id,))]
    append('assignments.json', canonical(assignments).encode())
    revisions = [{**dict(h), 'data': json.loads(h['data'])} for h in db.execute(
        'SELECT h.* FROM report_history h JOIN reports r ON r.id=h.report_id '
        'WHERE r.case_id=? ORDER BY h.report_id,h.version', (case_id,))]
    append('report-history.json', canonical(revisions).encode())
    receipts = [dict(x) for x in db.execute(
        'SELECT x.* FROM review_receipts x JOIN reports r ON r.id=x.report_id '
        'WHERE r.case_id=? ORDER BY x.created_at,x.id', (case_id,))]
    append('review-receipts.json', canonical(receipts).encode())
    for row in db.execute('SELECT * FROM reports WHERE case_id=? ORDER BY number', (case_id,)):
        report = report_row(db, row['id'])
        append(f"reports/{report['id']}.json", canonical(report_public(report)).encode())
        if report['status'] == 'issued':
            pdf = store.unseal(report['pdf_ciphertext'], f"report:{case_id}:{report['id']}")
            if digest(pdf) != report['pdf_sha256']:
                fail('Issued PDF integrity failure')
            append(f"reports/{report['id']}.pdf", pdf)
    if include_evidence:
        for row in db.execute("SELECT * FROM evidence WHERE case_id=? AND scan_status='clean'", (case_id,)):
            append(f"evidence/{row['id']}/{row['filename']}", evidence_bytes(store, row))
    # The incremental limit bounds accumulation; larger cases need streaming export.
    return signed_bundle(store, files, {'case_id': case_id, 'created_at': now(),
                                       'audit_head': events[-1]['hash'], 'audit_count': len(events),
                                       'contains_sensitive_case_data': True,
                                       'quarantined_originals_excluded': True,
                                       'synthetic_demo': store.settings.demo})
