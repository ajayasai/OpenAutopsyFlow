"""Single-node, transactional store. No distributed/multi-tenant claims."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value) -> str:
    data = value if isinstance(value, bytes) else canonical(value).encode()
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    key: bytes
    demo: bool = False
    secure_cookie: bool = True
    hosts: tuple[str, ...] = ('localhost', '127.0.0.1')
    origins: tuple[str, ...] = ('https://localhost',)
    max_upload: int = 12 * 1024 * 1024
    session_hours: int = 8
    idle_minutes: int = 30
    pdf_font: str = ''
    scanner: str = ''

    def __post_init__(self):
        if len(self.key) != 32:
            raise ValueError('OAF_MASTER_KEY must decode to 32 bytes')
        if not self.demo and not self.secure_cookie:
            raise ValueError('Secure cookies are mandatory outside synthetic demo mode')
        if '*' in self.hosts or '*' in self.origins:
            raise ValueError('Wildcard hosts/origins are not permitted')
        if not self.demo and any(not x.startswith('https://') for x in self.origins):
            raise ValueError('Production origins must use HTTPS')

    @classmethod
    def from_env(cls):
        data = Path(os.environ.get('OAF_DATA_DIR', './data')).resolve()
        demo = os.environ.get('OAF_DEMO', '0') == '1'
        key = os.environ.get('OAF_MASTER_KEY', '')
        if not key and demo:
            data.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = data / '.demo-key'
            if not path.exists():
                try:
                    with path.open('xb') as f:
                        os.chmod(path, 0o600)
                        f.write(secrets.token_bytes(32))
                except FileExistsError:
                    pass
            raw = path.read_bytes()
        elif key:
            raw = base64.b64decode(key, validate=True)
        else:
            raise ValueError('Set OAF_MASTER_KEY. Demo: OAF_DEMO=1; never use demo for real casework.')
        return cls(data, raw, demo, not demo,
                   tuple(os.environ.get('OAF_HOSTS', 'localhost,127.0.0.1').split(',')),
                   tuple(os.environ.get('OAF_ORIGINS', 'http://127.0.0.1:8000,http://localhost:8000'
                         if demo else 'https://localhost').split(',')),
                   pdf_font=os.environ.get('OAF_PDF_FONT', ''),
                   scanner=os.environ.get('OAF_SCANNER', ''))


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = settings.data_dir / 'casework.sqlite3'
        with closing(self.connect()) as db:
            from .migrations import initialize
            marker = digest(HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                                info=b'oaf/key-check/v1').derive(settings.key))
            initialize(db, marker, Path(__file__).with_name('schema.sql').read_text())
            db.execute('PRAGMA journal_mode=WAL')
        os.chmod(self.path, 0o600)
        signing_seed = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                            info=b'oaf/bundle-signing/v1').derive(settings.key)
        self.signer = Ed25519PrivateKey.from_private_bytes(signing_seed)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=15000')
        db.create_function('oaf_casefold', 1, lambda value: str(value or '').casefold(), deterministic=True)
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def read(self):
        db = self.connect()
        try:
            db.execute('BEGIN')
            yield db
        finally:
            db.rollback()
            db.close()

    def seal(self, data: bytes, context: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.settings.key).encrypt(nonce, data, context.encode())

    def unseal(self, data: bytes, context: str) -> bytes:
        return AESGCM(self.settings.key).decrypt(data[:12], data[12:], context.encode())

    def public_key(self) -> str:
        raw = self.signer.public_key().public_bytes(serialization.Encoding.Raw,
                                                   serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode()


def audit(db, scope: str, actor: str, action: str, entity: str = '', details=None):
    last = db.execute('SELECT seq,hash FROM audit WHERE scope=? ORDER BY seq DESC LIMIT 1',
                      (scope,)).fetchone()
    event = dict(id=uid(), scope=scope, seq=last['seq'] + 1 if last else 1, at=now(),
                 actor=actor, action=action, entity=entity, details=details or {},
                 previous=last['hash'] if last else '0' * 64)
    event['hash'] = digest(event)
    db.execute('INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?,?)',
               (event['id'], scope, event['seq'], event['at'], actor, action, entity,
                canonical(event['details']), event['previous'], event['hash']))
    return event


def audit_events(db, scope):
    events = []
    for row in db.execute('SELECT * FROM audit WHERE scope=? ORDER BY seq', (scope,)):
        e = dict(row)
        e['details'] = json.loads(e['details'])
        events.append(e)
    return events


def verify_audit(events) -> bool:
    previous = '0' * 64
    for seq, item in enumerate(events, 1):
        item = dict(item)
        claimed = item.pop('hash', None)
        if item.get('seq') != seq or item.get('previous') != previous or digest(item) != claimed:
            return False
        previous = claimed
    return True
