PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('schema_version', '1');
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
 password_hash TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1, totp BLOB, last_totp INTEGER NOT NULL DEFAULT -1,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 csrf TEXT NOT NULL, expires REAL NOT NULL, last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (key TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS failures_lookup ON login_failures(key,at);
CREATE TABLE IF NOT EXISTS cases (
 id TEXT PRIMARY KEY, case_no TEXT UNIQUE NOT NULL, data TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
 case_id TEXT NOT NULL REFERENCES cases(id), user_id TEXT NOT NULL REFERENCES users(id),
 role TEXT NOT NULL CHECK(role IN ('examiner','reviewer','coordinator','auditor')),
 PRIMARY KEY(case_id,user_id)
);
CREATE TABLE IF NOT EXISTS records (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), kind TEXT NOT NULL,
 label TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(case_id,kind,label)
);
CREATE TABLE IF NOT EXISTS record_history (
 id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id),
 version INTEGER NOT NULL, data TEXT NOT NULL, active INTEGER NOT NULL,
 actor TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL, at TEXT NOT NULL,
 UNIQUE(record_id,version)
);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
 finding_id TEXT REFERENCES records(id), kind TEXT NOT NULL, filename TEXT NOT NULL,
 mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, ciphertext BLOB NOT NULL,
 scan_status TEXT NOT NULL CHECK(scan_status IN ('quarantined','clean')),
 scan_engine TEXT NOT NULL, reviewed_by TEXT REFERENCES users(id), reviewed_at TEXT,
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS custody (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
 specimen_id TEXT NOT NULL REFERENCES records(id), from_custodian TEXT NOT NULL,
 to_custodian TEXT NOT NULL, seal TEXT NOT NULL, purpose TEXT NOT NULL,
 occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, actor TEXT NOT NULL REFERENCES users(id),
 accepted_by TEXT REFERENCES users(id), accepted_at TEXT
);
CREATE TABLE IF NOT EXISTS templates (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL, sections TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(name,version)
);
CREATE TABLE IF NOT EXISTS reports (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), number INTEGER NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('initial','supplementary')),
 parent_id TEXT REFERENCES reports(id), template_id TEXT NOT NULL REFERENCES templates(id),
 status TEXT NOT NULL CHECK(status IN ('draft','in_review','approved','issued')),
 version INTEGER NOT NULL DEFAULT 1, source_revision INTEGER NOT NULL,
 snapshot TEXT NOT NULL, sections TEXT NOT NULL, acknowledgements TEXT NOT NULL DEFAULT '{}',
 author TEXT NOT NULL REFERENCES users(id), last_editor TEXT NOT NULL REFERENCES users(id),
 reviewer TEXT REFERENCES users(id), approved_digest TEXT, approved_at TEXT,
 issued_at TEXT, issued_by TEXT REFERENCES users(id), pdf_ciphertext BLOB, pdf_sha256 TEXT,
 created_at TEXT NOT NULL, UNIQUE(case_id,number)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_draft ON reports(case_id) WHERE status!='issued';
CREATE TABLE IF NOT EXISTS comments (
 id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(id), actor TEXT NOT NULL REFERENCES users(id),
 body TEXT NOT NULL, blocking INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL,
 resolved_by TEXT REFERENCES users(id), resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, seq INTEGER NOT NULL, at TEXT NOT NULL,
 actor TEXT NOT NULL, action TEXT NOT NULL, entity TEXT NOT NULL, details TEXT NOT NULL,
 previous TEXT NOT NULL, hash TEXT NOT NULL, UNIQUE(scope,seq)
);
CREATE INDEX IF NOT EXISTS records_case ON records(case_id,kind,active);
CREATE INDEX IF NOT EXISTS evidence_case ON evidence(case_id);
CREATE INDEX IF NOT EXISTS reports_case ON reports(case_id);
CREATE INDEX IF NOT EXISTS membership_user ON members(user_id);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS history_no_update BEFORE UPDATE ON record_history BEGIN SELECT RAISE(ABORT,'History is append-only'); END;
CREATE TRIGGER IF NOT EXISTS history_no_delete BEFORE DELETE ON record_history BEGIN SELECT RAISE(ABORT,'History is append-only'); END;
CREATE TRIGGER IF NOT EXISTS template_no_update BEFORE UPDATE ON templates BEGIN SELECT RAISE(ABORT,'Templates are versioned'); END;
CREATE TRIGGER IF NOT EXISTS template_no_delete BEFORE DELETE ON templates BEGIN SELECT RAISE(ABORT,'Templates are versioned'); END;
CREATE TRIGGER IF NOT EXISTS issued_no_update BEFORE UPDATE ON reports WHEN OLD.status='issued' BEGIN SELECT RAISE(ABORT,'Issued report is immutable'); END;
CREATE TRIGGER IF NOT EXISTS reports_no_delete BEFORE DELETE ON reports BEGIN SELECT RAISE(ABORT,'Reports cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_replace BEFORE UPDATE OF id,case_id,finding_id,kind,filename,mime,size,sha256,ciphertext,created_by,created_at ON evidence BEGIN SELECT RAISE(ABORT,'Evidence originals cannot be replaced'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence BEGIN SELECT RAISE(ABORT,'Evidence cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS custody_no_rewrite BEFORE UPDATE OF id,case_id,specimen_id,from_custodian,to_custodian,seal,purpose,occurred_at,recorded_at,actor ON custody BEGIN SELECT RAISE(ABORT,'Custody facts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS custody_no_delete BEFORE DELETE ON custody BEGIN SELECT RAISE(ABORT,'Custody cannot be deleted'); END;

CREATE TABLE IF NOT EXISTS case_history (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), revision INTEGER NOT NULL,
 data TEXT NOT NULL, actor TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL, at TEXT NOT NULL,
 UNIQUE(case_id,revision)
);
CREATE TRIGGER IF NOT EXISTS intake_no_update BEFORE UPDATE ON case_history BEGIN SELECT RAISE(ABORT,'Intake history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS intake_no_delete BEFORE DELETE ON case_history BEGIN SELECT RAISE(ABORT,'Intake history is append-only'); END;
CREATE INDEX IF NOT EXISTS cases_updated ON cases(updated_at DESC,id);
