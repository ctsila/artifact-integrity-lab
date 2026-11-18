# Artifact Integrity Lab

This SaaS prototype lets independent manufacturers register trusted SHA-256 digests for inspection model and configuration releases. Each organisation can check whether a candidate matches its own baseline. Cloud Computing Practical Works 5–6, ITMO University.

- [Practical Work 5 design brief](docs/DESIGN.md)
- [Practical Work 6 results and evidence](docs/RESULTS.md)

## Build and run locally

Use Docker Engine and Python 3.11 or later on the local machine or Linux VM. The application and the test clients use the Python standard library.

```sh
git clone https://github.com/ctsila/artifact-integrity-lab.git
cd artifact-integrity-lab
docker pull python:3.12-slim
docker build --pull=false --network=none -t artifact-integrity-lab:1 .
python3 -c 'import secrets; print("BASELINE_KEY_HEX=" + secrets.token_hex(32))' > .env
docker run --detach --name artifact-integrity-lab --env-file .env \
  --publish 127.0.0.1:8080:8080 \
  --mount source=integrity-demo,target=/data artifact-integrity-lab:1
curl -fsS http://127.0.0.1:8080/health
```

Use an existing local `python:3.12-slim` image for an offline build. The first start initialises an empty SQLite database in the named volume. The two demo organisations and plans are preconfigured. Keep the same `.env` when reusing a volume so the original records can still be verified.

| Tenant | Demo organisation | Plan | Baselines | Demo bearer token |
| --- | --- | --- | ---: | --- |
| A | Neva Brewery Demo | Basic | 2 | `lab-neva-token` |
| B | Northline Beverages Demo | Pro | 5 | `lab-northline-token` |

The credentials in this table are public demo credentials. The commands bind the service to localhost.

## Run the acceptance checks

Run these against a fresh volume before creating baselines manually. The script creates synthetic baselines for both tenants and logs requests, HTTP statuses and response bodies. It exits with an error if an observed result differs from the expected result.

```sh
python3 tools/acceptance.py --output local-evidence/acceptance.json
```

`docker rm -f artifact-integrity-lab` stops and removes the demo container. To repeat the checks, start the same image with a different named volume, such as `integrity-demo-2`.

## Make requests manually

```sh
API=http://127.0.0.1:8080
DIGEST=$(python3 -c 'from pathlib import Path; import hashlib; print(hashlib.sha256(Path("fixtures/camera-config.json").read_bytes()).hexdigest())')
curl -sSi -H 'Authorization: Bearer lab-neva-token' -H 'Content-Type: application/json' \
  -d "{\"name\":\"demo-camera-config\",\"sha256\":\"$DIGEST\"}" "$API/v1/baselines"
curl -sSi -H 'Authorization: Bearer lab-neva-token' "$API/v1/baselines"
curl -sSi -H 'Authorization: Bearer lab-northline-token' "$API/v1/baselines"
```

Copy the `id` returned from `POST` into `ID` to retrieve or verify the record as its owner. Using the other organisation's token against that ID returns `404 baseline_not_found`.

```sh
ID='<id returned by POST>'
curl -sSi -H 'Authorization: Bearer lab-neva-token' "$API/v1/baselines/$ID"
curl -sSi -H 'Authorization: Bearer lab-neva-token' -H 'Content-Type: application/json' \
  -d "{\"sha256\":\"$DIGEST\"}" "$API/v1/baselines/$ID/verify"
```

| Operation | Success | Typical rejection |
| --- | --- | --- |
| `POST /v1/baselines` | `201`, new owned record | `409 plan_limit_reached` |
| `GET /v1/baselines` | `200`, tenant-only list with plan usage | `401 invalid_token` |
| `GET /v1/baselines/{id}` | `200`, owned record | `404 baseline_not_found` |
| `POST /v1/baselines/{id}/verify` | `200`, `decision: allow` | `404 baseline_not_found`, `422 digest_mismatch` or `409 baseline_integrity_failed` |

The server authenticates the bearer token before querying SQLite. It calculates the count and creates the baseline in one write transaction. An HMAC-SHA256 tag covers the baseline ID, tenant ID, label, digest and timestamp. The key lives in the container configuration, separately from the SQLite data volume.

## Use the file probe for experiments

```sh
python3 tools/probe_artifact.py --tenant B fixtures/camera-config.json \
  --output local-evidence/artifact-probe.json
```

The probe registers the trusted file's digest and checks both the original and a candidate with one bit flipped. It hashes in memory and leaves the file unchanged. Supply `--candidate path/to/changed-file` to compare a specific variant, or `--baseline ID` to reuse an enrolled reference without consuming another slot. It records decisions and HTTP round-trip times. A clean allow and changed rejection are required for a successful exit.

For thesis experiments, use the same trusted model or configuration file and the same controlled variant in this probe and in the biometric implementation. This gives you a reference result to compare with the actual verification decision and audit event from that implementation. The coursework run establishes file integrity decisions on synthetic fixtures. The biometric matcher and attack detector require separate measurements.

## Repository layout

`service.py` implements the HTTP API and persistence. `Dockerfile` builds the runtime image. `tools/` contains the HTTP client, acceptance script and file probe. `fixtures/` provides a synthetic input. `docs/` records the design and observed results. `evidence/` contains the recorded Docker run.
