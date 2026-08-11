# GitHub launch guide

This file is the owner checklist for publishing the clean snapshot as a new repository. Never import, merge, or force-push the private repository history.

## Repository metadata

**Repository name**

`hr-onboarding-agent`

**Description**

> Open-source AI-assisted HR onboarding for Feishu/Lark: configurable workflows, document OCR and review, Bitable sync, reminders, and a zero-credential demo.

**About text**

> A human-controlled FastAPI reference implementation for HR/People Ops teams building onboarding workflows on Feishu/Lark. Run the fictional demo without credentials, then connect OCR/LLM providers and Bitable only when your privacy controls are ready.

**Topics**

`hr-tech`, `onboarding`, `people-operations`, `feishu`, `lark`, `fastapi`, `python`, `workflow-automation`, `ocr`, `llm`, `human-in-the-loop`, `ai-agents`, `rag`, `document-ai`, `open-source`

Use `docs/assets/demo/hr-workspace.png` as the initial social preview if no dedicated 1280×640 image is prepared. Keep **Releases** visible in the About panel; leave the website field empty until a maintained public demo exists.

## Before repository creation

- [ ] Confirm every included file is owned by the releasing party or has compatible licensing/attribution.
- [ ] Confirm this directory has no `.git` and is not the private source directory.
- [ ] Run `PYTHON_BIN=python3.11 ./scripts/verify_release.sh`.
- [ ] Run `python3.11 scripts/audit_public_release.py --root .` and require `pass`.
- [ ] Review `OSS_SECURITY_AUDIT.md`, `PUBLIC_RELEASE_CHECKLIST.md`, and the generated SBOM/license evidence.
- [ ] Inspect both screenshots and the GIF for real names, URLs, browser chrome, or hidden metadata.
- [ ] Confirm `.env`, databases, uploads, logs, archives, caches, virtual environments, and symlinks are absent.
- [ ] Confirm the private repository has no newly modified tracked file.

## Create the clean public repository

Run these commands only from the clean snapshot directory:

```bash
git init -b main
git add -A
git status --short
git commit -m "feat: publish hr onboarding agent v0.1.0"
gh repo create z15114664687-dot/hr-onboarding-agent \
  --public \
  --source=. \
  --remote=origin \
  --push \
  --description "Open-source AI-assisted HR onboarding for Feishu/Lark: configurable workflows, document OCR and review, Bitable sync, reminders, and a zero-credential demo."
```

Before confirming `git add`, verify that the status contains only the intended public snapshot. Do not add a remote that points to the private repository.

Add topics in the GitHub About editor or with GitHub CLI after creation:

```bash
gh repo edit z15114664687-dot/hr-onboarding-agent \
  --add-topic hr-tech,onboarding,people-operations,feishu,lark,fastapi,python,workflow-automation,ocr,llm,human-in-the-loop,ai-agents,rag,document-ai,open-source
```

## GitHub settings after the first push

- [ ] Follow [`.github/SECURITY_CONFIGURATION.md`](../.github/SECURITY_CONFIGURATION.md).
- [ ] Enable dependency graph, Dependabot alerts, and Dependabot security updates.
- [ ] Enable secret scanning and push protection.
- [ ] Enable private vulnerability reporting.
- [ ] Create the `main` ruleset after the first successful workflow run and require its named checks.
- [ ] Keep Actions default token permissions read-only and prevent Actions from approving pull requests.
- [ ] Do not enable CodeQL default setup while the checked-in advanced workflow is active.
- [ ] Upload the social preview and apply the description/topics above.
- [ ] Confirm the Apache-2.0 license is detected by GitHub.

## First public verification

- [ ] CI passes on Python 3.11.
- [ ] The container image builds and `/health` succeeds.
- [ ] CodeQL completes without an unresolved high-severity alert.
- [ ] Dependency review is available on a test pull request.
- [ ] The supply-chain job uploads pip-audit, Bandit, secret-scan, license, SBOM, and snapshot-audit evidence.
- [ ] Dependabot recognizes pip, Actions, and Docker configuration.
- [ ] README badges resolve and the GIF renders from the public repository.
- [ ] Clone the public repository into a new temporary directory and run `./scripts/demo.sh`.
- [ ] Read back the remote `main` tree and confirm there is exactly one clean public root history.

## Publish v0.1.0

After all first-public verification checks pass:

```bash
git tag -s v0.1.0 -m "HR Onboarding Agent v0.1.0"
git push origin v0.1.0
gh release create v0.1.0 \
  --title "HR Onboarding Agent v0.1.0" \
  --notes-file docs/RELEASE_NOTES_v0.1.0.md
```

If signed tags are not configured, configure a signing key before release rather than silently creating an unsigned release tag.

## First public issues

Create these as separate issues. The proposed labels are `security`, `enhancement`, `good first issue`, `help wanted`, `integration`, and `documentation`.

### 1. Add a workflow YAML validation command and JSON Schema

Labels: `good first issue`, `documentation`

Expose `python -m app.services.workflow_config <path>` (or an equally small CLI) and publish a JSON Schema for editor completion. Acceptance criteria:

- validates the same rules as runtime loading without starting the app;
- exits non-zero with a concise path/field error;
- publishes a version 1 schema covering every supported key and enum;
- adds tests for valid, duplicate, unknown-key, unsafe-tag, and oversized files; and
- updates the workflow section in README.

### 2. Replace HTTP Basic with OIDC SSO and role-based access control

Labels: `security`, `enhancement`, `help wanted`

Add an organization-ready authentication seam without weakening demo mode boundaries. Acceptance criteria:

- supports an OIDC provider through documented environment settings;
- defines HR admin, HR reviewer, and read-only roles;
- binds every admin mutation to a role and actor identifier;
- keeps candidate case tokens separate from employee SSO;
- adds negative authorization/CSRF/session tests; and
- updates the threat model and migration notes.

### 3. Add encrypted object storage, retention, and deletion workflows

Labels: `security`, `enhancement`

Replace local document persistence for production profiles. Acceptance criteria:

- provides an object-storage interface with a local demo adapter;
- supports server-side encryption and private object access;
- records retention class and deletion deadline without storing raw document text by default;
- adds tested candidate/case deletion and audit events;
- documents backup/restore and provider-specific data handling; and
- preserves path, size, MIME, and magic-byte controls.

### 4. Add malware scanning and quarantine before OCR

Labels: `security`, `help wanted`

Introduce a fail-closed scan boundary before any external OCR/model call. Acceptance criteria:

- defines a scanner protocol and deterministic test adapter;
- quarantines infected, failed, or timed-out scans;
- never sends quarantined bytes to OCR or a provider;
- records a sanitized reason and human-review state;
- adds EICAR-style synthetic tests without shipping a live malicious binary; and
- updates deployment and threat-model docs.

### 5. Support authenticated Feishu/Lark encrypted callbacks

Labels: `integration`, `security`

Implement encryption handling without weakening the current fail-closed path. Acceptance criteria:

- decrypts and authenticates supported callback envelopes using the configured encrypt key;
- preserves timestamp freshness and event idempotency;
- rejects malformed, unauthenticated, stale, and replayed messages;
- avoids logging ciphertext, plaintext payloads, or identifiers;
- adds official-format synthetic fixtures and offline tests; and
- documents the Feishu/Lark setup and rotation procedure.

### 6. Add PostgreSQL, Alembic migrations, and scheduler ownership

Labels: `enhancement`, `help wanted`

Create a production persistence profile while keeping the SQLite demo. Acceptance criteria:

- adds Alembic migrations from an empty database;
- runs the test suite against SQLite and PostgreSQL in CI;
- preserves unique idempotency constraints and transactional state transitions;
- prevents duplicate reminder scans across multiple web instances;
- documents backup, restore, and rollback; and
- does not make PostgreSQL mandatory for the zero-credential demo.

### 7. Add permission-aware knowledge retrieval and exportable audit logs

Labels: `enhancement`, `help wanted`

Make retrieval and review traces easier to govern. Acceptance criteria:

- filters knowledge chunks by caller role and policy scope;
- records source identifiers, provider, model, rule result, and human action without raw sensitive text;
- exports a case-scoped audit bundle with deterministic ordering;
- adds cross-role leakage and redaction tests; and
- documents retention and incident-response use.

### 8. Add English UI copy without changing the Chinese demo default

Labels: `good first issue`, `enhancement`

Extract visible workspace strings into a minimal locale catalog. Acceptance criteria:

- supports Chinese and English through a documented locale setting;
- keeps synthetic data and statuses consistent across both locales;
- adds page tests for both languages;
- does not introduce a frontend framework; and
- refreshes one English screenshot through the existing capture script.

## Launch rhythm

For the first week, pin a demo issue/discussion, reply quickly to reproducible reports, tag small contribution-ready issues, and publish only verified screenshots or benchmark claims. Do not inflate the roadmap with integrations that have no owner or test plan.
