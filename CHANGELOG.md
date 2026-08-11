# Changelog

All notable changes will be documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project intends to use semantic versioning after its first public release.

## [Unreleased]

## [0.1.0] - 2026-08-11

### Added

- Synthetic, credential-free demo mode with three fictional candidate journeys.
- Candidate and HR workspaces, workflow nodes, document review, reminders, and Feishu/Lark adapters.
- Pluggable mock, OpenAI, Gemini, Ark/Doubao, Zhipu, and DeepSeek paths.
- Candidate access links, HR Basic authentication, callback freshness/idempotency, upload validation, and prompt-injection regression fixtures.
- Architecture, deployment, provider, data-flow, threat-model, and public-release documentation.
- Safe-YAML workflow templates and validation.
- Python 3.11, Docker smoke, Ruff, pip-audit, CycloneDX SBOM, Bandit, CodeQL, dependency review, and secret-scan workflows.
- One-command demo plus real-browser screenshots and GIF capture.

### Security

- External AI and sensitive raw-payload persistence are disabled by default.
- Unsupported encrypted callbacks fail closed.
- Public snapshot excludes private Git history, local databases, uploads, secrets, internal materials, and production URLs.
- OpenAI Responses API support uses structured outputs, image input, and PDF file input behind explicit opt-in.
