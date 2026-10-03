import logging
from collections.abc import Iterable
from typing import Any

MASK = "***"
_MIN_SECRET_LENGTH = 4


def redact(text: str, secrets: Iterable[str]) -> str:
    for secret in sorted(_usable(secrets), key=len, reverse=True):
        text = text.replace(secret, MASK)
    return text


def install_secret_redaction(secrets: Iterable[str]) -> None:
    """Mask secrets in every log record, whatever logger or handler emits it.

    Hooking the record factory rather than a handler or logger filter matters:
    logger filters skip records propagated from child loggers, and handler
    filters miss handlers added later, for instance by third-party libraries.
    """
    values = tuple(sorted(_usable(secrets), key=len, reverse=True))
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        record.msg = redact(record.getMessage(), values)
        record.args = None
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info), values)
        if record.stack_info:
            record.stack_info = redact(record.stack_info, values)
        return record

    logging.setLogRecordFactory(factory)


def _usable(secrets: Iterable[str]) -> set[str]:
    return {secret for secret in secrets if len(secret) >= _MIN_SECRET_LENGTH}
