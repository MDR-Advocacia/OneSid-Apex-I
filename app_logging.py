import logging
import os
import sys


def build_logging_handlers(log_path, *, service):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_path, encoding="utf-8"),
    ]

    try:
        import logging_loki
    except ImportError:
        return handlers, False

    loki_url = os.getenv("LOKI_URL", "http://localhost:3100/loki/api/v1/push").strip()
    if not loki_url:
        return handlers, False

    handlers.append(
        logging_loki.LokiHandler(
            url=loki_url,
            tags={
                "application": os.getenv("LOKI_APPLICATION", "onesid-apex"),
                "service": service,
                "environment": os.getenv("APP_ENV", "local"),
            },
            version="1",
        )
    )
    return handlers, True
