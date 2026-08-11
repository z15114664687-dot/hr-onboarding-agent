# Public release checklist

Status: **published as a new history-free public repository. Tag/release remains gated on the unchecked owner actions below.**

## Verified in the release candidate

- [x] Snapshot is a separate directory with no `.git` and no inherited source history.
- [x] Private application files/history were not modified during staging work.
- [x] No `.env`, database, upload, log, archive, cache, virtual environment, key, symlink, or oversized release file is present.
- [x] No machine-specific user path, production endpoint, known personal fixture, or credential-shaped value remains.
- [x] Demo data, workflow templates, policies, workbook, injection fixtures, screenshots, and GIF are synthetic.
- [x] One-command demo performs no external AI or Feishu/Lark call.
- [x] Python 3.11.15 full suite passes: 259 tests.
- [x] Ruff, compileall, and `pip check` pass.
- [x] Local pip-audit covered 37 resolved runtime components; the first remote isolated runtime covered 38; both reported 0 known vulnerabilities.
- [x] The first remote CycloneDX 1.6 SBOM contains 40 components and dependency entries: 38 frozen runtime packages plus pip and setuptools.
- [x] The first remote runtime license inventory contains 38 packages and no unknown/GPL/AGPL/proprietary result.
- [x] Bandit medium/high scan has 0 findings after `defusedxml` hardening and one reviewed server-bind exception.
- [x] detect-secrets candidates were manually reviewed as synthetic unit-test values; `.git/` metadata is excluded from remote scans; deterministic public audit reports 0 findings across 166 release files.
- [x] OpenAI provider text/schema/image/PDF/timeout behavior is covered by offline mocked tests.
- [x] Workflow YAML is bounded, safe-loaded, strictly validated, configurable, and covered by negative tests.
- [x] Real HR/candidate screenshots and a 4-frame GIF were captured from a temporary demo database and manually inspected.
- [x] CI, container smoke, SCA/SBOM, CodeQL, dependency review, Dependabot, CODEOWNERS, issue templates, and repository security instructions are present.
- [x] Every external GitHub Action is pinned to an immutable commit SHA.
- [x] The release-candidate review completed before the independent public Git history was created.

## Before creating the public repository

- [ ] Confirm ownership or compatible licensing for every source file and add required NOTICE/copyright attribution.
- [x] Review `OSS_SECURITY_AUDIT.md`, `docs/RELEASE_NOTES_v0.1.0.md`, and `docs/GITHUB_LAUNCH.md`.
- [x] Run `PYTHON_BIN=python3.11 ./scripts/verify_release.sh` once more from the final source directory.
- [x] Run `python3.11 scripts/audit_public_release.py --root .` and require `pass`.
- [x] Inspect `git status --short` before the first commit and include only this clean snapshot.

## After the first push, before v0.1.0

- [x] Python 3.11 CI passes.
- [x] Docker image builds and the live `/health` smoke test passes in GitHub Actions.
- [ ] CodeQL completes with no unresolved high-severity alert.
- [x] Supply-chain workflow uploads reviewed pip-audit, runtime freeze, SBOM, license, Bandit, secret-scan, and snapshot-audit evidence.
- [x] Dependabot recognizes pip, GitHub Actions, and Docker.
- [x] Enable dependency graph, Dependabot alerts/security updates, secret scanning, push protection, and private vulnerability reporting.
- [x] Create the `main` ruleset with required checks, Code Owner review, stale-review dismissal, no force push, and no deletion.
- [x] Keep Actions token permissions read-only and do not enable duplicate CodeQL default setup.
- [x] Pin the Python 3.11 base image digest after verifying the first successful build and keep digest updates enabled through Dependabot.
- [ ] Confirm README badges/GIF render and run the demo from a fresh public clone.
- [x] Read back remote `main`, file list, README, and root history; verify exactly one clean public root history.
- [ ] Create and sign the `v0.1.0` tag, then publish the prepared release notes.

## Production deployment controls

These are not blockers for publishing source, but they are blockers for real HR data:

- [ ] Organization SSO/OIDC, RBAC, and rate limits
- [ ] Encrypted private object storage, malware scanning/CDR, retention/deletion, and restore tests
- [ ] Candidate-token revocation and authenticated encrypted Feishu/Lark callbacks
- [ ] PostgreSQL/Alembic and distributed scheduler ownership
- [ ] Central audit logging, managed secrets, egress allowlists, and provider DPAs
- [ ] Multi-tenant isolation and per-tenant encryption/secrets

The exact GitHub commands, repository description, About copy, topics, ruleset settings, and first issue drafts are in [`docs/GITHUB_LAUNCH.md`](docs/GITHUB_LAUNCH.md).
