# Practical Work 6 — Docker demonstration

All three acceptance checks passed on 18 November 2026. The test runner recorded 27 HTTP exchanges with the containerised service. Test tenants were independent fictional organisations, and the records used synthetic digests.

| Environment | Observed configuration |
| --- | --- |
| Virtual machine | CC-PW56, VirtualBox 7.2.22, 1 vCPU, 2 GiB RAM, 8 GiB virtual disk |
| Guest OS | Alpine Linux 3.24.3, Linux 6.18.58-0-virt, x86_64 |
| Docker | 29.9.3 client and server |
| Runtime in image | Python 3.12.16, SQLite 3.46.1, UID 10001 |
| Image | `artifact-integrity-lab:1` (`linux/amd64`) |

Docker built the image from the repository source with `docker build --pull=false --network=none -t artifact-integrity-lab:1 .`. The image ID was `sha256:7fd5dc4b8ec4590d7119de4013a9321a918bf34da4e0e69e24365573dd4dbee7`. The VM used the already loaded Python base image, a fresh SQLite volume and a listener on `127.0.0.1:8080`.

## Acceptance results

| Check | Expected | Observed |
| --- | --- | --- |
| Normal use | Each tenant creates, reads and lists its own baseline. Matching digest is allowed. Changed digest is rejected. | **Pass.** Each create returned HTTP 201. Each owned GET and list returned HTTP 200 and the expected record. For both A and B the matching digest returned HTTP 200 `allow` and the changed digest returned HTTP 422 `digest_mismatch`. |
| Tenant isolation | A receives HTTP 404 when using its own token for B's baseline. B can still read its record, with exactly the same contents. | **Pass.** A received HTTP 404 `baseline_not_found` on both read and verify for B's baseline `1d59ecb22df6482bbce57a2faca5c5bd`. B's before and after HTTP 200 bodies were identical. An unknown ID returned the same 404 body. |
| Plan limit | A's third create returns HTTP 409 and its count remains 2. B's third create succeeds and reaches 3/5. | **Pass.** A reached 2/2. Its third request returned HTTP 409 `plan_limit_reached` and the complete before and after lists matched. B's third create returned HTTP 201 and its list reported 3/5. |

A request with no token returned HTTP 401. A create request supplying `tenant_id: B` in the JSON while using A's token returned HTTP 400. The server obtains ownership from the authentication result, and each lookup uses that ownership alongside the baseline ID.

## Integrity experiments

The file probe verified the original synthetic camera configuration and an in-memory one-bit change. The original returned HTTP 200 `allow`. The changed candidate returned HTTP 422 `digest_mismatch`. A separate test replaced the SHA-256 value inside a disposable SQLite row and sent the forged digest to the API. The unchanged HMAC tag caused HTTP 409 `baseline_integrity_failed`. Reinstating the original digest restored HTTP 200 for the original candidate.

## Implementation changes

The four workflow operations, tenant access rules and plan allowances followed the design brief. Implementation added a Python acceptance runner and a local-file probe to make the checks repeatable. It also checked the baseline's HMAC before comparing digests. This coursework demonstrates checking trusted artifact bytes and detecting changes to stored baseline fields. Testing model robustness and end-to-end biometric authentication remains separate.

## Evidence

- [Raw requests and responses from the acceptance run](../evidence/acceptance.json)
- [Environment and tested source hashes](../evidence/environment.txt), [Docker build log](../evidence/build.txt) and [container start and health](../evidence/container.txt)
- [Local file trace](../evidence/artifact-probe.json) and [altered SQLite baseline trace](../evidence/baseline-tamper.json)
- VM screenshots for the [Docker build](../evidence/screenshots/01-docker.png), [acceptance checks](../evidence/screenshots/02-acceptance.png) and [integrity experiments](../evidence/screenshots/03-integrity.png)
