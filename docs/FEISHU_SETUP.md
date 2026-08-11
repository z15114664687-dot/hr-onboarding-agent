# Feishu/Lark setup

## App credentials

Create a Feishu/Lark app and grant only the scopes needed by the enabled features. Store `FEISHU_APP_ID` and `FEISHU_APP_SECRET` in your deployment secret manager.

For HTTP callbacks:

- callback URL: `https://your-domain.example/api/feishu/events`
- set `FEISHU_VERIFICATION_TOKEN`;
- keep the default five-minute event-age window or choose a reviewed value; and
- do not enable encrypted callbacks with this release. Encrypted payloads are rejected until authenticated decryption is implemented.

For long connection:

```bash
python -m app.workers.feishu_event_listener
```

The long-connection worker and web process must share the same database. The Compose profile is `integrations`.

## Candidate links

Feishu/Lark cards use time-limited candidate links derived from `APP_SESSION_SECRET`. Use HTTPS and configure `FEISHU_WEB_APP_BASE_URL` if it differs from `PUBLIC_BASE_URL`. Redact query strings at the proxy because the initial link contains an access token.

## Bitable

Set the Base app token and table IDs only for the tables you use:

- `FEISHU_CANDIDATE_TABLE_ID`
- `FEISHU_NODE_TABLE_ID`
- `FEISHU_MATERIAL_TABLE_ID`
- `FEISHU_FAQ_TABLE_ID`

Field mappings are in `app/core/config.py`. Review them against a synthetic test Base first. Do not copy a production Base export into this repository.

## Validation

Use a synthetic candidate and confirm: callback validation, duplicate event handling, card links, case updates, material summaries, and Bitable readback. Demo mode is not an integration test because it intentionally bypasses credentials.
