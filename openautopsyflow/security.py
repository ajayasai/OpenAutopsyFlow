"""Local authentication, revocable sessions, CSRF, optional TOTP and bounded request bodies."""
from __future__ import annotations
import base64
import hashlib
import hmac
import secrets
import struct
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from .store import audit, now, uid

PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(24))


def password_ok(encoded: str, candidate: str) -> bool:
    try:
        return PASSWORDS.verify(encoded, candidate)
    except (VerificationError, InvalidHashError):
        return False


def create_user(db, username, name, password, admin=False):
    if len(password) < 14 or len(password) > 256:
        raise ValueError('Passwords must be 14 to 256 characters')
    ident = uid()
    db.execute('INSERT INTO users(id,username,name,password_hash,admin,created_at) VALUES (?,?,?,?,?,?)',
               (ident, username, name, PASSWORDS.hash(password), int(admin), now()))
    audit(db, 'system', ident, 'user.created', ident, {'admin': bool(admin)})
    return ident


def totp_code(secret: bytes, counter: int) -> str:
    msg = struct.pack('>Q', counter)
    raw = hmac.new(secret, msg, hashlib.sha1).digest()
    offset = raw[-1] & 15
    value = (struct.unpack('>I', raw[offset:offset + 4])[0] & 0x7fffffff) % 1_000_000
    return f'{value:06d}'


def verify_totp(secret, code, last, timestamp=None):
    counter = int((time.time() if timestamp is None else timestamp) // 30)
    if not code.isdigit() or len(code) != 6:
        return None
    for candidate in (counter, counter - 1, counter + 1):
        if candidate > last and hmac.compare_digest(totp_code(secret, candidate), code):
            return candidate
    return None


def login(store, username, password, otp, ip):
    timestamp = time.time()
    keys = [hashlib.sha256(f'{prefix}:{value}'.encode()).hexdigest()
            for prefix, value in [('account', username.casefold()), ('ip', ip)]]
    # Failed attempts must commit even when returning an authentication error.
    result = None
    with store.transaction() as db:
        db.execute('DELETE FROM login_failures WHERE at<?', (timestamp - 900,))
        counts = [db.execute('SELECT COUNT(*) FROM login_failures WHERE key=?', (k,)).fetchone()[0]
                  for k in keys]
        if counts[0] >= 8 or counts[1] >= 40:
            raise HTTPException(429, 'Too many attempts. Try again after the 15-minute window.',
                                headers={'Retry-After': '900'})
        user = db.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE', (username,)).fetchone()
        valid = password_ok(user['password_hash'] if user else DUMMY_HASH, password)
        counter = None
        if user and user['totp']:
            secret = store.unseal(user['totp'], f"totp:{user['id']}")
            counter = verify_totp(secret, otp, user['last_totp'], timestamp)
            valid = valid and counter is not None
        if not user or not user['active'] or not valid:
            db.executemany('INSERT INTO login_failures VALUES (?,?)', [(k, timestamp) for k in keys])
            audit(db, 'system', user['id'] if user else 'unknown', 'login.failed', details={'key': keys[0]})
        else:
            db.execute('DELETE FROM login_failures WHERE key=?', (keys[0],))
            db.execute('DELETE FROM sessions WHERE expires<? OR last_seen<?',
                       (timestamp, timestamp - store.settings.idle_minutes * 60))
            if counter is not None:
                db.execute('UPDATE users SET last_totp=? WHERE id=?', (counter, user['id']))
            if PASSWORDS.check_needs_rehash(user['password_hash']):
                db.execute('UPDATE users SET password_hash=? WHERE id=?', (PASSWORDS.hash(password), user['id']))
            token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), user['id'], csrf,
                        timestamp + store.settings.session_hours * 3600, timestamp))
            audit(db, 'system', user['id'], 'login.success')
            result = token, csrf, public_user(user)
    if result is None:
        raise HTTPException(401, 'Invalid credentials or verification code')
    return result


def public_user(user):
    return {k: user[k] for k in ('id', 'username', 'name', 'admin', 'active')}


def authenticate(request: Request):
    store = request.app.state.store
    token = request.cookies.get('oaf_session', '')
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    timestamp = time.time()
    with store.transaction() as db:
        row = db.execute('SELECT u.*,s.csrf,s.expires,s.last_seen FROM sessions s JOIN users u '
                         'ON s.user_id=u.id WHERE s.token_hash=?', (token_hash,)).fetchone()
        if not row or not row['active'] or row['expires'] < timestamp or \
                row['last_seen'] < timestamp - store.settings.idle_minutes * 60:
            raise HTTPException(401, 'Sign in to continue')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            csrf = request.headers.get('x-csrf-token', '')
            if not hmac.compare_digest(csrf, row['csrf']):
                raise HTTPException(403, 'CSRF validation failed')
        db.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?', (timestamp, token_hash))
        user = public_user(row)
        user['csrf'] = row['csrf']
        user['token_hash'] = token_hash
        return user


class RequestGuard:
    """Buffer at most max_body bytes, also enforcing the limit on chunked bodies.

    Deployed reverse proxies must additionally limit request rate/concurrency.
    No client IP headers are trusted here; configure the ASGI proxy allow-list explicitly.
    """
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        origin = headers.get(b'origin', b'').decode('latin1')
        method = scope['method']
        if method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin not in self.settings.origins:
            return await JSONResponse({'detail': 'Origin not permitted'}, 403)(scope, receive, send)
        if method not in ('GET', 'HEAD', 'OPTIONS'):
            body = bytearray()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                body.extend(message.get('body', b''))
                if len(body) > self.settings.max_upload + 65536:
                    return await JSONResponse({'detail': 'Request too large'}, 413)(scope, receive, send)
                if not message.get('more_body', False):
                    break
            delivered = False
            original_receive = receive

            async def limited_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await original_receive()

            receive = limited_receive

        async def secured_send(message):
            if message['type'] == 'http.response.start':
                additions = [(b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'),
                             (b'referrer-policy', b'no-referrer'), (b'cache-control', b'no-store'),
                             (b'permissions-policy', b'camera=(), microphone=(), geolocation=()'),
                             (b'content-security-policy', b"default-src 'self'; script-src 'self'; "
                              b"style-src 'self'; img-src 'self'; connect-src 'self'; "
                              b"object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]
                if self.settings.secure_cookie:
                    additions.append((b'strict-transport-security', b'max-age=31536000'))
                message = {**message, 'headers': list(message.get('headers', [])) + additions}
            await send(message)
        await self.app(scope, receive, secured_send)
