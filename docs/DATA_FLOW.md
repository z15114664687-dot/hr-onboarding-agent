# Data flow

## Candidate document review

1. A candidate opens a case-bound workspace link.
2. Outside demo mode, the HMAC link token is checked against the case ID and expiry. A restricted cookie continues the session.
3. The browser sends base64 file content, file name, material type, and MIME type.
4. The API enforces decoded size, a per-material extension allowlist, MIME/extension consistency, magic bytes, a safe file name, and an upload-root path.
5. Demo/mock mode performs deterministic local OCR. Opted-in providers may receive the document after `ALLOW_EXTERNAL_AI=true` is set.
6. OCR text and extracted fields enter the untrusted-content boundary. Suspicious instructions force manual review.
7. Deterministic material rules run. An optional LLM may only keep or increase the severity of the rule result; it cannot promote an existing rejection/manual review to approval.
8. Structured results are stored in SQLite. Raw OCR text is stored only in demo mode or when `STORE_RAW_OCR_TEXT=true`.
9. When configured, structured summaries synchronize to Bitable and a candidate card is sent.

## Feishu/Lark event flow

1. The HTTP callback validates the verification token and event age.
2. Encrypted payloads are rejected because authenticated decryption is not implemented.
3. The service claims a stable event ID in SQLite; duplicate successful events are ignored.
4. Raw event payloads are not persisted unless `STORE_EVENT_PAYLOADS=true`.
5. The message router resolves the case by Feishu identity, returns progress/material/Q&A content, and records only non-PII operational logs.

## Reminder flow

APScheduler scans overdue nodes daily. Recipient resolution and message templates are deterministic. Sending occurs only through configured Feishu/Lark adapters; demo mode has no credentials and performs no external delivery.
