# Deployment

## Demo

Use `.env.example` unchanged and run `docker compose up --build` or Uvicorn directly. Demo mode intentionally permits unauthenticated local browsing and seeds fictional data.

## Production minimum

Before setting `ENVIRONMENT=prod` and `DEMO_MODE=false`:

1. generate a random `APP_SESSION_SECRET` of at least 32 characters;
2. set a strong `HR_ADMIN_PASSWORD` of at least 12 characters;
3. use HTTPS at a stable domain for `PUBLIC_BASE_URL`;
4. mount a durable, private data directory for SQLite and uploads;
5. keep `STORE_EVENT_PAYLOADS=false` and `STORE_RAW_OCR_TEXT=false` unless a reviewed requirement says otherwise;
6. set `ALLOW_EXTERNAL_AI=true` only after provider/privacy review; and
7. configure Feishu/Lark credentials through the platform secret manager, not in the image or repository.
8. mount and review `WORKFLOW_CONFIG_PATH` when using a workflow other than the versioned fictional default.

`docker-compose.prod.yml` uses a named data volume, disables demo mode, and runs without source-code bind mounts or reload.

## Scale and availability limits

- SQLite is not a multi-instance coordination database. Use PostgreSQL or another shared transactional database before horizontal scaling.
- The scheduler runs in the web process. Multiple web replicas would duplicate schedules without leader election.
- Local uploads do not work across replicas. Use private object storage with encryption, malware scanning, retention, and deletion controls.
- Database schema changes use a small SQLite compatibility helper, not a full migration framework.

## Smoke checks

```bash
curl --fail http://127.0.0.1:8000/health
python -m pytest -q
```

Run the non-demo access-control tests before exposure to a network. Put a reverse proxy in front of Uvicorn, enforce HTTPS, restrict request-body size, and redact `access_token` query parameters from access logs.
