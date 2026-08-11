# Open-source security audit

Audit date: 2026-08-11  
Scope: the history-free `hr-onboarding-agent-public` snapshot  
Decision: **published after the remote Python, Docker, CodeQL, and supply-chain workflows passed and all high-severity CodeQL alerts were resolved or dispositioned. The owner confirmed the Apache-2.0 release rights, the public README media rendered successfully, and signed release `v0.1.0` was published with a GitHub-verified signature.**

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
| Tests | 260 passed; one upstream Starlette `httpx2` migration warning |
| Ruff | 0 findings with Python 3.11 target |
| Compile | `app` and `scripts` compile successfully |
| Dependency consistency | `pip check` reports no broken requirements |
| pip-audit 2.10.1 | 37 locally resolved components and 38 in the first remote isolated runtime; 0 known vulnerabilities in both |
| CycloneDX | First remote JSON SBOM 1.6: 40 components and dependency entries, including 38 frozen runtime packages plus pip and setuptools |
| License inventory | First remote inventory: 38 packages; no UNKNOWN, GPL, AGPL, or proprietary result |
| Bandit 1.9.4 | 0 medium/high findings after manual review and XML hardening |
| detect-secrets 1.5.0 | 2 synthetic unit-test keyword candidates; both manually verified false positives; `.git/` metadata excluded from remote scans |
| Deterministic snapshot audit | 166 publishable files; 0 blocking findings |
| Browser capture | 2 PNG screenshots and one 4-frame GIF from real demo pages |
| Workflow configuration | Valid/invalid/unsafe YAML and CI configuration regression tests pass |

Generated SCA, SBOM, license, Bandit, secret-scan, and snapshot-audit evidence is kept under ignored `artifacts/security/` locally and uploaded as a 30-day GitHub Actions artifact. The first remote artifact was downloaded and reviewed; the workflow now excludes ephemeral `.git/` checkout metadata after its `FETCH_HEAD` commit SHA appeared as a non-secret high-entropy candidate.

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

### CodeQL high-severity review

The first authenticated alert readback found 12 high-severity alerts even though the CodeQL workflow completed successfully. One `py/bad-tag-filter` finding was valid: regular expressions were being used to strip script/style elements from local HTML knowledge sources. It was replaced with Python's `HTMLParser` and covered by a malformed-end-tag regression test.

The other 11 `py/path-injection` findings are false-positive data-flow results after manual reachability review:

- the HR-only file-review endpoint is protected by HR authentication and resolves the requested path before enforcing upload-root containment, regular-file status, and the configured size limit;
- candidate uploads require a case-bound candidate session and same-origin request, validate the required material type, reduce the supplied name to a basename, enforce extension/MIME/magic-byte allowlists and size limits, sanitize every path segment, add a server-generated nonce, and enforce resolved-path containment under the configured upload root before writing; and
- downstream OCR/review/stat calls receive only the server-created confined path in production; no alternate untrusted production caller exists.

On 2026-08-11, the repository owner explicitly authorized dismissing CodeQL alerts #1–#11 as false positives. Each alert now records the above rationale in GitHub. Any future caller that accepts a path directly must repeat upload-root confinement before file access.

The valid parser finding remains fixed in this pull request and the pull-request CodeQL analysis passes. After merge, authenticated default-branch readback must confirm that no open high- or critical-severity alert remains.

## License review

The runtime inventory contains permissive Apache, BSD, ISC, MIT, PSF, Unlicense, and certifi's MPL-2.0 package terms. No unknown, GPL, AGPL, or proprietary classifier was reported. On 2026-08-11, the repository owner confirmed the right to release every included source file under Apache-2.0. This is an owner attestation rather than an independent legal opinion; the dependency review identified no additional required NOTICE attribution.

## Release verification and production gaps

Release-owner actions completed on 2026-08-11:

- confirmed code ownership and Apache-2.0 release rights;
- verified the README badges, GIF, screenshots, and architecture diagram on the public repository page; and
- registered a dedicated Ed25519 GitHub Signing Key, created signed tag `v0.1.0`, and published the GitHub-verified release.

Production controls, not public-source blockers:

- organization SSO/RBAC and rate limiting;
- encrypted private object storage, malware scanning, retention/deletion, and restore testing;
- candidate-token revocation and authenticated encrypted Feishu/Lark callback support;
- PostgreSQL/migrations and distributed scheduler ownership;
- centralized audit logs, egress allowlists, and provider data-processing agreements; and
- tenant isolation and per-tenant secrets.

## Conclusion

The code, data, dependency, workflow, provider, history-isolation, remote CI, licensing-attestation, README-rendering, and signed-tag gates support the published `v0.1.0` public release. The production controls above remain mandatory before processing real HR data.
