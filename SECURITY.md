# Security policy

## Supported versions

This project is pre-1.0. Security fixes are applied to the current `main` branch and the latest `0.1.x` release only.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability, credential, candidate record, or private document. Use GitHub's private vulnerability reporting feature after the repository is published. If that channel is temporarily unavailable, contact the repository owner privately and include:

- the affected version or commit;
- a minimal reproduction using synthetic data;
- the likely impact and required preconditions; and
- any suggested mitigation.

Do not test against systems or data you do not own or have explicit permission to assess. Do not include secrets, production URLs, access tokens, Feishu/Lark identifiers, or real HR data in a report.

We aim to acknowledge complete reports within five business days. Acknowledgement is not a promise of a particular disclosure or release date.

## Deployment warning

`DEMO_MODE=true` disables user-facing access controls for local evaluation. It must not be exposed to the internet or used with real candidate data. Before a real deployment, review [the threat model](docs/THREAT_MODEL.md), replace demo credentials, use HTTPS, restrict network access, configure retention, and add the controls marked as planned.
