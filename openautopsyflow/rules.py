"""Deterministic structural checks and human review prompts, never medical verdicts."""
import re
from datetime import date
from .store import digest

TOKEN = re.compile(r'\[\[(injury|record|evidence):([^\]\s]+)\]\]')
NATURAL_INJURY = re.compile(r'\binjury\s*(?:no\.?\s*)?#?\s*(\d+)\b', re.I)


def traceability(snapshot, sections, source_revision, current_revision, required_keys=()):
    records = {r['id']: r for r in snapshot['records'] if r['active']}
    injuries = {str(r['data']['number']): r for r in records.values() if r['kind'] == 'injury'}
    evidence = {e['id']: e for e in snapshot['evidence']}
    issues, seen = [], set()

    def add(code, entity, message, severity='warning'):
        key = f'{code}:{entity}'
        if key not in seen:
            # Prompt identity changes on every source revision; old acknowledgements cannot silently carry forward.
            issues.append({'id': digest({'key': key, 'revision': source_revision})[:24],
                           'code': code, 'entity': entity, 'severity': severity, 'message': message})
            seen.add(key)

    if source_revision != current_revision:
        add('stale_snapshot', 'case', 'Case records changed after this snapshot. Refresh and re-review the draft.', 'blocker')
    links = []
    for section in sections:
        tokens = set(TOKEN.findall(section['text']))
        tokens |= {('injury', n) for n in NATURAL_INJURY.findall(section['text'])}
        for kind, ident in sorted(tokens):
            target = {'injury': injuries, 'record': records, 'evidence': evidence}[kind].get(ident)
            links.append({'section': section['key'], 'kind': kind, 'target': ident,
                          'resolved': target is not None})
            if target is None:
                add('missing_reference', f'{kind}:{ident}',
                    f'{kind.title()} {ident} is referenced but no active corresponding record exists.', 'blocker')
            elif kind == 'evidence' and target['scan_status'] != 'clean':
                add('quarantined_reference', ident, 'Referenced evidence is still quarantined.', 'blocker')
        if section['key'] in required_keys and not section['text'].strip():
            add('required_section', section['key'], f"Required section '{section['title']}' is empty.", 'blocker')
    for e in evidence.values():
        if e['kind'] == 'lab_result' and not e['reviewed_by']:
            add('unreviewed_lab_result', e['id'], f"Laboratory result '{e['filename']}' has not been marked reviewed.")
        if e['scan_status'] != 'clean':
            add('quarantined_evidence', e['id'], f"'{e['filename']}' has not passed malware screening.")
    for r in records.values():
        d = r['data']
        if r['kind'] in ('lab', 'task') and d['status'] not in ('reviewed', 'complete', 'cancelled'):
            add('pending_work', r['id'], f"Pending {r['kind']}: {r['label']} ({d['status']}).")
        if r['kind'] == 'injury' and not any(e['finding_id'] == r['id'] for e in evidence.values()):
            add('injury_without_evidence', r['id'], f"Injury {d['number']} has no linked photograph or document; review whether one is needed.")
        if r['kind'] in ('lab', 'task') and d.get('due_date') and d['due_date'] < date.today().isoformat() \
                and d['status'] not in ('complete', 'reviewed', 'cancelled'):
            add('overdue', r['id'], f"'{r['label']}' is past its recorded due date.")
    return {'issues': issues, 'links': links,
            'disclaimer': 'Checks address recorded links and workflow only. They do not validate a medical opinion.'}
