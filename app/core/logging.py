import logging
import sys

from app.core.config import get_settings

SENSITIVE_KEYS = ("secret", "token", "encrypt_key", "authorization")


class SecretRedactionFilter(logging.Filter):
    """Redact sensitive key-value fragments from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        lowered = message.lower()
        if any(key in lowered for key in SENSITIVE_KEYS):
            record.msg = "[redacted sensitive log message]"
            record.args = ()
        return True


def setup_logging() -> None:
    """Configure app logging with a simple structured-ish format."""

    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    redaction_filter = SecretRedactionFilter()
    root = logging.getLogger()
    root.addFilter(redaction_filter)
    for handler in root.handlers:
        handler.addFilter(redaction_filter)
