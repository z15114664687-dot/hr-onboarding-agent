# Contributing

Thank you for improving HR Onboarding Agent. Keep changes small, testable, and safe to review in public.

## Local setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
./scripts/verify_release.sh
uvicorn app.main:app --reload
```

Demo mode must use fictional names, contacts, documents, organizations, policy text, IDs, and URLs. Never contribute real HR records, production exports, credentials, internal documents, local databases, upload directories, or copied private-repository history.

## Pull requests

1. Open or reference an issue for non-trivial changes.
2. Explain the behavior change and its security/privacy impact.
3. Add focused tests, then run `./scripts/verify_release.sh` with Python 3.11.
4. Confirm that examples remain synthetic and no `.env`, database, upload, credential, personal identifier, or internal URL is present.
5. Update documentation when configuration, trust boundaries, provider behavior, or deployment assumptions change.

Avoid unrelated refactors. Maintainers may ask for a smaller pull request.

## Security-sensitive areas

Changes to authentication, candidate links, callbacks, upload handling, prompt construction, provider URLs, logging, retention, or dependency installation require explicit threat-model review and negative tests. Do not weaken a fail-closed behavior to make a demo easier.

## Adding a model or OCR provider

Implement the relevant protocol in `app/providers/base.py`, keep credentials in environment settings, use a finite timeout, prevent private-network base URLs by default, preserve the untrusted-content boundary, and add offline tests with mocked transport. The default demo must never call the provider.

## Adding a workflow template

Use stable machine-readable node codes, make ownership and due-date rules explicit, and add only fictional examples under `examples/`. Workflow templates must not encode a real employer's internal process or policy text.

Run the workflow-specific tests after editing a template:

```bash
python -m pytest -q tests/test_workflow_config.py tests/test_workflow.py
```

To refresh public demo media, run `./scripts/capture_demo.sh`, inspect every pixel and frame, and commit only files under `docs/assets/demo/`. Never capture a production or private deployment.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
