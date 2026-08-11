# HR Onboarding Agent v0.1.0

The first public release is a history-free reference implementation for human-controlled HR onboarding on Feishu/Lark. It can be evaluated locally with fictional data and deterministic providers before any integration or external AI is configured.

## Highlights

- HR and candidate workspaces with onboarding timelines, status updates, material handling, and overdue views
- Versioned safe-YAML workflow templates with owners, due-date units, grouping, and candidate visibility
- Document upload validation, deterministic review, prompt-injection regression fixtures, and human-review gates
- Provider protocols plus deterministic mock, OpenAI Responses API, Gemini, Ark/Doubao, Zhipu, and DeepSeek paths
- Feishu/Lark callbacks, long connection, interactive cards, Bitable synchronization, and reminders
- One-command local/Docker demo and reproducible real-browser screenshots/GIF

## Security and release engineering

- External AI, raw event storage, and raw OCR persistence are off by default.
- HR/admin access fails closed outside demo/test mode; candidate access is time-limited and case-bound.
- Uploads are bounded by name, size, extension, MIME, magic bytes, and resolved storage path.
- Python 3.11 CI runs Ruff, compile checks, dependency consistency, and the offline test suite.
- Container CI builds the Python 3.11 image and requires a live `/health` response.
- Security CI runs pip-audit, Bandit, a secret scan, license inventory, the public-snapshot audit, and CycloneDX SBOM generation.
- CodeQL, dependency review, Dependabot, CODEOWNERS, and least-privilege workflow permissions are checked in.

## Demo

```bash
./scripts/demo.sh
```

The demo seeds only `example.com` identities and fictional workflows. It does not contact Feishu/Lark or a model provider.

## Known limitations

This release is not production-complete. It has no organization SSO/RBAC, multi-tenant isolation, encrypted object storage, malware scanning, automated retention/deletion, token revocation, distributed scheduling, or authenticated Feishu/Lark encrypted-callback handling. SQLite and in-process scheduling are intended for a single instance.

See [`docs/THREAT_MODEL.md`](THREAT_MODEL.md) and [`OSS_SECURITY_AUDIT.md`](../OSS_SECURITY_AUDIT.md) before using real HR data.

## Upgrade notes

This is the initial release. Copy `.env.example` to `.env`, keep `DEMO_MODE=true` for evaluation, and set `WORKFLOW_CONFIG_PATH` only to a reviewed version 1 YAML file. External providers require an explicit global opt-in plus provider-specific credentials.

## Verification

The release candidate is verified locally on Python 3.11.15 with 259 offline tests, Ruff, compilation, dependency consistency, safe-YAML regression tests, mocked OpenAI transport tests, a real-browser demo capture, and a 166-file deterministic public-snapshot audit. Local and first-public-run pip-audit evidence found no known vulnerability across 37 and 38 resolved runtime components respectively. The first public Actions runs also proved the Docker health smoke, CodeQL workflow, supply-chain evidence upload, and repository-backed security settings.
