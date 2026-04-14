import logging
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

    handlers.append(
        logging_loki.LokiQueueHandler(
            url="http://localhost:3100/loki/api/v1/push",
            tags={"application": "onesid-apex", "service": service},
            version="1",
        )
    )
    return handlers, True
