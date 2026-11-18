"""Check a local file and a changed candidate against an integrity baseline."""

from argparse import ArgumentParser
from hashlib import file_digest, sha256
from pathlib import Path
from time import perf_counter
import json
import os

from client import BASELINES, TOKENS, request


def clean_and_changed(path):
    clean = sha256()
    changed = sha256()
    with path.open('rb') as stream:
        first = stream.read(1)
        if not first:
            raise ValueError('The probe needs a nonempty reference file.')
        clean.update(first)
        changed.update(bytes([first[0] ^ 1]))
        while block := stream.read(1024 * 1024):
            clean.update(block)
            changed.update(block)
    return clean.hexdigest(), changed.hexdigest()


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path, help='Trusted model or configuration artifact')
    parser.add_argument('--candidate', type=Path, help='Changed file. Defaults to a one-bit flip in memory.')
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--tenant', choices=TOKENS, default='A')
    parser.add_argument('--baseline', help='Reuse an existing baseline ID for this reference.')
    parser.add_argument('--name', default='local-integrity-probe')
    parser.add_argument('--output', type=Path, help='Save JSON decisions and round-trip times.')
    args = parser.parse_args()
    token = os.environ.get('INTEGRITY_TOKEN') or TOKENS[args.tenant]
    clean, changed = clean_and_changed(args.file)
    if args.candidate:
        with args.candidate.open('rb') as stream:
            changed = file_digest(stream, 'sha256').hexdigest()
    if changed == clean:
        parser.error('The candidate is identical to the trusted file.')

    baseline_id = args.baseline
    if not baseline_id:
        status, body = request(args.url, 'POST', BASELINES, token, {'name': args.name, 'sha256': clean})
        if status != 201:
            raise SystemExit(f'Baseline registration failed (HTTP {status}): {body}')
        baseline_id = body['id']

    report = {'baseline_id': baseline_id, 'trials': []}
    for trial, digest in [('original', clean), ('changed', changed)]:
        start = perf_counter()
        status, body = request(args.url, 'POST', f'{BASELINES}/{baseline_id}/verify', token,
                               {'sha256': digest})
        report['trials'].append({'trial': trial, 'sha256': digest, 'http': status,
                                 'decision': body.get('decision'), 'error': body.get('error'),
                                 'round_trip_ms': round((perf_counter() - start) * 1000, 3)})
    rendered = json.dumps(report, indent=2) + '\n'
    print(rendered, end='')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    actual = [(trial['http'], trial['decision']) for trial in report['trials']]
    return 0 if actual == [(200, 'allow'), (422, 'reject')] else 1


if __name__ == '__main__':
    raise SystemExit(main())
