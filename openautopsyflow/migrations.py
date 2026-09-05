"""Transactional, checksummed additive migrations; authenticate storage before DDL.

Back up a store before upgrading. A migrated legacy report contributes only its
current version; earlier drafts are not reconstructed or invented.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

SCHEMA_VERSION = 2
REPORT_COLUMNS = (
    'id', 'case_id', 'number', 'kind', 'parent_id', 'template_id', 'status', 'version',
    'source_revision', 'snapshot', 'sections', 'acknowledgements', 'author',
    'last_editor', 'reviewer', 'approved_digest', 'approved_at', 'issued_at',
    'issued_by', 'pdf_sha256', 'created_at',
)


def _report_json(prefix: str = '') -> str:
    pairs = []
    for name in REPORT_COLUMNS:
        value = prefix + name
        if name in ('snapshot', 'sections', 'acknowledgements'):
            value = f'json({value})'
        pairs.extend((f"'{name}'", value))
    return 'json_object(' + ','.join(pairs) + ')'


REVIEW_HISTORY_SQL = f"""
CREATE TABLE report_history (
 report_id TEXT NOT NULL REFERENCES reports(id), version INTEGER NOT NULL,
 data TEXT NOT NULL, capture_kind TEXT NOT NULL CHECK(capture_kind IN ('legacy_baseline','live')),
 captured_at TEXT NOT NULL, PRIMARY KEY(report_id,version)
);
INSERT INTO report_history SELECT id,version,{_report_json()},'legacy_baseline',
 strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM reports;
CREATE TRIGGER report_history_insert AFTER INSERT ON reports BEGIN
 INSERT INTO report_history VALUES(NEW.id,NEW.version,{_report_json('NEW.')},'live',
 strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;
CREATE TRIGGER report_history_update AFTER UPDATE ON reports BEGIN
 INSERT INTO report_history VALUES(NEW.id,NEW.version,{_report_json('NEW.')},'live',
 strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;
CREATE TRIGGER report_history_no_update BEFORE UPDATE ON report_history BEGIN
 SELECT RAISE(ABORT,'Report history is append-only');
END;
CREATE TRIGGER report_history_no_delete BEFORE DELETE ON report_history BEGIN
 SELECT RAISE(ABORT,'Report history is append-only');
END;
CREATE TABLE review_receipts (
 id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(id),
 report_version INTEGER NOT NULL, basis_digest TEXT NOT NULL,
 evidence_id TEXT NOT NULL REFERENCES evidence(id), evidence_sha256 TEXT NOT NULL,
 reviewer_id TEXT NOT NULL REFERENCES users(id), statement TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(report_id,report_version,evidence_id,reviewer_id)
);
CREATE INDEX review_receipts_report ON review_receipts(report_id,report_version,reviewer_id);
CREATE TRIGGER review_receipts_no_update BEFORE UPDATE ON review_receipts BEGIN
 SELECT RAISE(ABORT,'Review receipts are append-only');
END;
CREATE TRIGGER review_receipts_no_delete BEFORE DELETE ON review_receipts BEGIN
 SELECT RAISE(ABORT,'Review receipts are append-only');
END;
"""
MIGRATIONS = {2: REVIEW_HISTORY_SQL}


def statements(script: str):
    """Split trusted checked-in SQL without executescript's implicit commit."""
    buffer = ''
    for character in script:
        buffer += character
        if character == ';' and sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ''
    if buffer.strip():
        raise ValueError('Incomplete checked-in migration SQL')


def initialize(db: sqlite3.Connection, marker: str, initial_schema: str) -> None:
    """All schema/data changes commit together or roll back, including bootstrap."""
    db.execute('BEGIN IMMEDIATE')
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables:
            if 'meta' not in tables:
                raise ValueError('Unrecognized database; refusing to initialize over existing tables')
            meta = dict(db.execute('SELECT key,value FROM meta'))
            if meta.get('key_check') != marker:
                raise ValueError('Wrong master key or missing key marker; refusing startup before schema changes')
            version = int(meta.get('schema_version', '0'))
            if version < 1 or version > SCHEMA_VERSION:
                raise ValueError('Unsupported schema version; use a compatible application and reviewed migration')
        else:
            for statement in statements(initial_schema):
                db.execute(statement)
            db.execute("INSERT INTO meta VALUES ('key_check',?)", (marker,))
            version = 1
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations '
                   '(version INTEGER PRIMARY KEY,sha256 TEXT NOT NULL,applied_at TEXT NOT NULL)')
        applied = dict(db.execute('SELECT version,sha256 FROM schema_migrations'))
        for target in range(2, version + 1):
            expected = hashlib.sha256(MIGRATIONS[target].encode()).hexdigest()
            if applied.get(target) != expected:
                raise ValueError('Applied migration checksum mismatch; refusing startup')
        if any(target > version or target not in MIGRATIONS for target in applied):
            raise ValueError('Inconsistent migration history; refusing startup')
        for target in range(version + 1, SCHEMA_VERSION + 1):
            script = MIGRATIONS[target]
            for statement in statements(script):
                db.execute(statement)
            db.execute('INSERT INTO schema_migrations VALUES (?,?,?)', (
                target, hashlib.sha256(script.encode()).hexdigest(),
                datetime.now(timezone.utc).isoformat()))
            db.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(target),))
        db.commit()
    except BaseException:
        db.rollback()
        raise
