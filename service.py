"""HTTP API for tenant-scoped artifact integrity baselines."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import hmac
import json
import os
import re
import sqlite3
import uuid


@dataclass(frozen=True)
class Tenant:
    id: str
    organisation: str
    plan: str
    allowance: int
    demo_token: str


TENANTS = (
    Tenant('A', 'Neva Brewery Demo', 'Basic', 2, 'lab-neva-token'),
    Tenant('B', 'Northline Beverages Demo', 'Pro', 5, 'lab-northline-token'),
)
BASELINE_PATH = re.compile(r'/v1/baselines/([0-9a-f]{32})(/verify)?')
SHA256_HEX = re.compile(r'[0-9a-f]{64}')
FIELDS = ('id', 'tenant_id', 'name', 'sha256', 'created_at')
MAX_BODY_BYTES = 4096


class ApiError(Exception):
    def __init__(self, status, code, **details):
        self.status = status
        self.payload = {'error': code, **details}


class Store:
    def __init__(self, path, signing_key):
        self.path = path
        self.signing_key = signing_key
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS baselines (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL CHECK (tenant_id IN ('A', 'B')),
                name TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                tag TEXT NOT NULL
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS baselines_tenant ON baselines(tenant_id)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def tag(self, baseline):
        payload = json.dumps([baseline[key] for key in FIELDS], separators=(',', ':'))
        return hmac.new(self.signing_key, payload.encode('utf-8'), sha256).hexdigest()

    def checked_record(self, row):
        baseline = {key: row[key] for key in FIELDS}
        tag = row['tag']
        if not SHA256_HEX.fullmatch(tag) or not hmac.compare_digest(tag, self.tag(baseline)):
            raise ApiError(409, 'baseline_integrity_failed', decision='reject')
        return baseline

    def list(self, tenant):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM baselines WHERE tenant_id = ? ORDER BY created_at, id',
                              (tenant.id,)).fetchall()
        return [self.checked_record(row) for row in rows]

    def get(self, tenant, baseline_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM baselines WHERE tenant_id = ? AND id = ?',
                             (tenant.id, baseline_id)).fetchone()
        if row is None:
            raise ApiError(404, 'baseline_not_found')
        return self.checked_record(row)

    def create(self, tenant, name, digest):
        with self.connect() as db:
            # The write lock covers both the allowance check and the insert.
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('SELECT COUNT(*) FROM baselines WHERE tenant_id = ?',
                               (tenant.id,)).fetchone()[0]
            if count >= tenant.allowance:
                raise ApiError(409, 'plan_limit_reached', plan=tenant.plan,
                               limit=tenant.allowance, used=count)
            baseline = {'id': uuid.uuid4().hex, 'tenant_id': tenant.id, 'name': name,
                        'sha256': digest, 'created_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds')}
            db.execute('''INSERT INTO baselines (id, tenant_id, name, sha256, created_at, tag)
                          VALUES (:id, :tenant_id, :name, :sha256, :created_at, :tag)''',
                       {**baseline, 'tag': self.tag(baseline)})
        return baseline


class ApiHandler(BaseHTTPRequestHandler):
    server_version = 'ArtifactIntegrityLab/1.0'
    sys_version = ''

    def send_json(self, status, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        if status == HTTPStatus.UNAUTHORIZED:
            self.send_header('WWW-Authenticate', 'Bearer')
        self.end_headers()
        self.wfile.write(body)

    def tenant(self):
        auth = self.headers.get_all('Authorization') or []
        if len(auth) == 1:
            scheme, sep, value = auth[0].partition(' ')
            if sep and scheme.lower() == 'bearer':
                for tenant in TENANTS:
                    if hmac.compare_digest(value.encode('utf-8'), tenant.demo_token.encode('utf-8')):
                        return tenant
        raise ApiError(401, 'invalid_token')

    def read_input(self, fields):
        lengths = self.headers.get_all('Content-Length') or []
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,8}', lengths[0]):
            raise ApiError(400, 'invalid_content_length')
        size = int(lengths[0])
        if not 0 < size <= MAX_BODY_BYTES:
            raise ApiError(413, 'invalid_body_size')
        if self.headers.get_content_type() != 'application/json':
            raise ApiError(415, 'expected_json')
        try:
            data = json.loads(self.rfile.read(size))
        except (ValueError, RecursionError):
            raise ApiError(400, 'malformed_json') from None
        if not isinstance(data, dict) or set(data) != fields:
            raise ApiError(400, 'invalid_fields', expected=sorted(fields))
        digest = data['sha256']
        if not isinstance(digest, str) or not SHA256_HEX.fullmatch(digest):
            raise ApiError(400, 'invalid_sha256')
        return data

    def dispatch(self, method):
        path = urlsplit(self.path).path
        if method == 'GET' and path == '/health':
            return 200, {'status': 'ok'}
        tenant = self.tenant()
        store = self.server.store
        if path == '/v1/baselines':
            if method == 'GET':
                baselines = store.list(tenant)
                return 200, {'tenant_id': tenant.id, 'organisation': tenant.organisation,
                             'plan': tenant.plan, 'limit': tenant.allowance, 'used': len(baselines),
                             'baselines': baselines}
            data = self.read_input({'name', 'sha256'})
            name = data['name']
            if not isinstance(name, str) or not 0 < len(name.strip()) <= 80 or not name.isprintable():
                raise ApiError(400, 'invalid_name')
            return 201, store.create(tenant, name.strip(), data['sha256'])
        match = BASELINE_PATH.fullmatch(path)
        if match:
            baseline_id, verify = match.groups()
            if method == 'GET' and not verify:
                return 200, store.get(tenant, baseline_id)
            if method == 'POST' and verify:
                # Authorize before parsing the candidate. Foreign IDs look absent.
                baseline = store.get(tenant, baseline_id)
                candidate = self.read_input({'sha256'})['sha256']
                if not hmac.compare_digest(baseline['sha256'], candidate):
                    raise ApiError(422, 'digest_mismatch', decision='reject')
                return 200, {'decision': 'allow', 'baseline_id': baseline_id}
        raise ApiError(404, 'route_not_found')

    def handle_api(self, method):
        try:
            status, body = self.dispatch(method)
        except ApiError as error:
            status, body = error.status, error.payload
        except sqlite3.Error as error:
            self.log_error('SQLite operation failed: %s', error)
            status, body = 503, {'error': 'storage_unavailable'}
        except (ValueError, TypeError):
            status, body = 400, {'error': 'invalid_request'}
        self.send_json(status, body)

    def do_GET(self):
        self.handle_api('GET')

    def do_POST(self):
        self.handle_api('POST')


def main():
    raw_key = os.environ.get('BASELINE_KEY_HEX', '')
    if not SHA256_HEX.fullmatch(raw_key):
        raise SystemExit('Set BASELINE_KEY_HEX to 32 random bytes in lowercase hex.')
    db_path = os.environ.get('DB_PATH', './integrity.sqlite')
    with ThreadingHTTPServer(('0.0.0.0', 8080), ApiHandler) as server:
        server.store = Store(db_path, bytes.fromhex(raw_key))
        print('Artifact Integrity Lab is listening on port 8080', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
