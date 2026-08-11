# Open-source security audit

Audit date: 2026-08-11  
Scope: the history-free `hr-onboarding-agent-public` snapshot  
Decision: **published after the initial remote Python, Docker, CodeQL, and supply-chain workflows passed; release tagging remains gated on owner licensing confirmation and signed-tag setup.**

No known credential, private key, local database, upload directory, production endpoint, personal record, symlink, nested Git repository, oversized file, or current runtime dependency vulnerability remains in the release candidate. This is a point-in-time engineering review, not a certification, legal opinion, or guarantee that the application is vulnerability-free.

## Provenance boundary

- The snapshot has no `.git` directory and does not inherit the private repository history.
- Deleted/history-only internal HR materials were not restored, opened, or copied into this snapshot.
- Demo candidates use fictional names, fictional organizations/cities, and `example.com` email addresses.
- The screenshots and GIF were captured from a temporary zero-credential demo database, not from a private or live site.
- The original private application directory and its tracked history are outside the publication scope and must never be merged into the public repository.

## Reproducible automated evidence

| Gate | Result |
| --- | --- |
| Python | 3.11.15 local isolated runtime |
| Tests | 259 passed; one upstream Starlette `httpx2` migration warning |
| Ruff | 0 findings with Python 3.11 target |
| Compile | `app` and `scripts` compile successfully |
| Dependency consistency | `pip check` reports no broken requirements |
| pip-audit 2.10.1 | 37 resolved runtime components; 0 known vulnerabilities |
| CycloneDX | JSON SBOM 1.6; 37 components and 37 dependency entries |
| License inventory | 37 packages; no UNKNOWN, GPL, AGPL, or proprietary result |
| Bandit 1.9.4 | 0 medium/high findings after manual review and XML hardening |
| detect-secrets 1.5.0 | 2 synthetic unit-test keyword candidates; both manually verified false positives |
| Deterministic snapshot audit | 166 publishable files; 0 blocking findings |
| Browser capture | 2 PNG screenshots and one 4-frame GIF from real demo pages |
| Workflow configuration | Valid/invalid/unsafe YAML and CI configuration regression tests pass |

Generated SCA, SBOM, license, Bandit, secret-scan, and snapshot-audit evidence is kept under ignored `artifacts/security/` locally and uploaded as a 30-day GitHub Actions artifact.

## Dependency remediation performed

The first current-database audit found 49 advisories across four packages. Publication was blocked until all were resolved.

| Initial affected package | Resolution | Final state |
| --- | --- | --- |
| `pypdf 5.9.0` | Upgraded to `6.15.0` | No known advisory in the final audit |
| `python-dotenv 1.0.1` | Upgraded to `1.2.2` | No known advisory in the final audit |
| `starlette 0.41.3` | Upgraded through `fastapi 0.141.1` to `starlette 1.6.0` | No known advisory in the final audit |
| `protobuf 3.20.3` | Upgraded `lark-oapi` to `1.7.2`, which removes this runtime dependency | Absent from the clean runtime environment |

Other direct dependencies were refreshed to compatible current versions and the full offline suite was rerun. Runtime dependencies remain exactly pinned; CI freezes and audits the resolved transitive environment.

## Source review and findings

### Authentication and authorization

- HR/admin routes require HTTP Basic outside demo/test mode and fail closed when no password is configured.
- Candidate links are HMAC-signed, expire, bind to one case, use uniform 404 responses, and move into a SameSite, HttpOnly cookie.
- Browser writes check same origin; candidate cookies are scoped to one candidate path.
- Feishu/Lark HTTP callbacks require a verification token, a bounded timestamp, and a claimed idempotency key. Unsupported encrypted callbacks fail closed.

Residual: organization SSO/RBAC, candidate-token revocation, rate limits, and multi-tenant policy enforcement are not implemented.

### Uploads, Office files, and local paths

- Uploaded bytes are bounded before storage; file name, extension, MIME, magic bytes, random stored name, and resolved-path confinement are checked.
- Bandit identified eight standard-library XML parsing sites used for XLSX/DOCX content. They were changed to `defusedxml`; an entity-expansion workbook regression test now fails closed.
- No generic command execution, shell invocation, pickle deserialization, or runtime unsafe YAML loading is present.
- The only subprocess call is the maintainer-only screenshot script, which starts a fixed `uvicorn` argument list with no shell.

Residual: valid-looking PDF/Office files are not malware-scanned or content-disarmed, and compressed-archive expansion still needs infrastructure-level resource controls.

### Workflow YAML

- `WORKFLOW_CONFIG_PATH` is parsed with `yaml.safe_load`.
- Files are capped at 128 KiB and 64 nodes.
- Root/step keys, version, types, string lengths, unique lowercase node names, offsets, roles, units, grouping, and candidate visibility are validated.
- Unknown, unsafe, empty, duplicate, missing, and oversized configurations fail closed.

### Providers and untrusted model content

- External AI requires `ALLOW_EXTERNAL_AI=true` plus provider credentials.
- Configurable provider endpoints require public HTTPS unless private endpoints are explicitly allowed.
- Uploaded/OCR text is marked as untrusted evidence; deterministic findings and human review remain authoritative.
- OpenAI tests cover disabled access, credentials, structured Responses output, image input, PDF `input_file`, nested output, malformed JSON, empty output, transport headers, timeouts, and no retry side effects without making a network inference call.

### CI/CD and repository controls

- GitHub Actions use immutable 40-character commit SHAs and least-privilege tokens.
- No `pull_request_target`, `write-all`, live secret, or event-text interpolation into shell commands is present.
- CI separates Python tests, container smoke, SCA/SBOM, CodeQL, and dependency review.
- Dependabot covers pip, GitHub Actions, and Docker.
- GitHub secret scanning, push protection, private vulnerability reporting, read-only Actions permissions, and the `main` ruleset were enabled after the public repository was created.

The Gemini gateway bind warning is an intentional server behavior: its host is configurable and defaults to an external container bind. Bandit B104 is suppressed on that single reviewed line with an explanatory comment.

## License review

The runtime inventory contains permissive Apache, BSD, ISC, MIT, PSF, Unlicense, and certifi's MPL-2.0 package terms. No unknown, GPL, AGPL, or proprietary classifier was reported. Dependency license compatibility does not establish ownership of this application's source; the repository owner must still confirm the right to release every included source file under Apache-2.0 and add any required NOTICE attribution.

## Residual release and production gaps

Release-owner actions:

- review the uploaded supply-chain artifact and any CodeQL alerts;
- confirm code ownership/licensing and add any required NOTICE attribution;
- verify the README badges/GIF and demo from a fresh public clone; and
- configure signed-tag tooling before creating `v0.1.0`.

Production controls, not public-source blockers:

- organization SSO/RBAC and rate limiting;
- encrypted private object storage, malware scanning, retention/deletion, and restore testing;
- candidate-token revocation and authenticated encrypted Feishu/Lark callback support;
- PostgreSQL/migrations and distributed scheduler ownership;
- centralized audit logs, egress allowlists, and provider data-processing agreements; and
- tenant isolation and per-tenant secrets.

## Conclusion

The code, data, dependency, workflow, provider, history-isolation, and initial remote CI gates support the published public source snapshot. Do not create the `v0.1.0` release until the owner completes the remaining licensing, artifact review, public-clone, and signed-tag checks in `PUBLIC_RELEASE_CHECKLIST.md`.
