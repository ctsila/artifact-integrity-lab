"""Exercise the three coursework checks through the container's HTTP API."""

from argparse import ArgumentParser
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import uuid

from client import BASELINES, TOKENS, request


def example_digest(value):
    return sha256(value.encode('utf-8')).hexdigest()


def sample_baseline(name, digest):
    return {'name': name, 'sha256': digest}


class Run:
    def __init__(self, base_url, output):
        self.base_url = base_url
        self.output = output
        self.log = {'started_utc': datetime.now(timezone.utc).isoformat(), 'base_url': base_url,
                    'results': {}, 'exchanges': []}

    def call(self, label, method, path, tenant=None, payload=None, status=200):
        actual, body = request(self.base_url, method, path, TOKENS.get(tenant), payload)
        self.log['exchanges'].append({'check': label, 'request': {'method': method, 'path': path,
                                      'tenant_credential': tenant, 'json': payload},
                                      'expected_http': status, 'response': {'status': actual, 'json': body}})
        require(actual == status, f'{label}: expected HTTP {status}, received {actual}: {body}')
        return body

    def save(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.log, indent=2) + '\n', encoding='utf-8')


def require(condition, explanation):
    if not condition:
        raise AssertionError(explanation)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--output', type=Path, default=Path('local-evidence/acceptance.json'))
    args = parser.parse_args()
    run = Run(args.url, args.output)
    call = run.call
    digest = example_digest('synthetic camera model v1')
    changed = example_digest('synthetic camera model v2')
    owned = {}
    try:
        call('health', 'GET', '/health')
        call('auth', 'GET', BASELINES, status=401)
        call('auth', 'POST', BASELINES, 'A', {**sample_baseline('spoofed-config', digest), 'tenant_id': 'B'}, 400)
        for tenant in ('A', 'B'):
            initial = call('normal.initial', 'GET', BASELINES, tenant)
            require(initial['used'] == 0, f'Tenant {tenant} needs an empty volume for this run')
            item = call('normal.create', 'POST', BASELINES, tenant, sample_baseline(f'{tenant}-camera-v1', digest), 201)
            require(item['tenant_id'] == tenant and item['sha256'] == digest, 'Wrong owner or digest')
            owned[tenant] = item
            path = f"{BASELINES}/{item['id']}"
            require(call('normal.read', 'GET', path, tenant) == item, 'Baseline changed after create')
            listing = call('normal.list', 'GET', BASELINES, tenant)
            require(listing['baselines'] == [item] and listing['used'] == 1, 'List includes incorrect records')
            reply = call('normal.allow', 'POST', path + '/verify', tenant, {'sha256': digest})
            require(reply == {'decision': 'allow', 'baseline_id': item['id']}, 'Clean candidate was rejected')
            reply = call('normal.reject', 'POST', path + '/verify', tenant, {'sha256': changed}, 422)
            require(reply == {'error': 'digest_mismatch', 'decision': 'reject'}, 'Changed candidate was accepted')
        run.log['results']['normal'] = 'PASS: both tenants created, retrieved and listed their own baseline. Clean digest = 200 allow. Changed digest = 422 reject.'

        path_b = f"{BASELINES}/{owned['B']['id']}"
        before_b = call('isolation.owner_before', 'GET', path_b, 'B')
        foreign = call('isolation.foreign_read', 'GET', path_b, 'A', status=404)
        missing = call('isolation.unknown_read', 'GET', f'{BASELINES}/{uuid.uuid4().hex}', 'A', status=404)
        require(foreign == missing == {'error': 'baseline_not_found'}, 'Foreign target revealed metadata')
        verify = call('isolation.foreign_verify', 'POST', path_b + '/verify', 'A', {'sha256': digest}, 404)
        require(verify == foreign, 'Foreign verify revealed metadata')
        require(call('isolation.owner_after', 'GET', path_b, 'B') == before_b == owned['B'],
                'Original record changed after foreign requests')
        run.log['results']['isolation'] = 'PASS: A received 404 for read and verify. B received 200 before and after, with an identical record.'

        call('limit.a_second', 'POST', BASELINES, 'A', sample_baseline('A-config-v1', digest), 201)
        before_a = call('limit.a_before', 'GET', BASELINES, 'A')
        require(before_a['used'] == before_a['limit'] == 2 and before_a['plan'] == 'Basic', 'Incorrect Basic allowance')
        denial = call('limit.a_third', 'POST', BASELINES, 'A', sample_baseline('A-config-v2', digest), 409)
        require(denial == {'error': 'plan_limit_reached', 'plan': 'Basic', 'limit': 2, 'used': 2},
                'Incorrect limit response')
        require(call('limit.a_after', 'GET', BASELINES, 'A') == before_a, 'Limit rejection changed tenant A data')
        call('limit.b_second', 'POST', BASELINES, 'B', sample_baseline('B-config-v1', digest), 201)
        call('limit.b_third', 'POST', BASELINES, 'B', sample_baseline('B-config-v2', digest), 201)
        final_b = call('limit.b_after', 'GET', BASELINES, 'B')
        require(final_b['used'] == 3 and final_b['limit'] == 5 and final_b['plan'] == 'Pro', 'Incorrect Pro usage')
        require(all(row['tenant_id'] == 'B' for row in final_b['baselines']), 'Pro list leaked foreign data')
        run.log['results']['limit'] = 'PASS: A reached 2/2. A’s third create returned 409 and stayed at 2/2. B’s third create returned 201 and reached 3/5.'
    except Exception as error:
        run.log['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        run.save()
    for result in run.log['results'].values():
        print(result)
    print(f"Recorded {len(run.log['exchanges'])} HTTP exchanges in {args.output}")


if __name__ == '__main__':
    main()
