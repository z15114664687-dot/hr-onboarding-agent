## What changed

Describe the smallest behavior change and link its issue.

## Security and privacy

- [ ] Examples and tests contain synthetic data only.
- [ ] No credentials, `.env`, database, upload, production URL, private identifier, or private-repository history is included.
- [ ] Authentication, uploads, callbacks, prompts, providers, logging, and retention were reviewed if touched.
- [ ] Documentation and the threat model were updated if a trust boundary changed.

## Verification

- [ ] `python -m pytest -q`
- [ ] `python -m compileall -q app`
- [ ] `python -m pip check`

