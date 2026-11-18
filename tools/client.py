"""Small HTTP client for the local demo and its acceptance checks."""

from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json


TOKENS = {'A': 'lab-neva-token', 'B': 'lab-northline-token'}
BASELINES = '/v1/baselines'


def request(base_url, method, path, token=None, payload=None):
    headers = {'Accept': 'application/json'}
    data = None
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(payload).encode('utf-8')
    req = Request(base_url.rstrip('/') + path, data=data, headers=headers, method=method)
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)
